# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from limem import Episode, Event, IngestResult, create_ltm
from limem.builder.extractor import TwoStageExtractor
from limem.utils import normalize_event_payload
from script.build_ltm_from_trips import _run_phase


class TestSessionBatchRegressions(unittest.TestCase):
    def test_two_stage_generation_json_uses_safe_client_wrapper(self):
        extractor = object.__new__(TwoStageExtractor)
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


if __name__ == "__main__":
    unittest.main()
