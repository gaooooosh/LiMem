# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

from limem.builder.extractor import ExtractionResult
from script.trips_debugger import TripsDebuggerConfig, TripsDebuggerSession


def _stub_episode_embeddings(engine) -> None:
    episode_vectors: dict[str, list[float]] = {}

    def fake_ensure_event_embedding(event):
        payload = event.payload if isinstance(event.payload, dict) else {}
        episode_id = str(payload.get("episode_id", "") or "").strip() or str(event.id or "")
        if episode_id not in episode_vectors:
            index = len(episode_vectors)
            episode_vectors[episode_id] = [1.0, 0.0] if index % 2 == 0 else [0.0, 1.0]
        return episode_vectors[episode_id]

    engine._ensure_event_embedding = fake_ensure_event_embedding


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
                    auto_merge_after_write=False,
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
                    "description": "用户处于会议相关场景，需要保持安静和专注",
                },
            )
            context_id = context_write["result"]["item"]["id"]
            self.assertTrue(context_id)

            second_context = session.write_manual(
                kind="context",
                payload={
                    "summary": "context:会议场景 / 安静",
                    "subtype": "会议场景",
                    "description": "用户处于会议场景，并且希望环境保持安静",
                },
            )["result"]["item"]["id"]

            preview = session.auto_merge(
                scope="context",
                strategy="llm",
                dry_run=True,
                max_pairs=5,
            )
            self.assertGreaterEqual(preview["result"]["context_candidates"], 1)

            merge_result = session.auto_merge(
                scope="context",
                strategy="llm",
                dry_run=False,
                max_pairs=5,
            )
            self.assertGreaterEqual(merge_result["result"]["merged_contexts"], 1)
            self.assertEqual(
                merge_result["state"]["latest_auto_merge"]["resolved_strategy"],
                "llm",
            )
            self.assertGreaterEqual(len(merge_result["state"]["operation_log"]), 1)

    def test_batch_write_auto_merge_prioritizes_new_episode_local_event_merge(self):
        sample = [
            {
                "trip_meta": {"trip_id": "trip_1"},
                "导航记录数据": [
                    {
                        "start_time": "2026-03-12 22:44:39",
                        "source": "导航记录",
                        "payload": {},
                        "detail": "2026-03-12 晚上10点半左右,用户发起导航,从当前位置导航到公司园区停车场,用时17分钟",
                    }
                ],
                "车机对话数据": [
                    {
                        "start_time": "2026-03-12 22:46:00",
                        "source": "车机对话",
                        "payload": {},
                        "detail": "2026-03-12 晚上10点46分左右,车机已为用户打开影院模式",
                    }
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as td:
            trips_path = os.path.join(td, "trips.json")
            db_path = os.path.join(td, "debugger_batch_local.kz")
            with open(trips_path, "w", encoding="utf-8") as f:
                json.dump(sample, f, ensure_ascii=False)

            session = TripsDebuggerSession(
                TripsDebuggerConfig(
                    trips_path=trips_path,
                    db_path=db_path,
                    offline_mode=True,
                    append_first_mode=True,
                    snapshot_limit=50,
                    auto_merge_after_write=True,
                )
            )

            def fake_extract(text: str) -> ExtractionResult:
                if "导航" in text:
                    events = [
                        {
                            "summary": "用户发起导航",
                            "participants": [{"role": "用户", "seat": ""}],
                            "time_range": {"start": 1773326679, "end": 1773326679, "display_time_bucket": "evening"},
                            "action": "发起导航",
                            "causality": "",
                            "evidence": [],
                        },
                        {
                            "summary": "用户从当前位置导航到公司园区停车场",
                            "participants": [{"role": "用户", "seat": ""}],
                            "time_range": {"start": 1773326679, "end": 1773326679, "display_time_bucket": "evening"},
                            "action": "导航",
                            "causality": "",
                            "evidence": [],
                        },
                        {
                            "summary": "导航耗时17分钟",
                            "participants": [{"role": "用户", "seat": ""}],
                            "time_range": {"start": 1773326679, "end": 1773326679, "display_time_bucket": "evening"},
                            "action": "导航耗时17分钟",
                            "causality": "",
                            "evidence": [],
                        },
                    ]
                    return ExtractionResult(event_data=events[0], events_data=events, entities=["公司", "停车场"])
                events = [
                    {
                        "summary": "车机已为用户打开影院模式",
                        "participants": [{"role": "车机", "seat": ""}],
                        "time_range": {"start": 1773326760, "end": 1773326760, "display_time_bucket": "evening"},
                        "action": "打开影院模式",
                        "causality": "",
                        "evidence": [],
                    }
                ]
                return ExtractionResult(event_data=events[0], events_data=events, entities=["车机"])

            session._ltm.builder.extractor.extract = fake_extract
            session._ltm.dynamic_engine._llm_merge_available = lambda: True
            session._ltm.dynamic_engine._call_merge_llm = lambda payload: {
                "should_merge": payload.get("pair_features", {}).get("same_episode", False),
                "canonical_id": str(payload.get("left", {}).get("id", "") or ""),
                "reason": "same_episode_batch_local_merge",
                "confidence": 0.95,
            }
            _stub_episode_embeddings(session._ltm.dynamic_engine)

            write_result = session.write_selected([0, 1])
            auto_merge = write_result["auto_merge"]
            self.assertIsNotNone(auto_merge)
            self.assertEqual(auto_merge["event_candidates"], 2)
            self.assertEqual(auto_merge["merged_events"], 2)

            snapshot_events = write_result["state"]["snapshot"]["events"]
            self.assertTrue(
                any(
                    event["status"] == "active" and "停车场" in event["summary"]
                    for event in snapshot_events
                )
            )
            self.assertTrue(
                any(
                    event["status"] == "active" and "影院模式" in event["summary"]
                    for event in snapshot_events
                )
            )
            self.assertFalse(
                any(
                    event["status"] == "active" and "停车场" in event["summary"] and "影院模式" in event["summary"]
                    for event in snapshot_events
                )
            )

    def test_manual_merge_event_uses_last_written_batch_scope(self):
        sample = [
            {
                "trip_meta": {"trip_id": "trip_1"},
                "导航记录数据": [
                    {
                        "start_time": "2026-03-12 22:44:39",
                        "source": "导航记录",
                        "payload": {},
                        "detail": "2026-03-12 晚上10点半左右,用户发起导航,从当前位置导航到公司园区停车场,用时17分钟",
                    }
                ],
                "车机对话数据": [
                    {
                        "start_time": "2026-03-12 22:46:00",
                        "source": "车机对话",
                        "payload": {},
                        "detail": "2026-03-12 晚上10点46分左右,车机已为用户打开影院模式",
                    }
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as td:
            trips_path = os.path.join(td, "trips.json")
            db_path = os.path.join(td, "debugger_manual_batch_local.kz")
            with open(trips_path, "w", encoding="utf-8") as f:
                json.dump(sample, f, ensure_ascii=False)

            session = TripsDebuggerSession(
                TripsDebuggerConfig(
                    trips_path=trips_path,
                    db_path=db_path,
                    offline_mode=True,
                    append_first_mode=True,
                    snapshot_limit=50,
                    auto_merge_after_write=False,
                )
            )

            def fake_extract(text: str) -> ExtractionResult:
                if "导航" in text:
                    events = [
                        {
                            "summary": "用户发起导航",
                            "participants": [{"role": "用户", "seat": ""}],
                            "time_range": {"start": 1773326679, "end": 1773326679, "display_time_bucket": "evening"},
                            "action": "发起导航",
                            "causality": "",
                            "evidence": [],
                        },
                        {
                            "summary": "用户从当前位置导航到公司园区停车场",
                            "participants": [{"role": "用户", "seat": ""}],
                            "time_range": {"start": 1773326679, "end": 1773326679, "display_time_bucket": "evening"},
                            "action": "导航",
                            "causality": "",
                            "evidence": [],
                        },
                        {
                            "summary": "导航耗时17分钟",
                            "participants": [{"role": "用户", "seat": ""}],
                            "time_range": {"start": 1773326679, "end": 1773326679, "display_time_bucket": "evening"},
                            "action": "导航耗时17分钟",
                            "causality": "",
                            "evidence": [],
                        },
                    ]
                    return ExtractionResult(event_data=events[0], events_data=events, entities=["公司", "停车场"])
                events = [
                    {
                        "summary": "车机已为用户打开影院模式",
                        "participants": [{"role": "车机", "seat": ""}],
                        "time_range": {"start": 1773326760, "end": 1773326760, "display_time_bucket": "evening"},
                        "action": "打开影院模式",
                        "causality": "",
                        "evidence": [],
                    }
                ]
                return ExtractionResult(event_data=events[0], events_data=events, entities=["车机"])

            session._ltm.builder.extractor.extract = fake_extract
            session._ltm.dynamic_engine._llm_merge_available = lambda: True
            session._ltm.dynamic_engine._call_merge_llm = lambda payload: {
                "should_merge": payload.get("pair_features", {}).get("same_episode", False),
                "canonical_id": str(payload.get("left", {}).get("id", "") or ""),
                "reason": "same_episode_manual_batch_local_merge",
                "confidence": 0.95,
            }
            _stub_episode_embeddings(session._ltm.dynamic_engine)

            session.write_selected([0, 1], auto_merge=False)
            merge_result = session.auto_merge(scope="event", strategy="llm", dry_run=False, max_pairs=10)

            self.assertEqual(merge_result["result"]["event_candidates"], 2)
            self.assertEqual(merge_result["result"]["merged_events"], 2)
            snapshot_events = merge_result["state"]["snapshot"]["events"]
            self.assertTrue(
                any(
                    event["status"] == "active" and "停车场" in event["summary"]
                    for event in snapshot_events
                )
            )
            self.assertTrue(
                any(
                    event["status"] == "active" and "影院模式" in event["summary"]
                    for event in snapshot_events
                )
            )
            self.assertFalse(
                any(
                    event["status"] == "active" and "停车场" in event["summary"] and "影院模式" in event["summary"]
                    for event in snapshot_events
                )
            )


if __name__ == "__main__":
    unittest.main()
