# -*- coding: utf-8 -*-
import os
import tempfile
import time
import json
import unittest

from limem import create_ltm, Episode
from limem.core.context import Context, ContextDraft
from limem.core.event import Event
from limem.evolution import DynamicEvolutionConfig, DynamicEvolutionEngine


class TestDynamicEvolution(unittest.TestCase):
    def test_bulk_ingest_mode_skips_auto_consolidation_for_new_and_existing_events(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_bulk_ingest_no_auto_consolidation.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": True,
                    "bulk_ingest_mode": True,
                    "generate_answer": False,
                },
            )

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._resolve_context_pairs_for_event_batch = lambda events, record=None: [[] for _ in events]
            engine.extract_event_event_relations = lambda events, record=None: 0
            engine.run_consolidation = lambda *args, **kwargs: self.fail(
                "bulk ingest mode should skip auto consolidation"
            )

            existing_event = Event(
                summary="用户开启勿扰模式",
                action="开启勿扰",
                timestamp=1773326409,
                last_active=1773326409,
            )
            existing_report = engine.evolve_existing_events([existing_event])
            self.assertEqual(existing_report["context_links"], 0)
            self.assertEqual(existing_report["event_relation_links"], 0)

            new_event = Event(
                summary="系统开始播放通勤歌单",
                action="播放歌单",
                timestamp=1773326410,
                last_active=1773326410,
            )
            write_report = engine.write_event_batch([new_event], record=None)
            self.assertEqual(write_report["event_count"], 1)
            self.assertEqual(write_report["context_links"], 0)

    def test_append_first_and_dynamic_edges(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_dynamic.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "generate_answer": False,
                },
            )

            ltm.ingest(Episode(content="用户说: 我要开会，开勿扰", timestamp=1773326409))
            ltm.ingest(Episode(content="用户说: 导航去公司", timestamp=1773326500))

            stats = ltm.get_stats()
            self.assertGreaterEqual(stats.get("event_count", 0), 2)
            self.assertGreaterEqual(stats.get("context_count", 0), 1)
            self.assertGreaterEqual(stats.get("abstract_to_count", 0), 1)


    def test_run_consolidation_respects_llm_gate_for_events_and_contexts(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_dynamic_llm_gate.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": False,
                    "generate_answer": False,
                },
            )

            ts = 1773326409
            ltm.write(
                {
                    "id": "evt_a",
                    "summary": "用户在会议场景开启勿扰模式",
                    "event_type": "meeting_mode",
                    "action": "开启勿扰",
                    "timestamp": ts,
                    "last_active": ts,
                    "participants": [{"role": "用户"}],
                    "location": {"geo_context": "车内", "digital_context": "会议模式"},
                },
                kind="event",
                evolve=False,
            )
            ltm.write(
                {
                    "id": "evt_b",
                    "summary": "用户在会议场景开启勿扰模式",
                    "event_type": "meeting_mode",
                    "action": "开启勿扰",
                    "timestamp": ts + 20,
                    "last_active": ts + 20,
                    "participants": [{"role": "用户"}],
                    "location": {"geo_context": "车内", "digital_context": "会议模式"},
                },
                kind="event",
                evolve=False,
            )

            first_context = ltm.write(
                {
                    "id": "ctx_a",
                    "summary": "context:会议场景 / 车内 / 会议模式",
                    "subtype": "会议场景",
                    "description": "用户处于车内会议场景，数字环境为会议模式，需要安静与低干扰",
                },
                kind="context",
            )["item"]
            second_context = ltm.write(
                {
                    "id": "ctx_b",
                    "summary": "context:会议场景 / 车内 / 会议模式 / 早高峰",
                    "subtype": "会议场景",
                    "description": "用户处于车内会议场景，数字环境为会议模式，时间特征为早高峰时段",
                },
                kind="context",
            )["item"]

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._llm_merge_available = lambda: True
            engine._ensure_event_embedding = lambda event: {
                "evt_nav_a": [1.0, 0.0, 0.0],
                "evt_nav_b": [0.99, 0.01, 0.0],
            }.get(event.id, [0.0, 1.0, 0.0])

            def fake_merge_call(payload):
                task = str(payload.get("task", ""))
                if "memory events" in task:
                    return {
                        "should_merge": False,
                        "canonical_id": "evt_a",
                        "reason": "different_intents",
                        "confidence": 0.41,
                    }
                if "context nodes" in task:
                    return {
                        "should_merge": True,
                        "canonical_id": first_context["id"],
                        "reason": "same_situation_refinement",
                        "confidence": 0.93,
                    }
                return None

            engine._call_merge_llm = fake_merge_call

            report = engine.run_consolidation(current_time=ts + 30, strategy="llm")
            self.assertGreaterEqual(report["candidate_pairs"], 1)
            self.assertEqual(report["merged_events"], 0)
            self.assertEqual(report["merged_contexts"], 1)
            self.assertEqual(ltm.get_event("evt_b").status, "active")
            self.assertEqual(ltm.store.get_context(second_context["id"]).status, "merged")
            self.assertEqual(ltm.store.get_context(first_context["id"]).status, "active")

    def test_event_relations_are_extracted_via_llm_with_session_scope(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_event_relations.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": False,
                    "enable_event_relations": True,
                    "generate_answer": False,
                },
            )

            ts = 1773326409
            ltm.write(
                {
                    "id": "evt_rel_a",
                    "summary": "用户准备导航去公司",
                    "event_type": "navigation",
                    "action": "准备导航",
                    "timestamp": ts,
                    "last_active": ts,
                    "participants": [{"role": "用户"}],
                    "location": {"geo_context": "车内"},
                    "payload": {"session_id": "sess_1"},
                },
                kind="event",
                evolve=False,
            )
            ltm.write(
                {
                    "id": "evt_rel_b",
                    "summary": "系统开始规划去公司的路线",
                    "event_type": "navigation",
                    "action": "规划路线",
                    "timestamp": ts + 10,
                    "last_active": ts + 10,
                    "participants": [{"role": "用户"}],
                    "location": {"geo_context": "车内"},
                    "payload": {"session_id": "sess_1"},
                },
                kind="event",
                evolve=False,
            )

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._llm_relation_available = lambda: True
            engine._call_relation_llm = lambda payload: {
                "should_link": True,
                "relation_type": "促成",
                "from_id": "evt_rel_a",
                "to_id": "evt_rel_b",
                "reason": "用户发起导航请求，促成系统开始规划路线",
                "confidence": 0.91,
            }

            engine.evolve_existing_events(
                [
                    ltm.get_event("evt_rel_a"),
                    ltm.get_event("evt_rel_b"),
                ]
            )

            snapshot = ltm.snapshot(limit=20, include_inactive=True)
            self.assertEqual(snapshot["stats"].get("event_relation_count", 0), 1)
            self.assertEqual(snapshot["edges"]["next"], [])
            self.assertTrue(
                any(
                    edge["from_event_id"] == "evt_rel_a"
                    and edge["to_event_id"] == "evt_rel_b"
                    and edge["relation_type"] == "促成"
                    and edge["description"] == "用户发起导航请求，促成系统开始规划路线"
                    for edge in snapshot["edges"]["event_event"]
                )
            )

    def test_relation_prompt_payload_is_fully_localized_to_chinese(self):
        class _Store:
            @staticmethod
            def get_event_contexts(event_id):
                del event_id
                return []

        engine = DynamicEvolutionEngine(
            store=_Store(),
            config=DynamicEvolutionConfig(),
        )
        left = Event(id="evt_left", summary="用户说要去公司", action="发起导航请求")
        right = Event(id="evt_right", summary="系统开始规划路线", action="规划路线")

        payload = engine._relation_prompt_payload(
            left=left,
            right=right,
            source_text="用户说导航去公司，系统开始规划路线。",
        )

        self.assertIn("判断是否需要创建事件-事件关系边", payload["task"])
        self.assertIn("relation_type 只能使用：因果、时序相邻、前置条件、促成、后续。", payload["rules"])
        self.assertEqual(payload["output_schema"]["relation_type"], "因果")
        self.assertEqual(payload["output_schema"]["reason"], "两个事件之间的详细关系说明")

    def test_auto_merge_events_uses_embedding_candidates_and_merges_fields(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_event_embedding_merge.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": False,
                    "generate_answer": False,
                },
            )

            ts = int(time.time())
            ltm.write(
                {
                    "id": "evt_merge_a",
                    "summary": "用户在会议中开启勿扰",
                    "action": "开启勿扰",
                    "timestamp": ts,
                    "last_active": ts,
                    "participants": [{"role": "用户"}],
                    "time_range": {"display_time_bucket": "morning"},
                    "payload": {"scene": "meeting"},
                },
                kind="event",
                evolve=False,
            )
            ltm.write(
                {
                    "id": "evt_merge_b",
                    "summary": "会议里打开勿扰模式",
                    "action": "",
                    "causality": "防打扰",
                    "timestamp": ts + 10,
                    "last_active": ts + 10,
                    "participants": [{"role": "系统"}],
                    "time_range": {"display_time_bucket": "work"},
                    "payload": {"device": "car"},
                },
                kind="event",
                evolve=False,
            )
            ltm.write(
                {
                    "id": "evt_far",
                    "summary": "用户规划周末观影",
                    "timestamp": ts + 20,
                    "last_active": ts + 20,
                },
                kind="event",
                evolve=False,
            )

            context = ltm.write(
                {
                    "id": "ctx_merge_b",
                    "summary": "context:会议模式",
                    "subtype": "会议场景",
                    "description": "用户处于会议相关场景，需要低干扰环境",
                },
                kind="context",
            )["item"]
            ltm.store.link_event_to_context(
                event_id="evt_merge_b",
                context_id=context["id"],
                confidence=0.9,
                weight=1.0,
                original_signal="unit_test",
                evidence_span="meeting",
                timestamp=ts + 11,
            )

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            embedding_map = {
                "evt_merge_a": [1.0, 0.0, 0.0],
                "evt_merge_b": [0.999, 0.001, 0.0],
                "evt_far": [0.0, 1.0, 0.0],
            }
            engine._ensure_event_embedding = lambda event: embedding_map.get(event.id)
            engine._llm_merge_available = lambda: True
            def fake_merge_call(payload):
                left_id = str(payload.get("left", {}).get("id", "") or "")
                right_id = str(payload.get("right", {}).get("id", "") or "")
                pair = {left_id, right_id}
                if pair == {"evt_merge_a", "evt_merge_b"}:
                    return {
                        "should_merge": True,
                        "canonical_id": "evt_merge_a",
                        "reason": "same_atomic_change_unit",
                        "confidence": 0.95,
                    }
                return {
                    "should_merge": False,
                    "canonical_id": left_id,
                    "reason": "different_memory_units",
                    "confidence": 0.2,
                }
            engine._call_merge_llm = fake_merge_call

            report = ltm.auto_merge(
                scope="event",
                strategy="llm",
                dry_run=False,
                max_pairs=5,
            )
            self.assertEqual(report["merged_events"], 1)
            self.assertGreaterEqual(report["event_candidates"], 1)
            self.assertTrue(report["event_plans"])
            reason_payload = json.loads(report["event_plans"][0]["reason"])
            self.assertEqual(reason_payload.get("source"), "embedding_preselect+llm_judge")
            self.assertIn("embedding_similarity", reason_payload)
            self.assertGreater(float(report["event_plans"][0].get("embedding_similarity", 0.0)), 0.9)

            canonical = ltm.get_event("evt_merge_a")
            merged = ltm.get_event("evt_merge_b")
            self.assertIsNotNone(canonical)
            self.assertIsNotNone(merged)
            self.assertEqual(merged.status, "merged")
            self.assertIn("开启勿扰", canonical.summary)
            self.assertNotIn("；", canonical.summary)
            self.assertIn("开启勿扰", canonical.action)
            self.assertEqual(canonical.causality, "防打扰")
            self.assertGreaterEqual(len(canonical.participants), 2)
            self.assertIn("scene", canonical.payload)
            self.assertIn("device", canonical.payload)

            snapshot = ltm.snapshot(limit=20, include_inactive=True)
            self.assertTrue(
                any(
                    edge["event_id"] == "evt_merge_a" and edge["context_id"] == context["id"]
                    for edge in snapshot["edges"]["event_context"]
                )
            )
            self.assertFalse(
                any(
                    edge["event_id"] == "evt_merge_b"
                    for edge in snapshot["edges"]["event_context"]
                )
            )

    def test_auto_merge_llm_payload_exposes_same_episode_atomic_aggregation_signals(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_event_atomic_aggregation.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": False,
                    "generate_answer": False,
                },
            )

            ts = int(time.time())
            episode_id = "episode_nav_1"
            episode_text = "用户发起导航，从当前位置导航到儿童医院停车场，用时20分钟"
            ltm.write(
                {
                    "id": "evt_nav_a",
                    "summary": "用户发起导航",
                    "action": "发起导航",
                    "timestamp": ts,
                    "last_active": ts,
                    "participants": [{"role": "用户"}],
                    "payload": {
                        "episode_id": episode_id,
                        "episode_text": episode_text,
                        "event_index": 0,
                    },
                },
                kind="event",
                entity_ids=["儿童医院停车场"],
                evolve=False,
            )
            ltm.write(
                {
                    "id": "evt_nav_b",
                    "summary": "导航到儿童医院停车场",
                    "action": "导航到儿童医院停车场",
                    "timestamp": ts + 1,
                    "last_active": ts + 1,
                    "participants": [{"role": "用户"}],
                    "payload": {
                        "episode_id": episode_id,
                        "episode_text": episode_text,
                        "event_index": 1,
                    },
                },
                kind="event",
                entity_ids=["儿童医院停车场"],
                evolve=False,
            )

            context = ltm.write(
                {
                    "id": "ctx_nav_main",
                    "summary": "context:出行导航场景",
                    "subtype": "出行导航",
                    "description": "用户处于出行导航场景，系统正在辅助前往目的地",
                },
                kind="context",
            )["item"]
            ltm.store.link_event_to_context(
                event_id="evt_nav_a",
                context_id=context["id"],
                confidence=0.92,
                weight=1.0,
                original_signal="unit_test",
                evidence_span=episode_text,
                timestamp=ts,
            )
            ltm.store.link_event_to_context(
                event_id="evt_nav_b",
                context_id=context["id"],
                confidence=0.92,
                weight=1.0,
                original_signal="unit_test",
                evidence_span=episode_text,
                timestamp=ts + 1,
            )

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._llm_merge_available = lambda: True
            engine._ensure_event_embedding = lambda event: {
                "evt_nav_a": [1.0, 0.0, 0.0],
                "evt_nav_b": [0.99, 0.01, 0.0],
            }.get(event.id, [0.0, 1.0, 0.0])

            captured_payloads: list[dict[str, object]] = []

            def fake_merge_call(payload):
                captured_payloads.append(payload)
                rules_text = " ".join(str(item) for item in payload.get("rules", []))
                self.assertIn("higher-level user interaction", rules_text)
                pair_features = payload.get("pair_features", {})
                self.assertTrue(pair_features.get("same_episode"))
                self.assertEqual(pair_features.get("event_index_distance"), 1)
                self.assertEqual(pair_features.get("shared_episode_id"), episode_id)
                self.assertIn(context["id"], pair_features.get("shared_context_ids", []))
                self.assertIn("儿童医院停车场", pair_features.get("shared_entity_ids", []))
                self.assertIn("用户", "".join(pair_features.get("shared_participants", [])))
                self.assertIn("儿童医院停车场", pair_features.get("shared_episode_text_excerpt", ""))
                return {
                    "should_merge": True,
                    "canonical_id": "evt_nav_a",
                    "reason": "same_navigation_main_event",
                    "confidence": 0.96,
                }

            engine._call_merge_llm = fake_merge_call

            report = ltm.auto_merge(
                scope="event",
                strategy="llm",
                dry_run=True,
                max_pairs=5,
            )
            self.assertTrue(captured_payloads)
            self.assertGreaterEqual(report["event_candidates"], 1)
            self.assertTrue(report["event_plans"])
            self.assertEqual(report["event_plans"][0]["canonical_event_id"], "evt_nav_a")
            self.assertEqual(report["event_plans"][0]["merged_event_id"], "evt_nav_b")

    def test_auto_merge_uses_graph_local_time_anchor_for_historical_events(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_event_historical_anchor.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": False,
                    "generate_answer": False,
                },
            )

            historical_ts = 1773337414
            episode_id = "episode_nav_hist"
            episode_text = "2026-03-13 晚上1点半左右,用户发起导航,从当前位置导航到儿童医院停车场,用时20分钟"
            first = ltm.write(
                {
                    "id": "evt_hist_a",
                    "summary": "用户发起导航",
                    "action": "发起导航",
                    "timestamp": historical_ts,
                    "last_active": historical_ts,
                    "participants": [{"role": "用户"}],
                    "payload": {
                        "episode_id": episode_id,
                        "episode_text": episode_text,
                        "event_index": 0,
                    },
                },
                kind="event",
                entity_ids=["儿童医院停车场"],
                evolve=False,
            )["item"]
            second = ltm.write(
                {
                    "id": "evt_hist_b",
                    "summary": "导航到儿童医院停车场",
                    "action": "导航到儿童医院停车场",
                    "timestamp": historical_ts,
                    "last_active": historical_ts,
                    "participants": [{"role": "用户"}],
                    "payload": {
                        "episode_id": episode_id,
                        "episode_text": episode_text,
                        "event_index": 1,
                    },
                },
                kind="event",
                entity_ids=["儿童医院停车场"],
                evolve=False,
            )["item"]
            context = ltm.write(
                {
                    "id": "ctx_hist_nav",
                    "summary": "context:出行导航场景",
                    "subtype": "出行导航",
                    "description": "用户处于出行导航场景，系统正在辅助前往目标地点",
                },
                kind="context",
            )["item"]
            for event_id in [first["id"], second["id"]]:
                ltm.store.link_event_to_context(
                    event_id=event_id,
                    context_id=context["id"],
                    confidence=0.9,
                    weight=1.0,
                    original_signal="unit_test",
                    evidence_span=episode_text,
                    timestamp=historical_ts,
                )

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._llm_merge_available = lambda: True
            engine._ensure_event_embedding = lambda event: {
                "evt_hist_a": [1.0, 0.0, 0.0],
                "evt_hist_b": [0.99, 0.01, 0.0],
            }.get(event.id, [0.0, 1.0, 0.0])
            engine._call_merge_llm = lambda payload: {
                "should_merge": True,
                "canonical_id": "evt_hist_a",
                "reason": "same_navigation_main_event",
                "confidence": 0.94,
            }

            report = ltm.auto_merge(
                scope="event",
                strategy="llm",
                dry_run=True,
                max_pairs=5,
            )
            self.assertGreaterEqual(report["event_candidates"], 1)
            self.assertTrue(report["event_plans"])
            self.assertEqual(report["event_plans"][0]["canonical_event_id"], "evt_hist_a")
            self.assertEqual(report["event_plans"][0]["merged_event_id"], "evt_hist_b")

    def test_auto_merge_reuses_existing_canonical_for_multi_event_aggregation(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_event_multi_plan_stability.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": False,
                    "generate_answer": False,
                },
            )

            ts = 1773337414
            episode_id = "episode_nav_cluster"
            episode_text = "用户发起导航，从当前位置导航到儿童医院停车场，用时20分钟"
            event_specs = [
                ("evt_root", "用户发起导航", "发起导航", 0),
                ("evt_target", "导航到儿童医院停车场", "导航到儿童医院停车场", 1),
                ("evt_duration", "导航耗时20分钟", "导航耗时20分钟", 2),
            ]
            for event_id, summary, action, index in event_specs:
                ltm.write(
                    {
                        "id": event_id,
                        "summary": summary,
                        "action": action,
                        "timestamp": ts,
                        "last_active": ts,
                        "participants": [{"role": "用户"}],
                        "payload": {
                            "episode_id": episode_id,
                            "episode_text": episode_text,
                            "event_index": index,
                        },
                    },
                    kind="event",
                    entity_ids=["儿童医院停车场"],
                    evolve=False,
                )

            context = ltm.write(
                {
                    "id": "ctx_nav_cluster",
                    "summary": "context:出行导航场景",
                    "subtype": "出行导航",
                    "description": "用户处于出行导航场景，系统正在辅助前往目标地点",
                },
                kind="context",
            )["item"]
            for event_id, *_rest in event_specs:
                ltm.store.link_event_to_context(
                    event_id=event_id,
                    context_id=context["id"],
                    confidence=0.9,
                    weight=1.0,
                    original_signal="unit_test",
                    evidence_span=episode_text,
                    timestamp=ts,
                )

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._llm_merge_available = lambda: True
            engine._ensure_event_embedding = lambda event: {
                "evt_root": [1.0, 0.0, 0.0],
                "evt_target": [0.99, 0.01, 0.0],
                "evt_duration": [0.98, 0.02, 0.0],
            }.get(event.id, [0.0, 1.0, 0.0])

            def fake_merge_call(payload):
                return {
                    "should_merge": True,
                    "canonical_id": str(payload.get("left", {}).get("id", "") or ""),
                    "reason": "same_navigation_main_event",
                    "confidence": 0.93,
                }

            engine._call_merge_llm = fake_merge_call

            report = ltm.auto_merge(
                scope="event",
                strategy="llm",
                dry_run=True,
                max_pairs=10,
            )
            self.assertEqual(report["event_candidates"], 2)
            self.assertTrue(report["event_plans"])
            self.assertEqual(
                {plan["canonical_event_id"] for plan in report["event_plans"]},
                {"evt_root"},
            )
            self.assertEqual(
                {plan["merged_event_id"] for plan in report["event_plans"]},
                {"evt_target", "evt_duration"},
            )

    def test_auto_merge_rewrites_canonical_summary_into_main_event_summary(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_event_main_summary_rewrite.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": False,
                    "generate_answer": False,
                },
            )

            ts = 1773337414
            episode_id = "episode_nav_summary"
            episode_text = "用户发起导航，从当前位置导航到儿童医院停车场，用时20分钟"
            specs = [
                ("evt_main_a", "用户发起导航", "发起导航", 0),
                ("evt_main_b", "用户导航到儿童医院停车场", "导航到儿童医院停车场", 1),
                ("evt_main_c", "导航耗时20分钟", "导航耗时20分钟", 2),
            ]
            for event_id, summary, action, index in specs:
                ltm.write(
                    {
                        "id": event_id,
                        "summary": summary,
                        "action": action,
                        "timestamp": ts,
                        "last_active": ts,
                        "participants": [{"role": "用户"}],
                        "payload": {
                            "episode_id": episode_id,
                            "episode_text": episode_text,
                            "event_index": index,
                        },
                    },
                    kind="event",
                    entity_ids=["儿童医院停车场"],
                    evolve=False,
                )

            context = ltm.write(
                {
                    "id": "ctx_main_nav",
                    "summary": "context:出行导航场景",
                    "subtype": "出行导航",
                    "description": "用户处于出行导航场景，系统正在辅助前往目标地点",
                },
                kind="context",
            )["item"]
            for event_id, *_rest in specs:
                ltm.store.link_event_to_context(
                    event_id=event_id,
                    context_id=context["id"],
                    confidence=0.9,
                    weight=1.0,
                    original_signal="unit_test",
                    evidence_span=episode_text,
                    timestamp=ts,
                )

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._llm_merge_available = lambda: True
            engine._ensure_event_embedding = lambda event: {
                "evt_main_a": [1.0, 0.0, 0.0],
                "evt_main_b": [0.99, 0.01, 0.0],
                "evt_main_c": [0.98, 0.02, 0.0],
            }.get(event.id, [0.0, 1.0, 0.0])
            engine._call_merge_llm = lambda payload: {
                "should_merge": True,
                "canonical_id": "evt_main_a",
                "reason": "same_navigation_main_event",
                "confidence": 0.95,
            }
            engine._llm_rewrite_merged_event = lambda canonical, merged: {
                "summary": "用户导航到儿童医院停车场，耗时20分钟",
                "action": "导航到儿童医院停车场",
                "causality": "耗时20分钟",
            }

            report = ltm.auto_merge(
                scope="event",
                strategy="llm",
                dry_run=False,
                max_pairs=10,
            )
            self.assertEqual(report["merged_events"], 2)

            canonical = ltm.get_event("evt_main_a")
            self.assertIsNotNone(canonical)
            self.assertEqual(canonical.summary, "用户导航到儿童医院停车场，耗时20分钟")
            self.assertEqual(canonical.action, "导航到儿童医院停车场")
            self.assertEqual(canonical.causality, "耗时20分钟")
            self.assertEqual(canonical.payload.get("summary"), canonical.summary)
            self.assertEqual(canonical.payload.get("action"), canonical.action)
            self.assertEqual(canonical.payload.get("causality"), canonical.causality)

    def test_auto_merge_rewrites_action_and_causality_for_climate_main_event(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_event_climate_semantics.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": False,
                    "generate_answer": False,
                },
            )

            ts = 1773337705
            episode_id = "episode_climate_semantics"
            episode_text = "用户把后排温度调到26度，车机启动安抚模式"
            specs = [
                ("evt_climate_a", "用户将后排温度调整为26度", "调整后排温度", "", 0),
                ("evt_climate_b", "车机启动安抚模式", "启动安抚模式", "", 1),
            ]
            for event_id, summary, action, causality, index in specs:
                ltm.write(
                    {
                        "id": event_id,
                        "summary": summary,
                        "action": action,
                        "causality": causality,
                        "timestamp": ts,
                        "last_active": ts,
                        "participants": (
                            [{"role": "用户", "seat": "主驾"}]
                            if event_id == "evt_climate_a"
                            else [{"role": "车机"}]
                        ),
                        "payload": {
                            "episode_id": episode_id,
                            "episode_text": episode_text,
                            "event_index": index,
                        },
                    },
                    kind="event",
                    entity_ids=["后排温度", "26度", "安抚模式"],
                    evolve=False,
                )

            context = ltm.write(
                {
                    "id": "ctx_climate_semantics",
                    "summary": "context:车内温控场景",
                    "subtype": "温控场景",
                    "description": "用户处于车内温控场景，当前关注车内温度与舒适度",
                },
                kind="context",
            )["item"]
            for event_id, *_rest in specs:
                ltm.store.link_event_to_context(
                    event_id=event_id,
                    context_id=context["id"],
                    confidence=0.9,
                    weight=1.0,
                    original_signal="unit_test",
                    evidence_span=episode_text,
                    timestamp=ts,
                )

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._llm_merge_available = lambda: True
            engine._ensure_event_embedding = lambda event: {
                "evt_climate_a": [0.0, 1.0, 0.0],
                "evt_climate_b": [0.0, 0.99, 0.01],
            }.get(event.id, [1.0, 0.0, 0.0])
            engine._call_merge_llm = lambda payload: {
                "should_merge": True,
                "canonical_id": "evt_climate_a",
                "reason": "same_climate_main_event",
                "confidence": 0.94,
            }
            engine._llm_rewrite_merged_event = lambda canonical, merged: {
                "summary": "用户将后排温度调整为26度，车机启动安抚模式",
                "action": "调整后排温度",
                "causality": "车机启动安抚模式",
            }

            report = ltm.auto_merge(
                scope="event",
                strategy="llm",
                dry_run=False,
                max_pairs=10,
            )
            self.assertEqual(report["merged_events"], 1)

            canonical = ltm.get_event("evt_climate_a")
            self.assertIsNotNone(canonical)
            self.assertEqual(canonical.summary, "用户将后排温度调整为26度，车机启动安抚模式")
            self.assertEqual(canonical.action, "调整后排温度")
            self.assertEqual(canonical.causality, "车机启动安抚模式")

    def test_auto_merge_does_not_merge_two_existing_main_events(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_event_no_main_to_main_merge.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": False,
                    "generate_answer": False,
                },
            )

            ts_nav = 1773337414
            ts_climate = 1773337705
            ltm.write(
                {
                    "id": "evt_nav_root",
                    "summary": "用户发起导航",
                    "action": "发起导航",
                    "timestamp": ts_nav,
                    "last_active": ts_nav,
                    "participants": [{"role": "用户"}],
                    "payload": {"episode_id": "episode_nav_bug", "event_index": 0},
                },
                kind="event",
                entity_ids=["儿童医院停车场"],
                evolve=False,
            )
            ltm.write(
                {
                    "id": "evt_nav_target",
                    "summary": "导航到儿童医院停车场",
                    "action": "导航到儿童医院停车场",
                    "timestamp": ts_nav,
                    "last_active": ts_nav,
                    "participants": [{"role": "用户"}],
                    "payload": {"episode_id": "episode_nav_bug", "event_index": 1},
                },
                kind="event",
                entity_ids=["儿童医院停车场"],
                evolve=False,
            )
            nav_context = ltm.write(
                {
                    "id": "ctx_nav_bug",
                    "summary": "context:出行导航场景",
                    "subtype": "出行导航",
                    "description": "用户处于出行导航场景，系统正在辅助前往目标地点",
                },
                kind="context",
            )["item"]
            for event_id in ["evt_nav_root", "evt_nav_target"]:
                ltm.store.link_event_to_context(
                    event_id=event_id,
                    context_id=nav_context["id"],
                    confidence=0.9,
                    weight=1.0,
                    original_signal="unit_test",
                    evidence_span="navigation",
                    timestamp=ts_nav,
                )
            ltm.merge_event(
                canonical_event_id="evt_nav_root",
                merged_event_id="evt_nav_target",
                merged_at=ts_nav + 1,
                merge_reason="unit_test_main_event",
            )

            ltm.write(
                {
                    "id": "evt_climate_root",
                    "summary": "用户将后排温度调整为26度",
                    "action": "调整后排温度",
                    "timestamp": ts_climate,
                    "last_active": ts_climate,
                    "participants": [{"role": "用户", "seat": "主驾"}],
                    "payload": {"episode_id": "episode_climate_bug", "event_index": 0},
                },
                kind="event",
                entity_ids=["后排温度", "26度"],
                evolve=False,
            )
            ltm.write(
                {
                    "id": "evt_climate_mode",
                    "summary": "车机启动安抚模式",
                    "action": "启动安抚模式",
                    "timestamp": ts_climate,
                    "last_active": ts_climate,
                    "participants": [{"role": "车机"}],
                    "payload": {"episode_id": "episode_climate_bug", "event_index": 1},
                },
                kind="event",
                entity_ids=["车机"],
                evolve=False,
            )
            climate_context = ltm.write(
                {
                    "id": "ctx_climate_bug",
                    "summary": "context:车内温控场景",
                    "subtype": "温控场景",
                    "description": "用户处于车内温控场景，当前关注车内温度与舒适度",
                },
                kind="context",
            )["item"]
            for event_id in ["evt_climate_root", "evt_climate_mode"]:
                ltm.store.link_event_to_context(
                    event_id=event_id,
                    context_id=climate_context["id"],
                    confidence=0.9,
                    weight=1.0,
                    original_signal="unit_test",
                    evidence_span="climate",
                    timestamp=ts_climate,
                )
            ltm.merge_event(
                canonical_event_id="evt_climate_root",
                merged_event_id="evt_climate_mode",
                merged_at=ts_climate + 1,
                merge_reason="unit_test_main_event",
            )

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._llm_merge_available = lambda: True
            engine._call_merge_llm = lambda payload: {
                "should_merge": True,
                "canonical_id": str(payload.get("left", {}).get("id", "") or ""),
                "reason": "should_have_been_blocked_locally",
                "confidence": 0.99,
            }

            report = ltm.auto_merge(
                scope="event",
                strategy="llm",
                dry_run=True,
                max_pairs=10,
            )
            self.assertEqual(report["event_candidates"], 0)
            self.assertEqual(report["event_plans"], [])

    def test_auto_merge_allows_main_event_to_absorb_remaining_atomic_event(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_event_main_absorbs_atomic.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": False,
                    "generate_answer": False,
                },
            )

            ts = 1773337414
            episode_id = "episode_nav_repeat_click"
            episode_text = "用户发起导航，从当前位置导航到儿童医院停车场，用时20分钟"
            specs = [
                ("evt_repeat_root", "用户发起导航", "发起导航", 0),
                ("evt_repeat_target", "导航到儿童医院停车场", "导航到儿童医院停车场", 1),
                ("evt_repeat_duration", "导航耗时20分钟", "导航耗时20分钟", 2),
            ]
            for event_id, summary, action, index in specs:
                ltm.write(
                    {
                        "id": event_id,
                        "summary": summary,
                        "action": action,
                        "timestamp": ts,
                        "last_active": ts,
                        "participants": [{"role": "用户"}],
                        "payload": {
                            "episode_id": episode_id,
                            "episode_text": episode_text,
                            "event_index": index,
                        },
                    },
                    kind="event",
                    entity_ids=["儿童医院停车场"],
                    evolve=False,
                )

            context = ltm.write(
                {
                    "id": "ctx_repeat_nav",
                    "summary": "context:出行导航场景",
                    "subtype": "出行导航",
                    "description": "用户处于出行导航场景，系统正在辅助前往目标地点",
                },
                kind="context",
            )["item"]
            for event_id, *_rest in specs:
                ltm.store.link_event_to_context(
                    event_id=event_id,
                    context_id=context["id"],
                    confidence=0.9,
                    weight=1.0,
                    original_signal="unit_test",
                    evidence_span=episode_text,
                    timestamp=ts,
                )

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._llm_merge_available = lambda: True
            engine._ensure_event_embedding = lambda event: {
                "evt_repeat_root": [1.0, 0.0, 0.0],
                "evt_repeat_target": [0.99, 0.01, 0.0],
                "evt_repeat_duration": [0.98, 0.02, 0.0],
            }.get(event.id, [0.0, 1.0, 0.0])
            engine._call_merge_llm = lambda payload: {
                "should_merge": True,
                "canonical_id": "evt_repeat_root",
                "reason": "same_navigation_main_event",
                "confidence": 0.95,
            }

            first_report = ltm.auto_merge(
                scope="event",
                strategy="llm",
                dry_run=False,
                max_pairs=1,
            )
            self.assertEqual(first_report["merged_events"], 1)

            canonical = ltm.get_event("evt_repeat_root")
            self.assertIsNotNone(canonical)
            self.assertEqual(canonical.status, "active")

    def test_auto_merge_does_not_absorb_cross_episode_atomic_event_into_main_event(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_event_main_scope_guard.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": False,
                    "generate_answer": False,
                },
            )

            nav_ts = 1773389632
            nav_episode = "episode_nav_scope"
            for event_id, summary, action, index in [
                ("evt_nav_scope_root", "用户发起导航", "发起导航", 0),
                ("evt_nav_scope_target", "导航到公共充电站", "导航到公共充电站", 1),
            ]:
                ltm.write(
                    {
                        "id": event_id,
                        "summary": summary,
                        "action": action,
                        "timestamp": nav_ts,
                        "last_active": nav_ts,
                        "participants": [{"role": "用户"}],
                        "payload": {
                            "episode_id": nav_episode,
                            "episode_text": "用户发起导航，从当前位置导航到公共充电站",
                            "event_index": index,
                        },
                    },
                    kind="event",
                    entity_ids=["公共充电站"],
                    evolve=False,
                )

            ctx = ltm.write(
                {
                    "id": "ctx_scope_shared",
                    "summary": "context:出行导航场景",
                    "subtype": "出行导航",
                    "description": "用户处于出行导航场景，系统正在辅助前往目标地点",
                },
                kind="context",
            )["item"]
            for event_id in ["evt_nav_scope_root", "evt_nav_scope_target"]:
                ltm.store.link_event_to_context(
                    event_id=event_id,
                    context_id=ctx["id"],
                    confidence=0.9,
                    weight=1.0,
                    original_signal="unit_test",
                    evidence_span="navigation",
                    timestamp=nav_ts,
                )

            ltm.merge_event(
                canonical_event_id="evt_nav_scope_root",
                merged_event_id="evt_nav_scope_target",
                merged_at=nav_ts + 1,
                merge_reason="unit_test_main_event",
            )

            cinema_ts = 1773389887
            ltm.write(
                {
                    "id": "evt_cinema_atomic",
                    "summary": "车机已为用户打开影院模式",
                    "action": "打开影院模式",
                    "timestamp": cinema_ts,
                    "last_active": cinema_ts,
                    "participants": [{"role": "车机"}],
                    "payload": {
                        "episode_id": "episode_cinema_scope",
                        "episode_text": "车机已为用户打开影院模式",
                        "event_index": 0,
                    },
                },
                kind="event",
                entity_ids=["车机"],
                evolve=False,
            )
            ltm.store.link_event_to_context(
                event_id="evt_cinema_atomic",
                context_id=ctx["id"],
                confidence=0.9,
                weight=1.0,
                original_signal="unit_test",
                evidence_span="cinema",
                timestamp=cinema_ts,
            )

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._llm_merge_available = lambda: True
            engine._call_merge_llm = lambda payload: {
                "should_merge": True,
                "canonical_id": str(payload.get("left", {}).get("id", "") or ""),
                "reason": "should_have_been_blocked_by_scope_guard",
                "confidence": 0.99,
            }

            report = ltm.auto_merge(
                scope="event",
                strategy="llm",
                dry_run=True,
                max_pairs=10,
            )
            self.assertEqual(report["event_candidates"], 0)
            self.assertEqual(report["event_plans"], [])

    def test_llm_context_merge_requires_minimum_local_score(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_context_llm_local_gate.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "enable_auto_consolidation": False,
                    "generate_answer": False,
                },
            )

            first_context = ltm.write(
                {
                    "id": "ctx_nav_llm_gate",
                    "summary": "出行导航场景",
                    "subtype": "state",
                    "description": "用户处于出行导航场景，导航已经开始并处于进行中状态",
                },
                kind="context",
            )["item"]
            second_context = ltm.write(
                {
                    "id": "ctx_cinema_llm_gate",
                    "summary": "坐在副驾的用户说:充电这会儿想看一集剧 -> 车机回答:已为你",
                    "subtype": "situation",
                    "description": "副驾乘客在充电等待期间希望看视频放松，当前为车内娱乐请求场景",
                },
                kind="context",
            )["item"]

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._llm_merge_available = lambda: True
            engine._call_merge_llm = lambda payload: {
                "should_merge": True,
                "canonical_id": first_context["id"],
                "reason": "should_have_been_blocked_by_local_context_gate",
                "confidence": 0.99,
            }

            preview = ltm.auto_merge(
                scope="context",
                strategy="llm",
                dry_run=True,
                max_pairs=10,
            )
            self.assertEqual(preview["context_candidates"], 0)
            self.assertEqual(preview["context_plans"], [])

    def test_context_merge_score_uses_summary_embedding_or_exact_match(self):
        engine = DynamicEvolutionEngine(
            store=object(),
            config=DynamicEvolutionConfig(),
        )
        left = ContextDraft(
            summary="会议场景",
            description="团队成员在办公室进行会议讨论",
        )
        right = ContextDraft(summary="会议场景", description="团队成员在办公室进行会议讨论")

        self.assertGreaterEqual(
            engine._context_merge_score(left, right),
            engine.config.context_reuse_threshold,
        )

    def test_context_similarity_uses_description_enriched_embedding(self):
        engine = DynamicEvolutionEngine(
            store=object(),
            config=DynamicEvolutionConfig(),
        )
        left = ContextDraft(
            subtype="situation",
            summary="音乐播放场景",
            description="用户在车内通过语音播放轻松音乐，当前处于娱乐交互环境",
        )
        right = ContextDraft(
            subtype="state",
            summary="音乐播放场景",
            description="用户在车内请求播放轻音乐，希望放松心情，当前为语音交互",
        )

        self.assertGreaterEqual(
            engine._context_similarity(left, right),
            engine.config.context_reuse_threshold,
        )

    def test_new_context_id_ignores_valid_from_when_slots_only_echo_summary(self):
        engine = DynamicEvolutionEngine(
            store=object(),
            config=DynamicEvolutionConfig(),
        )
        first = ContextDraft(
            subtype="situation",
            summary="音乐播放场景",
            description="用户在车内进行音乐播放",
            valid_from=100,
        )
        second = ContextDraft(
            subtype="situation",
            summary="音乐播放场景",
            description="用户在车内进行音乐播放，当前为娱乐交互",
            valid_from=200,
        )

        self.assertEqual(engine._new_context_id(first), engine._new_context_id(second))

    def test_match_existing_context_reuses_exact_summary_across_subtypes(self):
        class _Store:
            def __init__(self, candidates):
                self._candidates = candidates

            def find_context_candidates(self, context_type, subtype="", limit=20, only_active=True):
                del context_type, limit, only_active
                if subtype:
                    return [item for item in self._candidates if item.subtype == subtype]
                return list(self._candidates)

        existing = Context(
            id="ctx_music_state",
            subtype="state",
            summary="音乐播放场景",
            description="用户在车内进行音乐播放，当前为娱乐交互环境",
            support_count=3,
            last_seen_at=20,
        )
        weaker = Context(
            id="ctx_other",
            subtype="situation",
            summary="会议场景",
            description="团队成员在会议场景中讨论工作事项",
            support_count=1,
            last_seen_at=10,
        )
        engine = DynamicEvolutionEngine(
            store=_Store([existing, weaker]),
            config=DynamicEvolutionConfig(),
        )

        match = engine.match_existing_context(
            ContextDraft(
                subtype="situation",
                summary="音乐播放场景",
                description="用户在车内播放音乐，希望放松心情",
            )
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.id, existing.id)

    def test_match_existing_context_uses_global_exact_summary_beyond_recent_limit(self):
        class _Store:
            def __init__(self, recent_candidates, all_contexts):
                self._recent_candidates = recent_candidates
                self._all_contexts = {context.id: context for context in all_contexts}

            def find_context_candidates(self, context_type, subtype="", limit=20, only_active=True):
                del context_type, limit, only_active
                candidates = list(self._recent_candidates)
                if subtype:
                    candidates = [item for item in candidates if item.subtype == subtype]
                return candidates

            def find_contexts_summary_index(self, context_type, only_active=True):
                del context_type, only_active
                return [
                    (context.id, context.summary)
                    for context in self._all_contexts.values()
                ]

            def get_context(self, context_id):
                return self._all_contexts.get(context_id)

        exact_but_not_recent = Context(
            id="ctx_global_exact",
            subtype="state",
            summary="音乐播放场景",
            description="用户在车内进行音乐播放，当前为娱乐交互环境",
            support_count=5,
            last_seen_at=50,
            status="active",
        )
        recent_other = Context(
            id="ctx_recent_other",
            subtype="situation",
            summary="会议场景",
            description="团队成员在会议场景中讨论工作事项",
            support_count=2,
            last_seen_at=100,
            status="active",
        )
        engine = DynamicEvolutionEngine(
            store=_Store([recent_other], [recent_other, exact_but_not_recent]),
            config=DynamicEvolutionConfig(context_candidate_limit=1),
        )

        match = engine.match_existing_context(
            ContextDraft(
                subtype="situation",
                summary="音乐播放场景",
                description="用户在车内播放音乐，希望放松心情",
            )
        )

        self.assertIsNotNone(match)
        self.assertEqual(match.id, exact_but_not_recent.id)

    def test_context_conflict_ratio_returns_zero_when_description_missing(self):
        engine = DynamicEvolutionEngine(
            store=object(),
            config=DynamicEvolutionConfig(),
        )
        no_slots_left = ContextDraft(summary="会议场景")
        no_slots_right = ContextDraft(summary="会议场景")
        sparse_right = ContextDraft(
            summary="会议场景",
            description="团队成员在办公室进行会议讨论",
        )

        self.assertAlmostEqual(engine._context_conflict_ratio(no_slots_left, no_slots_right), 0.0)
        self.assertAlmostEqual(engine._context_conflict_ratio(no_slots_left, sparse_right), 0.0)

    def test_create_ltm_ignores_removed_context_similarity_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_context_similarity_overrides.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "generate_answer": False,
                    "context_fuzzy_match_threshold": 0.55,
                    "context_similarity_summary_weight": 0.31,
                    "context_merge_containment_weight_mid": 0.19,
                },
            )

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            self.assertGreater(engine.config.context_reuse_threshold, 0.0)


if __name__ == "__main__":
    unittest.main()
