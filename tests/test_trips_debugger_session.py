# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

from limem.trips_debugger import TripsDebuggerConfig, TripsDebuggerSession


class TestTripsDebuggerSession(unittest.TestCase):
    def test_session_supports_episode_write_and_manual_ops(self):
        sample = [
            {
                "trip_meta": {"trip_id": "trip_1"},
                "车机对话数据": [
                    {
                        "start_time": "2026-03-12 17:34:31",
                        "source": "车机对话",
                        "payload": {"query": "我要开会，帮我安静一点", "tts": "已开启会议模式"},
                        "detail": "",
                    },
                    {
                        "start_time": "2026-03-12 17:35:31",
                        "source": "车机对话",
                        "payload": {"query": "导航去公司", "tts": "已开始导航"},
                        "detail": "",
                    },
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as td:
            trips_path = os.path.join(td, "trips.json")
            db_path = os.path.join(td, "debugger.kz")
            with open(trips_path, "w", encoding="utf-8") as f:
                json.dump(sample, f, ensure_ascii=False)

            session = TripsDebuggerSession(
                TripsDebuggerConfig(
                    trips_path=trips_path,
                    db_path=db_path,
                    offline_mode=True,
                    append_first_mode=True,
                    snapshot_limit=20,
                )
            )

            episodes = session.list_episodes()
            self.assertEqual(episodes["total"], 2)

            write_result = session.write_selected([0, 1])
            self.assertEqual(len(write_result["results"]), 2)
            self.assertEqual(write_result["state"]["progress"]["written_count"], 2)
            self.assertGreaterEqual(write_result["state"]["stats"]["event_count"], 2)

            context_write = session.write_manual(
                kind="context",
                payload={
                    "summary": "context:会议场景",
                    "subtype": "会议场景",
                    "structured_slots": {"scene": "会议场景"},
                },
            )
            context_id = context_write["result"]["item"]["id"]
            self.assertTrue(context_id)

            second_context = session.write_manual(
                kind="context",
                payload={
                    "summary": "context:会议场景 / 安静",
                    "subtype": "会议场景",
                    "structured_slots": {"scene": "会议场景", "goal_hint": "保持安静"},
                },
            )["result"]["item"]["id"]

            merge_result = session.merge_context(
                canonical_context_id=context_id,
                merged_context_id=second_context,
            )
            self.assertEqual(merge_result["result"]["merged_context"]["status"], "merged")
            self.assertGreaterEqual(len(merge_result["state"]["operation_log"]), 1)


if __name__ == "__main__":
    unittest.main()
