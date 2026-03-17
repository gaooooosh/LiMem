# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from limem import create_ltm, Episode
from limem.builder.extractor import ExtractionResult


class TestMultiEventIngest(unittest.TestCase):
    def test_single_ingest_can_persist_multiple_events(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "test_multi_event_ingest.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "generate_answer": False,
                },
            )

            def fake_extract(_: str) -> ExtractionResult:
                events = [
                    {
                        "summary": "用户提出导航需求",
                        "participants": [{"role": "用户", "seat": ""}],
                        "time_range": {"start": 0, "end": 0, "display_time_bucket": ""},
                        "action": "提出导航需求",
                        "causality": "",
                        "evidence": [],
                    },
                    {
                        "summary": "系统开始规划路线",
                        "participants": [{"role": "系统", "seat": ""}],
                        "time_range": {"start": 0, "end": 0, "display_time_bucket": ""},
                        "action": "规划路线",
                        "causality": "响应用户导航需求",
                        "evidence": [],
                    },
                ]
                return ExtractionResult(
                    event_data=events[0],
                    events_data=events,
                    entities=["公司"],
                )

            ltm.builder.extractor.extract = fake_extract

            result = ltm.ingest(
                Episode(
                    content="用户说导航去公司，系统开始规划路线",
                    timestamp=1773326500,
                )
            )
            stats = ltm.get_stats()
            self.assertEqual(len(result.events), 2)
            self.assertEqual(result.to_dict().get("event_count"), 2)
            self.assertGreaterEqual(stats.get("event_count", 0), 2)


if __name__ == "__main__":
    unittest.main()
