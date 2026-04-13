# -*- coding: utf-8 -*-
import tempfile
import unittest
from types import SimpleNamespace

from limem.core.event import Event
from limem.evolution.recall_pipeline import RecallPipeline
from limem.evolution.recall_pipeline import CandidateSet, RecallCandidate
from limem.evolution.relation_processor import OperationDecision, RelationProcessor


class _RecallStore:
    def __init__(self, recent_events, entity_events, semantic_events, entity_map):
        self._recent_events = list(recent_events)
        self._entity_events = list(entity_events)
        self._semantic_events = list(semantic_events)
        self._entity_map = dict(entity_map)
        self.embedding_client = None

    def get_recent_events(self, current_time, window_seconds, limit=100):
        del current_time, window_seconds, limit
        return list(self._recent_events)

    def get_events_by_entities(self, entities):
        del entities
        return list(self._entity_events)

    def get_active_events_with_embeddings(self, limit=200):
        del limit
        return list(self._semantic_events)

    def get_event_entities(self, event_id):
        return list(self._entity_map.get(event_id, []))

    def find_events_by_state_key(self, entity, attribute, limit=20):
        del entity, attribute, limit
        return []

    def find_events_by_thread(self, thread_id, limit=20):
        del thread_id, limit
        return []


class _RelationStore:
    def __init__(self, events):
        self.events = {event.id: event for event in events}
        self.saved_events = []
        self.archived_events = []
        self.relinked = []
        self.event_relations = []
        self.merge_traces = []

    def get_event(self, event_id):
        return self.events.get(event_id)

    def save_event(self, event):
        self.events[event.id] = event
        self.saved_events.append(event)

    def update_event(self, event):
        self.events[event.id] = event

    def archive_event(self, event_id, archived_at):
        event = self.events[event_id]
        event.status = "archived"
        event.valid_to = archived_at
        event.updated_at = archived_at
        self.archived_events.append((event_id, archived_at))

    def relink_event_references(self, source_event_id, target_event_id, timestamp):
        self.relinked.append((source_event_id, target_event_id, timestamp))
        return {"event_relations": 0}

    def upsert_event_relation(self, **kwargs):
        self.event_relations.append(dict(kwargs))
        return True

    def get_event_contexts(self, event_id):
        del event_id
        return []

    def get_event_entities(self, event_id):
        del event_id
        return []

    def save_event_merge_trace(
        self,
        source_event_id,
        target_event_id,
        merge_reason,
        similarity_score,
        merged_at,
        strategy_version,
    ):
        self.merge_traces.append(
            {
                "source_event_id": source_event_id,
                "target_event_id": target_event_id,
                "merge_reason": merge_reason,
                "similarity_score": similarity_score,
                "merged_at": merged_at,
                "strategy_version": strategy_version,
            }
        )


class _NoopLLM:
    generation_model = "test-model"

    @staticmethod
    def build_messages(system_prompt, user_message):
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    def call_generation(self, model, messages):
        del model, messages
        raise RuntimeError("LLM should not be called in this test")

    @staticmethod
    def message_content(response):
        del response
        return "{}"


