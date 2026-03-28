# -*- coding: utf-8 -*-
import os
import tempfile
import unittest

from limem import create_ltm, Episode
from limem.builder.extractor import ExtractionResult
from script.trips_loader import extract_episode_text


class TestLowSignalIngest(unittest.TestCase):
    def test_vehicle_state_record_text_is_not_raw_json(self):
        record = {
            "start_time": "2026-03-12 22:41:17",
            "end_time": "",
            "source": "车辆状态",
            "payload": {
                "body_status": {
                    "window_pct": [0, 0, 0, 0],
                    "vehicle_motion": "driving",
                },
                "seat_and_thermal": {
                    "hvac_fan": 2,
                    "hvac_temp_set": 24.0,
                    "hvac_mode": "face_foot",
                },
            },
        }

        text = extract_episode_text(record, "车辆状态数据")
        self.assertIn("车辆状态快照", text)
        self.assertNotIn('{"start_time"', text)

    def test_low_signal_episode_does_not_persist_fallback_event(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "low_signal.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "generate_answer": False,
                },
            )

            ltm.builder.extractor.extract = lambda _: ExtractionResult(
                event_data={},
                events_data=[],
                entities=[],
            )

            result = ltm.ingest(
                Episode(
                    content='[车辆状态数据] {"start_time": "2026-03-12 22:41:17", "payload": {"body_status": {"window_pct": [0, 0, 0, 0]}}}',
                    timestamp=1773326477,
                    metadata={"bucket_name": "车辆状态数据"},
                )
            )
            stats = ltm.get_stats()

            self.assertEqual(result.event.status, "skipped")
            self.assertEqual(stats.get("event_count", 0), 0)

    def test_camera_stable_episode_does_not_persist_fallback_event(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "camera_low_signal.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "generate_answer": False,
                },
            )

            ltm.builder.extractor.extract = lambda _: ExtractionResult(
                event_data={
                    "summary": "",
                    "participants": [{"role": "用户", "seat": "0"}],
                    "time_range": {"start": 0, "end": 0, "display_time_bucket": ""},
                    "action": "",
                    "causality": "",
                    "evidence": [],
                },
                events_data=[],
                entities=[],
            )

            result = ltm.ingest(
                Episode(
                    content="[舱内摄像头数据] 主驾目视前方，驾驶姿态稳定。（初始阶段） | 副驾乘客姿态自然。 | 后排状态平稳。",
                    timestamp=1773326369,
                    metadata={"bucket_name": "舱内摄像头数据"},
                )
            )
            stats = ltm.get_stats()

            self.assertEqual(result.event.status, "skipped")
            self.assertEqual(stats.get("event_count", 0), 0)

    def test_dynamic_fallback_summary_is_compact(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "compact_fallback.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "generate_answer": False,
                },
            )

            ltm.builder.extractor.extract = lambda _: ExtractionResult(
                event_data={},
                events_data=[],
                entities=[],
            )

            result = ltm.ingest(
                Episode(
                    content="[车机对话数据] 用户说: 导航去公司 | 车机回答: 已开始导航",
                    timestamp=1773326500,
                    metadata={"bucket_name": "车机对话数据"},
                )
            )

            self.assertEqual(result.event.status, "active")
            self.assertNotIn("|", result.event.summary)
            self.assertNotIn("[车机对话数据]", result.event.summary)

    def test_media_summary_and_time_are_sanitized(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "media_sanitize.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "generate_answer": False,
                },
            )

            ltm.builder.extractor.extract = lambda _: ExtractionResult(
                event_data={
                    "summary": "QQ音乐播放；时间:2023-01-22 02:00",
                    "participants": [{"role": "QQ音乐", "seat": "1"}],
                    "time_range": {"start": 1674324000, "end": 1674324000, "display_time_bucket": "evening"},
                    "action": "播放",
                    "causality": "",
                    "evidence": [],
                },
                events_data=[],
                entities=["QQ音乐", "热身进行曲-阶段A"],
            )

            result = ltm.ingest(
                Episode(
                    content="[媒体播放数据] QQ音乐播放《热身进行曲-阶段A》 | 歌手:运动热身歌单",
                    timestamp=1773340889,
                    metadata={"bucket_name": "媒体播放数据"},
                )
            )

            self.assertEqual(result.event.summary, "QQ音乐播放《热身进行曲-阶段A》")
            self.assertEqual(result.event.time_range["start"], 1773340889)

    def test_dialog_detail_does_not_become_verbatim_long_summary(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "dialog_compact.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "generate_answer": False,
                },
            )

            ltm.builder.extractor.extract = lambda _: ExtractionResult(
                event_data={
                    "summary": "",
                    "participants": [{"role": "用户", "seat": "主驾"}],
                    "time_range": {"start": 168204000, "end": 168204000, "display_time_bucket": "evening"},
                    "action": "",
                    "causality": "",
                    "evidence": [],
                },
                events_data=[],
                entities=["车机"],
            )

            raw_text = "2026-03-12 晚上6点半左右,坐在主驾的用户说:空调先拉满，再给我放点热身歌 -> 车机回答:已开启极速制冷并切到运动热身歌单。（触发后反馈）"
            result = ltm.ingest(
                Episode(
                    content=raw_text,
                    timestamp=1773340839,
                    metadata={"bucket_name": "车机对话数据"},
                )
            )

            self.assertNotIn("->", result.event.summary)
            self.assertLess(len(result.event.summary), len(raw_text))
            self.assertEqual(result.event.time_range["start"], 1773340839)

    def test_screen_fallback_summary_does_not_collapse_to_noun_only_part(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "screen_compact.kz")
            ltm = create_ltm(
                db_path=db_path,
                config={
                    "offline_mode": True,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": True,
                    "generate_answer": False,
                },
            )

            ltm.builder.extractor.extract = lambda _: ExtractionResult(
                event_data={
                    "summary": "",
                    "participants": [{"role": "副驾屏", "seat": "1"}],
                    "time_range": {"start": 0, "end": 0, "display_time_bucket": ""},
                    "action": "",
                    "causality": "",
                    "evidence": [],
                },
                events_data=[],
                entities=["会议模式"],
            )

            result = ltm.ingest(
                Episode(
                    content="[屏幕操作数据] 副驾屏 | 会议模式 | 会议模式已开启 | 会议进行中，屏蔽娱乐内容（阶段1/2）",
                    timestamp=1773328744,
                    metadata={"bucket_name": "屏幕操作数据"},
                )
            )

            self.assertNotEqual(result.event.summary, "副驾屏")
            self.assertIn("开启", result.event.summary)


if __name__ == "__main__":
    unittest.main()
