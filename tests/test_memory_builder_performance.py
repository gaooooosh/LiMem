# -*- coding: utf-8 -*-
import types
import unittest
from unittest.mock import MagicMock

from limem.builder.extractor import ExtractionResult
from limem.builder.memory_builder import BuilderConfig, MemoryBuilder
from limem.core.episode import Episode
from limem.llm import DashScopeClient

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
                "contexts": [
                    {"subtype": "situation", "summary": "前往目的地"},
                    {"subtype": "situation", "summary": "正在出行"},
                ],
            },
            {
                "summary": "系统开始规划路线",
                "action": "规划路线",
                "causality": "响应导航请求",
                "participants": [{"role": "系统", "seat": ""}],
                "time_range": {"start": 101, "end": 101, "display_time_bucket": ""},
                "contexts": [
                    {"subtype": "environment", "summary": "车机导航场景"},
                ],
            },
        ]
        return ExtractionResult(
            event_data=events[0],
            events_data=events,
            entities=[],
            confidence=1.0,
            orphan_contexts=[
                {
                    "subtype": "situation",
                    "summary": "路况未知",
                    "evidence_span": "detail",
                    "confidence": 0.9,
                }
            ],
        )


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


class TestMemoryBuilderPerformance(unittest.TestCase):
    def test_get_embeddings_batches_dashscope_calls(self):
        embedding_calls: list[list[str]] = []

        def fake_embeddings_create(**kwargs):
            items = kwargs.get("input", [])
            if isinstance(items, str):
                items = [items]
            embedding_calls.append(list(items))
            return types.SimpleNamespace(
                data=[
                    types.SimpleNamespace(
                        index=idx,
                        embedding=[float(idx), float(len(text))],
                    )
                    for idx, text in enumerate(items)
                ]
            )

        client = DashScopeClient(
            api_key="test-key",
            base_url="http://test.local",
            embedding_model="test-model",
        )
        mock_openai = MagicMock()
        mock_openai.embeddings.create.side_effect = fake_embeddings_create
        client._openai_client = mock_openai

        builder = MemoryBuilder(
            extractor=_FakeExtractor(),
            store=_BatchAwareStore(),
            config=BuilderConfig(llm_concurrency=4),
            llm_client=client,
        )
        texts = ["x" * (idx + 1) for idx in range(30)]

        embeddings = builder._get_embeddings(texts)

        self.assertEqual(len(embedding_calls), 2)
        self.assertEqual(len(embedding_calls[0]), 25)
        self.assertEqual(len(embedding_calls[1]), 5)
        self.assertEqual(len(embeddings), 30)
        self.assertEqual(embeddings[0][1], 1.0)
        self.assertEqual(embeddings[24][1], 25.0)
        self.assertEqual(embeddings[29][1], 30.0)

    def test_build_uses_batch_store_methods_for_append_first(self):
        store = _BatchAwareStore()
        builder = MemoryBuilder(
            extractor=_FakeExtractor(),
            store=store,
            config=BuilderConfig(append_first_mode=True),
        )
        builder._get_embeddings = lambda texts: [[0.0] * 1536 for _ in texts]

        result = builder.build(Episode(content="用户导航去公司，系统开始规划路线", timestamp=123))

        self.assertEqual(len(result.events), 2)
        self.assertEqual(store.save_events_batch_calls, 1)
        self.assertEqual(store.save_event_calls, 0)
        self.assertEqual(store.link_events_to_episode_batch_calls, 1)
        self.assertEqual(store.link_event_to_episode_calls, 0)
        self.assertEqual(result.metrics["event_count"], 2)
        self.assertEqual(result.metrics["raw_event_count"], 2)
        self.assertEqual(result.metrics["subject_event_count"], 2)
        self.assertEqual(result.metrics["inline_context_count"], 3)
        self.assertEqual(result.metrics["orphan_context_count"], 1)
        self.assertEqual(result.metrics["episodes_with_orphan_contexts"], 1)
        self.assertEqual(result.metrics["eventless_orphan_episode_count"], 0)
        self.assertEqual(result.metrics["orphan_contexts"][0]["summary"], "路况未知")

    def test_build_can_defer_dynamic_evolution(self):
        store = _BatchAwareStore()
        dynamic_engine = _FakeDynamicEngine()
        builder = MemoryBuilder(
            extractor=_FakeExtractor(),
            store=store,
            config=BuilderConfig(append_first_mode=True, deferred_evolution=True),
            dynamic_engine=dynamic_engine,
        )
        builder._get_embeddings = lambda texts: [[0.0] * 1536 for _ in texts]

        result = builder.build(Episode(content="用户导航去公司，系统开始规划路线", timestamp=456))

        self.assertEqual(len(result.events), 2)
        self.assertEqual(dynamic_engine.calls, 0)
        self.assertTrue(result.metrics["deferred_evolution"])

    def test_build_preserves_orphan_context_metrics_for_eventless_episode(self):
        class _OrphanOnlyExtractor:
            def extract(self, text, metadata=None):
                del text, metadata
                return ExtractionResult(
                    event_data={},
                    events_data=[],
                    entities=[],
                    orphan_contexts=[
                        {"subtype": "situation", "summary": "低电量", "evidence_span": "电量12%"},
                        {"subtype": "environment", "summary": "车内安静", "evidence_span": "噪音34dB"},
                    ],
                )

        builder = MemoryBuilder(
            extractor=_OrphanOnlyExtractor(),
            store=_BatchAwareStore(),
            config=BuilderConfig(),
        )

        result = builder.build(Episode(content="环境数据", timestamp=789))

        self.assertEqual(result.event.status, "ignored")
        self.assertEqual(result.metrics["event_count"], 0)
        self.assertEqual(result.metrics["raw_event_count"], 0)
        self.assertEqual(result.metrics["orphan_context_count"], 2)
        self.assertEqual(result.metrics["episodes_with_orphan_contexts"], 1)
        self.assertEqual(result.metrics["eventless_orphan_episode_count"], 1)
        self.assertEqual(result.metrics["orphan_contexts"][0]["summary"], "低电量")

    def test_build_only_persists_events_with_summary_after_frame_build(self):
        class _MixedExtractor:
            def extract(self, text, metadata=None):
                del text, metadata
                return ExtractionResult(
                    event_data={
                        "summary": "系统开启沉浸会议模式",
                        "participants": [{"role": "车机系统", "seat": ""}],
                        "action": "开启沉浸会议模式",
                        "causality": "检测到会议即将开始",
                    },
                    events_data=[
                        {
                            "summary": "系统开启沉浸会议模式",
                            "participants": [{"role": "车机系统", "seat": ""}],
                            "action": "开启沉浸会议模式",
                            "causality": "检测到会议即将开始",
                        },
                        {
                            "summary": "",
                            "participants": [{"role": "车机系统", "seat": ""}],
                            "action": "自动暂停当前媒体",
                            "causality": "保持座舱安静",
                        },
                    ],
                    entities=[],
                )

        builder = MemoryBuilder(
            extractor=_MixedExtractor(),
            store=_BatchAwareStore(),
            config=BuilderConfig(append_first_mode=True),
        )
        builder._get_embeddings = lambda texts: [[0.0] * 1536 for _ in texts]

        result = builder.build(Episode(content="会议即将开始，系统开启沉浸会议模式", timestamp=999))

        self.assertEqual(result.metrics["raw_event_count"], 2)
        self.assertEqual(result.metrics["event_count"], 1)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0].summary, "系统开启沉浸会议模式")


if __name__ == "__main__":
    unittest.main()