def _make_config(**overrides):
    defaults = dict(
        recall_max_candidates=10,
        recall_min_aggregate_score=0.12,
        recall_temporal_window=100,
        recall_temporal_limit=5,
        recall_entity_limit=8,
        recall_semantic_limit=8,
        recall_semantic_threshold=0.78,
        recall_state_limit=5,
        recall_reference_limit=5,
        recall_weight_temporal=0.10,
        recall_weight_entity=0.30,
        recall_weight_semantic=0.35,
        recall_weight_state=0.15,
        recall_weight_reference=0.10,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_processor_config(**overrides):
    defaults = dict(
        llm_model="test-model",
        relation_classification_batch_size=15,
        relation_min_confidence=0.75,
        relation_max_links_per_event=3,
        enable_derive_operation=False,
        max_derivations_per_batch=3,
        event_merge_trace_strategy_version="v1",
        event_merge_trace_log_path=tempfile.mktemp(),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestRelationPipeline(unittest.TestCase):
    def test_recall_pipeline_merges_channels(self):
        combo = Event(
            id="evt_combo",
            summary="组合候选",
            timestamp=120,
            last_active=120,
            embedding=[1.0, 0.0],
        )
        temporal_only = Event(
            id="evt_temporal",
            summary="时间候选",
            timestamp=119,
            last_active=119,
            embedding=[0.2, 0.8],
        )
        entity_only = Event(
            id="evt_entity",
            summary="实体候选",
            timestamp=118,
            last_active=118,
            embedding=[0.5, 0.5],
        )
        semantic_only = Event(
            id="evt_semantic",
            summary="语义候选",
            timestamp=117,
            last_active=117,
            embedding=[0.98, 0.02],
        )
        new_event = Event(
            id="evt_new",
            summary="新事件",
            timestamp=122,
            last_active=122,
            embedding=[1.0, 0.0],
            payload={"session_id": "sess_1"},
        )

        store = _RecallStore(
            recent_events=[combo, temporal_only],
            entity_events=[combo, entity_only],
            semantic_events=[combo, semantic_only],
            entity_map={
                "evt_new": ["导航"],
                "evt_combo": ["导航"],
                "evt_entity": ["导航"],
            },
        )
        config = _make_config(
            recall_semantic_threshold=0.65,
            recall_min_aggregate_score=0.0,
        )
        pipeline = RecallPipeline(store=store, config=config)

        result = pipeline.recall(new_event)
        ids = [candidate.event.id for candidate in result.candidates]

        self.assertEqual(ids.count("evt_combo"), 1)
        combo_candidate = next(candidate for candidate in result.candidates if candidate.event.id == "evt_combo")
        self.assertEqual(set(combo_candidate.features["channels"].keys()), {"temporal", "entity", "semantic"})
        self.assertGreater(combo_candidate.features["aggregate_score"], 0.7)

    def test_recall_aggregate_score_floor_filters_weak_candidates(self):
        """Candidates below min_aggregate_score should be filtered out."""
        weak_event = Event(
            id="evt_weak",
            summary="弱候选",
            timestamp=50,
            last_active=50,
            embedding=[0.1, 0.9],
        )
        new_event = Event(
            id="evt_new",
            summary="新事件",
            timestamp=122,
            last_active=122,
            embedding=[1.0, 0.0],
        )

        store = _RecallStore(
            recent_events=[weak_event],
            entity_events=[],
            semantic_events=[],
            entity_map={},
        )
        config = _make_config(
            recall_min_aggregate_score=0.25,
            recall_temporal_window=1000,
        )
        pipeline = RecallPipeline(store=store, config=config)

        result = pipeline.recall(new_event)
        ids = [c.event.id for c in result.candidates]
        self.assertNotIn("evt_weak", ids)

    def test_relation_processor_legacy_compat_creates_link_edge(self):
        new_event = Event(id="evt_new", summary="用户发起导航", timestamp=100, last_active=100)
        candidate_event = Event(id="evt_old", summary="系统开始规划路线", timestamp=101, last_active=101)
        store = _RelationStore([new_event, candidate_event])
        processor = RelationProcessor(
            store=store,
            llm_client=_NoopLLM(),
            config=_make_processor_config(),
        )
        processor._legacy_relation_enabled = lambda: True
        processor._legacy_relation_payload = lambda left, right, source_text: {
            "left": left.id,
            "right": right.id,
            "source_text": source_text,
        }
        processor._legacy_relation_call = lambda payload: {
            "should_link": True,
            "relation_type": "促成",
            "from_id": payload["left"],
            "to_id": payload["right"],
            "reason": "用户请求触发系统规划路线",
            "confidence": 0.9,
        }
        candidate_set = CandidateSet(
            candidates=[
                RecallCandidate(
                    event=candidate_event,
                    channel="semantic",
                    channel_score=0.91,
                    features={"aggregate_score": 0.91, "channels": {"semantic": 0.91}},
                )
            ],
            channel_stats={"semantic": 1},
        )

        result = processor.process(new_event, candidate_set, "用户说导航去公司，系统开始规划路线。")

        self.assertEqual(result.links, 1)
        self.assertEqual(result.total_links, 1)
        self.assertEqual(store.event_relations[0]["relation_type"], "促成")
        self.assertEqual(store.event_relations[0]["operation"], "link")

    def test_relation_processor_update_creates_version_and_archives_old_event(self):
        old_event = Event(
            id="evt_old",
            summary="用户账号余额为100元",
            action="记录余额",
            timestamp=100,
            last_active=100,
            payload={"state_changes": [{"entity": "账户", "attribute": "余额", "value_after": "100"}]},
        )
        new_event = Event(
            id="evt_new",
            summary="用户账号余额更新为120元",
            action="更新余额",
            timestamp=110,
            last_active=110,
            payload={"state_changes": [{"entity": "账户", "attribute": "余额", "value_before": "100", "value_after": "120"}]},
        )
        store = _RelationStore([old_event, new_event])
        processor = RelationProcessor(
            store=store,
            llm_client=_NoopLLM(),
            config=_make_processor_config(),
        )
        processor._classify_batch = lambda e_new, candidates, source_text: [
            OperationDecision(
                candidate=candidates.candidates[0],
                operation="update",
                confidence=0.95,
                reason="新事件更新了旧余额值",
                value_before="100",
                value_after="120",
            )
        ]
        processor._fuse_event = lambda mode, old_event, new_event: {
            "summary": "用户账号余额更新为120元",
            "action": "更新余额",
            "causality": "余额从100元变为120元",
        }
        candidate_set = CandidateSet(
            candidates=[
                RecallCandidate(
                    event=old_event,
                    channel="state",
                    channel_score=1.0,
                    features={"aggregate_score": 1.0},
                )
            ],
            channel_stats={"state": 1},
        )

        result = processor.process(new_event, candidate_set, "账户余额从100元更新到120元。")

        self.assertEqual(result.updates, 1)
        self.assertEqual(result.total_links, 2)
        self.assertEqual(store.events["evt_old"].status, "archived")
        self.assertEqual(len(store.saved_events), 1)
        version_event = store.saved_events[0]
        self.assertEqual(version_event.payload["parent_event_id"], "evt_old")
        self.assertIn("evt_new", version_event.payload["version_source"])

    def test_link_budget_limits_link_operations(self):
        """Link operations should be capped at relation_max_links_per_event."""
        new_event = Event(id="evt_new", summary="新事件", timestamp=100, last_active=100)
        candidates = []
        for i in range(5):
            candidates.append(Event(
                id=f"evt_c{i}",
                summary=f"候选事件{i}",
                timestamp=90 + i,
                last_active=90 + i,
            ))
        store = _RelationStore([new_event] + candidates)
        processor = RelationProcessor(
            store=store,
            llm_client=_NoopLLM(),
            config=_make_processor_config(relation_max_links_per_event=2),
        )
        recall_candidates = [
            RecallCandidate(
                event=c,
                channel="semantic",
                channel_score=0.9,
                features={"aggregate_score": 0.9, "channels": {"semantic": 0.9, "entity": 0.8}},
            )
            for c in candidates
        ]
        processor._classify_batch = lambda e_new, candidates, source_text: [
            OperationDecision(
                candidate=rc,
                operation="link",
                confidence=0.85,
                reason="因果关系",
                link_subtype="因果",
                direction="new_to_candidate",
            )
            for rc in candidates.candidates
        ]
        candidate_set = CandidateSet(
            candidates=recall_candidates,
            channel_stats={"semantic": 5},
        )

        result = processor.process(new_event, candidate_set, "测试源文本")

        self.assertEqual(result.links, 2)
        self.assertGreaterEqual(result.skipped, 3)


if __name__ == "__main__":
    unittest.main()
