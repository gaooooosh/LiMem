# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from limem import create_ltm, Episode, migrate_to_dynamic_graph


class TestDynamicEvolution(unittest.TestCase):
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

            report = migrate_to_dynamic_graph(ltm.store, dry_run=True).to_dict()
            self.assertIn("scanned_involves", report)
            self.assertTrue(report["dry_run"])

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
                    "structured_slots": {
                        "scene": "会议场景",
                        "geo_context": "车内",
                        "digital_context": "会议模式",
                    },
                },
                kind="context",
            )["item"]
            second_context = ltm.write(
                {
                    "id": "ctx_b",
                    "summary": "context:会议场景 / 车内 / 会议模式 / 早高峰",
                    "subtype": "会议场景",
                    "structured_slots": {
                        "scene": "会议场景",
                        "geo_context": "车内",
                        "digital_context": "会议模式",
                        "time_bucket": "morning",
                    },
                },
                kind="context",
            )["item"]

            engine = ltm.dynamic_engine
            self.assertIsNotNone(engine)
            engine._llm_merge_available = lambda: True

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
                "relation_type": "enables",
                "from_id": "evt_rel_a",
                "to_id": "evt_rel_b",
                "reason": "user_request_enables_route_planning",
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
                    and edge["relation_type"] == "enables"
                    for edge in snapshot["edges"]["event_event"]
                )
            )


if __name__ == "__main__":
    unittest.main()
