# -*- coding: utf-8 -*-
import types
import unittest

from limem.builder.extractor import ExtractionResult
from limem.builder.memory_builder import BuilderConfig, MemoryBuilder
from limem.core.episode import Episode

import limem.builder.memory_builder as memory_builder_module


class _FakeExtractor:
    def extract(self, text, metadata=None):
        del text, metadata
        events = [
            {
                "summary": "用户发起导航",
                "action": "发起导航",
                "causality": "",
                "participants": [{"role": "用户", "seat": ""}],
                "time_range": {"start": 100, "end": 100, "display_time_bucket": ""},
            },
            {
                "summary": "系统开始规划路线",
                "action": "规划路线",
                "causality": "响应导航请求",
                "participants": [{"role": "系统", "seat": ""}],
                "time_range": {"start": 101, "end": 101, "display_time_bucket": ""},
            },
        ]
        return ExtractionResult(
            event_data=events[0],
            events_data=events,
            entities=[],
            confidence=1.0,
        )


class _FakeConsolidator:
    pass


class _NoopRelationshipInferrer:
    def infer(self, events):
        del events
        return []


class _BatchAwareStore:
    def __init__(self):
        self.events = {}
        self.episodes = {}
        self.save_event_calls = 0
        self.save_events_batch_calls = 0
        self.link_event_to_episode_calls = 0
        self.link_events_to_episode_batch_calls = 0

    def save_episode(self, episode):
        self.episodes[episode.id] = episode

    def save_event(self, event):
        self.save_event_calls += 1
        self.events[event.id] = event

    def save_events_batch(self, events):
        self.save_events_batch_calls += 1
        for event in events:
            self.events[event.id] = event

    def get_event(self, event_id):
        return self.events.get(event_id)

    def update_event(self, event):
        self.events[event.id] = event

    def link_event_to_episode(self, event_id, episode_id):
        self.link_event_to_episode_calls += 1
        self.episodes.setdefault(episode_id, None)

    def link_events_to_episode_batch(self, event_ids, episode_id):
        self.link_events_to_episode_batch_calls += 1
        self.episodes.setdefault(episode_id, None)
        self.last_linked_event_ids = list(event_ids)

    def upsert_event_relation(self, *args, **kwargs):
        del args, kwargs


class _FakeDynamicEngine:
    def __init__(self):
        self.calls = 0

    def evolve_existing_events(self, events):
        self.calls += 1
        return {
            "context_links": 0,
            "next_links": 0,
            "event_relation_links": len(events),
        }


class _FakeTextEmbedding:
    calls = []

    @classmethod
    def call(cls, model, input):
        del model
        items = list(input) if isinstance(input, list) else [input]
        cls.calls.append(items)
        return types.SimpleNamespace(
            output={
                "embeddings": [
                    {
                        "text_index": idx,
                        "embedding": [float(idx), float(len(text))],
                    }
                    for idx, text in enumerate(items)
                ]
            }
        )


class TestMemoryBuilderPerformance(unittest.TestCase):
    def test_get_embeddings_batches_dashscope_calls(self):
        builder = MemoryBuilder(
            extractor=_FakeExtractor(),
            consolidator=_FakeConsolidator(),
            store=_BatchAwareStore(),
            config=BuilderConfig(llm_concurrency=4),
            relationship_inferrer=_NoopRelationshipInferrer(),
        )
        texts = ["x" * (idx + 1) for idx in range(30)]

        original_text_embedding = memory_builder_module.TextEmbedding
        memory_builder_module.TextEmbedding = _FakeTextEmbedding
        _FakeTextEmbedding.calls = []
        try:
            embeddings = builder._get_embeddings(texts)
        finally:
            memory_builder_module.TextEmbedding = original_text_embedding

        self.assertEqual(len(_FakeTextEmbedding.calls), 2)
        self.assertEqual(len(_FakeTextEmbedding.calls[0]), 25)
        self.assertEqual(len(_FakeTextEmbedding.calls[1]), 5)
        self.assertEqual(len(embeddings), 30)
        self.assertEqual(embeddings[0][1], 1.0)
        self.assertEqual(embeddings[24][1], 25.0)
        self.assertEqual(embeddings[29][1], 30.0)

    def test_build_uses_batch_store_methods_for_append_first(self):
        store = _BatchAwareStore()
        builder = MemoryBuilder(
            extractor=_FakeExtractor(),
            consolidator=_FakeConsolidator(),
            store=store,
            config=BuilderConfig(append_first_mode=True),
            relationship_inferrer=_NoopRelationshipInferrer(),
        )
        builder._get_embeddings = lambda texts: [[0.0] * 1536 for _ in texts]

        result = builder.build(Episode(content="用户导航去公司，系统开始规划路线", timestamp=123))

        self.assertEqual(len(result.events), 2)
        self.assertEqual(store.save_events_batch_calls, 1)
        self.assertEqual(store.save_event_calls, 0)
        self.assertEqual(store.link_events_to_episode_batch_calls, 1)
        self.assertEqual(store.link_event_to_episode_calls, 0)
        self.assertEqual(result.metrics["event_count"], 2)

    def test_build_can_defer_dynamic_evolution(self):
        store = _BatchAwareStore()
        dynamic_engine = _FakeDynamicEngine()
        builder = MemoryBuilder(
            extractor=_FakeExtractor(),
            consolidator=_FakeConsolidator(),
            store=store,
            config=BuilderConfig(append_first_mode=True, deferred_evolution=True),
            dynamic_engine=dynamic_engine,
            relationship_inferrer=_NoopRelationshipInferrer(),
        )
        builder._get_embeddings = lambda texts: [[0.0] * 1536 for _ in texts]

        result = builder.build(Episode(content="用户导航去公司，系统开始规划路线", timestamp=456))

        self.assertEqual(len(result.events), 2)
        self.assertEqual(dynamic_engine.calls, 0)
        self.assertTrue(result.metrics["deferred_evolution"])


if __name__ == "__main__":
    unittest.main()
