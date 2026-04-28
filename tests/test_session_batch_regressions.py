# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from limem import Episode, Event, IngestResult, create_ltm
from limem.builder.extractor import UnifiedExtractor
from limem.utils import normalize_event_payload
from script import build_ltm_from_sessions
from script.build_ltm_from_trips import _run_phase


class TestSessionBatchRegressions(unittest.TestCase):
    def test_sessions_main_enables_deferred_phase_evolution_for_both_phases(self):
        with tempfile.TemporaryDirectory() as td:
            args = SimpleNamespace(
                sessions_path=os.path.join(td, "session_v1.json"),
                db_path=os.path.join(td, "dynamic_sessions.kz"),
                max_items=0,
                debug_max_items=0,
                sources="",
                split_index=0,
                split_ratio=1.0,
                no_sort=False,
                online=False,
                legacy_merge=False,
                clear_db=False,
                progress_every=0,
                debug_snapshot_every=0,
                snapshot_limit=5,
                run_consolidation=False,
                skip_visualize=True,
                batch_size=8,
                output_dir=td,
            )
            split_result = SimpleNamespace(
                total_episodes=2,
                base_episodes=[Episode(content="base", timestamp=1)],
                debug_episodes=[Episode(content="debug", timestamp=2)],
                split_index=1,
                split_ratio=0.5,
            )
            phase_calls = []

            class StubLTM:
                def run_consolidation(self):
                    return {}

                def get_stats(self):
                    return {
                        "event_count": 0,
                        "entity_count": 0,
                        "context_count": 0,
                        "involves_count": 0,
                        "in_count": 0,
                        "event_relation_count": 0,
                    }

                def snapshot(self, limit=12, include_inactive=True):
                    return {
                        "limit": limit,
                        "include_inactive": include_inactive,
                        "stats": self.get_stats(),
                        "edges": {},
                    }

            def fake_run_phase(**kwargs):
                phase_calls.append(kwargs)
                return {
                    "episodes": len(kwargs["episodes"]),
                    "errors": 0,
                    "timeline": [],
                    "stats": kwargs["ltm"].get_stats(),
                    "extraction_summary": {},
                    "snapshot": {},
                    "timing": {},
                    "deferred_evolution": {},
                }

            with (
                patch.object(build_ltm_from_sessions, "_parse_args", return_value=args),
                patch.object(
                    build_ltm_from_sessions,
                    "load_and_split_session_episodes",
                    return_value=split_result,
                ),
                patch.object(build_ltm_from_sessions, "create_ltm", return_value=StubLTM()),
                patch.object(build_ltm_from_sessions, "_run_phase", side_effect=fake_run_phase),
                patch.object(build_ltm_from_sessions, "_capture_snapshot", return_value={}),
                patch.object(build_ltm_from_sessions, "_combine_extraction_summaries", return_value={}),
                patch.object(build_ltm_from_sessions, "_render_html_report", return_value="<html></html>"),
            ):
                build_ltm_from_sessions.main()

            self.assertEqual(len(phase_calls), 2)
            self.assertTrue(all(call.get("run_deferred_evolution") is True for call in phase_calls))

    def test_unified_generation_json_uses_safe_client_wrapper(self):
        extractor = object.__new__(UnifiedExtractor)
        extractor.generation_model = "test-generation-model"

        class FakeClient:
            def __init__(self):
                self.calls = []

            def call_generation_json(self, **kwargs):
                self.calls.append(kwargs)
                return {"events": []}

        client = FakeClient()
        extractor.llm_client = client

        result = extractor._call_generation_json(
            system_prompt="SYSTEM",
            user_message="USER",
            default={"fallback": True},
        )

        self.assertEqual(result, {"events": []})
        self.assertEqual(
            client.calls,
            [
                {
                    "system_prompt": "SYSTEM",
                    "user_message": "USER",
                    "default": {"fallback": True},
                    "model": "test-generation-model",
                }
            ],
        )

    def test_session_dynamic_hints_keep_detection_and_scene_events(self):
        cases = [
            ("系统识别到主驾用户上车", "识别用户上车"),
            ("导航已到达公司地库", "到达公司地库"),
            ("午休场景触发空调联动", "触发空调联动"),
            ("系统播报导航结束", "播报导航结束"),
        ]

        for summary, action in cases:
            with self.subTest(summary=summary, action=action):
                normalized = normalize_event_payload(
                    {
                        "event": {
                            "summary": summary,
                            "participants": [{"role": "系统"}],
                            "action": action,
                        }
                    },
                    episode_text=summary,
                )

                self.assertEqual(normalized["summary"], summary)
                self.assertEqual(normalized["action"], action)

    def test_ingest_batch_propagates_extract_only_exceptions(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "session_batch_regression.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "generate_answer": False,
                },
            )

            expected_result = IngestResult(
                event=Event(
                    summary="用户要求导航去公司",
                    action="要求导航",
                    time_range={"start": 2, "end": 2, "display_time_bucket": ""},
                    timestamp=2,
                    last_active=2,
                    created_at=2,
                    updated_at=2,
                    valid_from=2,
                    participants=[{"role": "用户", "seat": ""}],
                    evidence=[],
                ),
                is_new=True,
                events=[],
                metrics={"event_count": 1},
            )

            def fake_extract_only(episode):
                if "失败" in episode.content:
                    raise RuntimeError("dashscope timeout")
                return {"episode_id": episode.id}

            def fake_persist_extraction(_bundle):
                return expected_result

            ltm.builder.extract_only = fake_extract_only
            ltm.builder.persist_extraction = fake_persist_extraction

            results = ltm.ingest_batch(
                [
                    Episode(content="这条会失败", timestamp=1),
                    Episode(content="用户要求导航去公司", timestamp=2),
                ],
                concurrency=2,
            )

            self.assertEqual(len(results), 2)
            self.assertIsInstance(results[0], RuntimeError)
            self.assertEqual(str(results[0]), "dashscope timeout")
            self.assertIs(results[1], expected_result)

    def test_run_phase_logs_batch_exception_repr_and_none_fallback(self):
        class StubLTM:
            def ingest_batch(self, episodes, concurrency=0):
                return [RuntimeError("dashscope timeout"), None]

            def get_stats(self):
                return {"event_count": 0, "entity_count": 0, "context_count": 0}

            def snapshot(self, limit=12, include_inactive=True):
                return {"limit": limit, "include_inactive": include_inactive}

        report = _run_phase(
            ltm=StubLTM(),
            episodes=[
                Episode(content="失败 episode", timestamp=1),
                Episode(content="空结果 episode", timestamp=2),
            ],
            phase_name="debug",
            progress_every=0,
            capture_every=1,
            snapshot_limit=5,
            batch_size=2,
        )

        self.assertEqual(report["errors"], 2)
        self.assertEqual(len(report["timeline"]), 2)
        self.assertEqual(report["timeline"][0]["error"], "RuntimeError('dashscope timeout')")
        self.assertEqual(report["timeline"][1]["error"], "extraction returned None")

    def test_run_phase_aggregates_orphan_context_observation_metrics(self):
        class StubLTM:
            def __init__(self):
                self.calls = 0

            def ingest(self, episode):
                self.calls += 1
                if self.calls == 1:
                    event = Event(
                        id="evt_1",
                        summary="用户发起导航",
                        action="发起导航",
                        time_range={"start": episode.timestamp, "end": episode.timestamp, "display_time_bucket": ""},
                        timestamp=episode.timestamp,
                        last_active=episode.timestamp,
                        created_at=episode.timestamp,
                        updated_at=episode.timestamp,
                        valid_from=episode.timestamp,
                        participants=[{"role": "用户", "seat": ""}],
                        evidence=[],
                    )
                    return IngestResult(
                        event=event,
                        is_new=True,
                        events=[event],
                        metrics={
                            "event_count": 1,
                            "raw_event_count": 1,
                            "subject_event_count": 1,
                            "inline_context_count": 2,
                            "orphan_context_count": 1,
                            "episodes_with_orphan_contexts": 1,
                            "eventless_orphan_episode_count": 0,
                            "orphan_contexts": [
                                {"subtype": "situation", "summary": "时间紧张", "evidence_span": "马上开会"}
                            ],
                        },
                    )

                ignored_event = Event(
                    id="ignored_2",
                    summary="",
                    action="",
                    status="ignored",
                    time_range={"start": episode.timestamp, "end": episode.timestamp, "display_time_bucket": ""},
                    timestamp=episode.timestamp,
                    last_active=episode.timestamp,
                    created_at=episode.timestamp,
                    updated_at=episode.timestamp,
                    valid_from=episode.timestamp,
                    participants=[],
                    evidence=[],
                )
                return IngestResult(
                    event=ignored_event,
                    is_new=False,
                    events=[],
                    metrics={
                        "event_count": 0,
                        "raw_event_count": 0,
                        "subject_event_count": 0,
                        "inline_context_count": 0,
                        "orphan_context_count": 2,
                        "episodes_with_orphan_contexts": 1,
                        "eventless_orphan_episode_count": 1,
                        "orphan_contexts": [
                            {"subtype": "situation", "summary": "低电量", "evidence_span": "电量12%"},
                            {"subtype": "environment", "summary": "车内安静", "evidence_span": "噪音34dB"},
                        ],
                    },
                )

            def get_stats(self):
                return {"event_count": 1, "entity_count": 0, "context_count": 0}

            def snapshot(self, limit=12, include_inactive=True):
                return {"limit": limit, "include_inactive": include_inactive}

        report = _run_phase(
            ltm=StubLTM(),
            episodes=[
                Episode(content="用户导航去公司", timestamp=1),
                Episode(content="环境数据", timestamp=2),
            ],
            phase_name="debug",
            progress_every=0,
            capture_every=1,
            snapshot_limit=5,
            batch_size=0,
        )

        extraction = report["extraction_summary"]
        self.assertEqual(extraction["episodes"], 2)
        self.assertEqual(extraction["event_count"], 1)
        self.assertEqual(extraction["raw_event_count"], 1)
        self.assertEqual(extraction["subject_event_count"], 1)
        self.assertEqual(extraction["inline_context_count"], 2)
        self.assertEqual(extraction["orphan_context_count"], 3)
        self.assertEqual(extraction["episodes_with_orphan_contexts"], 2)
        self.assertEqual(extraction["eventless_orphan_episode_count"], 1)
        self.assertEqual(extraction["subject_event_ratio"], 1.0)
        self.assertEqual(extraction["context_per_event_avg"], 2.0)
        self.assertEqual(extraction["orphan_context_yield"], 1.5)
        self.assertEqual(report["timeline"][0]["ingest_result"]["orphan_context_count"], 1)
        self.assertEqual(report["timeline"][1]["ingest_result"]["orphan_context_count"], 2)


if __name__ == "__main__":
    unittest.main()
