# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

from limem import create_ltm
from script.trips_loader import load_and_split_trips_episodes


class TestTripsDebugWorkflow(unittest.TestCase):
    def test_load_and_split_trips_episodes_sorts_before_split(self):
        sample = [
            {
                "trip_meta": {"trip_id": "trip_1"},
                "车机对话数据": [
                    {
                        "start_time": "2026-03-12 22:40:10",
                        "source": "车机对话",
                        "payload": {"query": "后发生"},
                        "detail": "",
                    },
                    {
                        "start_time": "2026-03-12 22:40:09",
                        "source": "车机对话",
                        "payload": {"query": "先发生"},
                        "detail": "",
                    },
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as td:
            trips_path = os.path.join(td, "trips.json")
            with open(trips_path, "w", encoding="utf-8") as f:
                json.dump(sample, f, ensure_ascii=False)

            split = load_and_split_trips_episodes(
                path=trips_path,
                split_ratio=0.5,
                sort_by_time=True,
            )

            self.assertEqual(split.total_episodes, 2)
            self.assertEqual(len(split.base_episodes), 1)
            self.assertEqual(len(split.debug_episodes), 1)
            self.assertIn("先发生", split.base_episodes[0].content)
            self.assertIn("后发生", split.debug_episodes[0].content)
            self.assertEqual(split.base_episodes[0].metadata["record_index"], 1)

    def test_memory_ops_support_write_query_merge_and_remove(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "ops_debug.kz")
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
            first = ltm.write(
                {
                    "summary": "用户在会议场景开启勿扰模式",
                    "event_type": "meeting_mode",
                    "action": "开启勿扰",
                    "timestamp": ts,
                    "last_active": ts,
                    "participants": [{"role": "用户"}],
                    "location": {"digital_context": "会议模式"},
                    "payload": {"context": {"digital_context": "会议模式"}},
                },
                kind="event",
                entity_ids=["会议模式"],
            )
            second = ltm.write(
                {
                    "summary": "用户在会议场景调低风量",
                    "event_type": "meeting_mode",
                    "action": "调低风量",
                    "timestamp": ts + 30,
                    "last_active": ts + 30,
                    "participants": [{"role": "用户"}],
                    "location": {"digital_context": "空调"},
                    "payload": {"context": {"digital_context": "空调"}},
                },
                kind="event",
                entity_ids=["空调"],
            )

            query = ltm.query(limit=10, include_graph=True)
            self.assertGreaterEqual(len(query["events"]), 2)
            self.assertGreaterEqual(query["stats"].get("context_count", 0), 1)

            primary_context = ltm.write(
                {
                    "summary": "context:会议场景",
                    "subtype": "会议场景",
                    "structured_slots": {"scene": "会议场景"},
                },
                kind="context",
            )["item"]
            secondary_context = ltm.write(
                {
                    "summary": "context:会议场景 / 空调",
                    "subtype": "会议场景",
                    "structured_slots": {"scene": "会议场景", "digital_context": "空调"},
                },
                kind="context",
            )["item"]

            ltm.store.link_event_to_context(
                event_id=second["item"]["id"],
                context_id=secondary_context["id"],
                confidence=0.9,
                weight=1.2,
                original_type="manual_test",
                timestamp=ts + 40,
            )

            context_merge = ltm.merge_context(
                canonical_context_id=primary_context["id"],
                merged_context_id=secondary_context["id"],
                merged_at=ts + 60,
            )
            self.assertEqual(context_merge["merged_context"]["status"], "merged")

            snapshot_after_context_merge = ltm.snapshot(limit=20, include_inactive=True)
            self.assertTrue(
                any(
                    edge["event_id"] == second["item"]["id"] and edge["context_id"] == primary_context["id"]
                    for edge in snapshot_after_context_merge["edges"]["event_context"]
                )
            )
            self.assertFalse(
                any(
                    edge["event_id"] == second["item"]["id"] and edge["context_id"] == secondary_context["id"]
                    for edge in snapshot_after_context_merge["edges"]["event_context"]
                )
            )

            event_merge = ltm.merge_event(
                canonical_event_id=first["item"]["id"],
                merged_event_id=second["item"]["id"],
                merged_at=ts + 120,
                merge_reason="unit_test_merge",
            )
            self.assertEqual(event_merge["merged_event"]["status"], "merged")
            self.assertTrue(
                any(
                    trace["source_event_id"] == second["item"]["id"]
                    for trace in event_merge["canonical_event"]["merge_traces"]
                )
            )

            snapshot_after_event_merge = ltm.snapshot(limit=20, include_inactive=True)
            self.assertTrue(
                any(
                    edge["event_id"] == first["item"]["id"] and edge["context_id"] == primary_context["id"]
                    for edge in snapshot_after_event_merge["edges"]["event_context"]
                )
            )
            self.assertFalse(
                any(
                    edge["event_id"] == second["item"]["id"]
                    for edge in snapshot_after_event_merge["edges"]["event_context"]
                )
            )

            removed = ltm.remove(first["item"]["id"], kind="event", hard_delete=False, removed_at=ts + 180)
            self.assertEqual(removed["after"]["status"], "archived")

            inactive_query = ltm.query(limit=20, include_graph=False, include_inactive=True)
            self.assertTrue(
                any(
                    event["id"] == first["item"]["id"] and event["status"] == "archived"
                    for event in inactive_query["events"]
                )
            )


if __name__ == "__main__":
    unittest.main()
