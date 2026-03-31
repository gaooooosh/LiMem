# -*- coding: utf-8 -*-
import unittest

from limem.utils import _SKIP_DYNAMIC_CHECK, normalize_entity_candidates, normalize_event_payload


class TestExtractionNormalization(unittest.TestCase):
    def test_event_payload_is_canonical_and_no_debug_mirrors(self):
        payload = {
            "event": {
                "actor": [{"role": "用户"}],
                "action": "导航去公司",
                "context": {"geo_context": "车内", "digital_context": "导航系统"},
                "time": {"text": "今天上午"},
                "outcome": "系统开始导航",
                "event_type": "interaction",
            }
        }

        normalized = normalize_event_payload(payload, episode_text="用户说：导航去公司")

        self.assertEqual(normalized["participants"], [{"role": "用户", "seat": ""}])
        self.assertEqual(normalized["time_range"]["display_time_bucket"], "morning")
        self.assertEqual(normalized["action"], "导航去公司")
        self.assertEqual(normalized["causality"], "系统开始导航")
        self.assertTrue(normalized["summary"])
        self.assertNotIn("actor", normalized)
        self.assertNotIn("context", normalized)
        self.assertNotIn("time", normalized)
        self.assertNotIn("outcome", normalized)
        self.assertNotIn("event_type", normalized)
        self.assertNotIn("location", normalized)

    def test_dynamic_change_is_preserved(self):
        normalized = normalize_event_payload(
            {
                "event": {
                    "participants": [{"role": "系统"}],
                    "summary": "系统检测到胎压异常",
                    "action": "检测到胎压异常",
                }
            },
            episode_text="系统检测到胎压异常",
        )

        self.assertEqual(normalized["summary"], "系统检测到胎压异常")
        self.assertEqual(normalized["action"], "检测到胎压异常")
        self.assertEqual(normalized["participants"], [{"role": "系统", "seat": ""}])

    def test_static_background_is_not_promoted_as_dynamic_event(self):
        normalized = normalize_event_payload(
            {
                "event": {
                    "participants": [{"role": "用户"}],
                    "summary": "车内环境",
                    "location": {"geo_context": "车内"},
                }
            },
            episode_text="车内环境",
        )

        self.assertEqual(normalized["summary"], "")
        self.assertEqual(normalized["action"], "")

    def test_episode_like_summary_is_rewritten_to_event_summary(self):
        episode_text = "用户说: 导航去公司 | 车机回答: 已开始导航"
        normalized = normalize_event_payload(
            {
                "event": {
                    "summary": episode_text,
                    "participants": [{"role": "用户"}],
                    "action": "导航去公司",
                    "location": {"geo_context": "车内", "digital_context": "导航系统"},
                    "time": {"text": "今天上午"},
                    "outcome": "开始导航",
                }
            },
            episode_text=episode_text,
        )

        self.assertNotEqual(normalized["summary"], episode_text)
        self.assertIn("用户", normalized["summary"])
        self.assertIn("导航去公司", normalized["summary"])

    def test_timestamp_prefixed_single_clause_summary_is_preserved(self):
        episode_text = "2026-03-13 下午4点半左右,在芒果TV播放视频《纪录片：城市切片-阶段A》"
        normalized = normalize_event_payload(
            {
                "event": {
                    "summary": "在芒果TV播放视频《纪录片：城市切片-阶段A》",
                    "participants": [{"role": "芒果TV"}],
                    "action": "播放",
                    "time": {"text": "2026-03-13 下午4点半左右"},
                }
            },
            episode_text=episode_text,
        )

        self.assertEqual(normalized["summary"], "在芒果TV播放视频《纪录片：城市切片-阶段A》")
        self.assertEqual(normalized["action"], "播放")

    def test_entity_candidates_are_de_fragmented(self):
        entities = normalize_entity_candidates(
            [
                "上次",
                "孩子",
                "给孩子",
                "放",
                "音乐",
                "周杰伦的歌",
                "周杰伦",
                "QQ音乐",
                "导航到公司",
                "公司",
                "25",
                "播放动画片",
            ]
        )

        self.assertEqual(entities, ["孩子", "周杰伦", "QQ音乐", "公司"])

    def test_noisy_record_summary_and_inline_history_time_are_cleaned(self):
        episode_text = (
            '[屏幕操作数据] {"start_time":"2026-03-12 17:37:01","source":"屏幕",'
            '"payload":{"SCREEN":"副驾屏","APP":"QQ音乐。还有：QQ音乐播放；时间:2023-03-08 08:40"}}'
        )
        normalized = normalize_event_payload(
            {
                "event": {
                    "summary": episode_text,
                    "action": "QQ音乐播放；时间:2023-03-08 08:40",
                    "participants": [],
                }
            },
            episode_text=episode_text,
        )

        self.assertNotIn("start_time", normalized["summary"])
        self.assertNotIn("2023-03-08 08:40", normalized["summary"])
        self.assertEqual(normalized["action"], "QQ音乐播放")

    def test_environment_snapshot_is_dropped_as_non_event(self):
        episode_text = (
            '[环境感知数据] 来源: 环境感知 | {"source":"环境感知","payload":'
            '{"cabin_env":{"light_amb":"dynamic","noise_db":45,"temp_in":55},'
            '"weather":{"temp_out":35.0,"condition":"sunny"},'
            '"spatial":{"geo_type":"urban"}}}'
        )
        normalized = normalize_event_payload(
            {
                "event": {
                    "summary": episode_text,
                    "participants": [{"role": "环境感知"}],
                    "action": "",
                    "causality": "",
                }
            },
            episode_text=episode_text,
        )

        self.assertEqual(normalized["summary"], "")
        self.assertEqual(normalized["action"], "")
        self.assertEqual(normalized["causality"], "")

    def test_vehicle_adjustment_and_startup_actions_are_kept_as_dynamic_events(self):
        adjust_episode = (
            "2026-03-13 晚上2点左右,坐在主驾的用户说:把后排温度调到26度 -> "
            "车机回答:已调整后排温度到26度并启动安抚模式。"
        )
        adjust_normalized = normalize_event_payload(
            {
                "event": {
                    "summary": "用户将后排温度调整为26度",
                    "participants": [{"role": "用户", "seat": "主驾"}],
                    "action": "调整后排温度",
                    "time": {"text": "晚上2点左右"},
                }
            },
            episode_text=adjust_episode,
        )
        startup_normalized = normalize_event_payload(
            {
                "event": {
                    "summary": "车机启动安抚模式",
                    "participants": [{"role": "车机系统", "seat": ""}],
                    "action": "启动安抚模式",
                }
            },
            episode_text=adjust_episode,
        )

        self.assertEqual(adjust_normalized["summary"], "用户将后排温度调整为26度")
        self.assertEqual(adjust_normalized["action"], "调整后排温度")
        self.assertEqual(startup_normalized["summary"], "车机启动安抚模式")
        self.assertEqual(startup_normalized["action"], "启动安抚模式")

    def test_passive_screen_app_metadata_is_dropped_even_if_llm_generates_generic_setting_event(self):
        episode_text = "[屏幕操作数据] 屏幕: 副驾屏 | 应用: QQ音乐"
        normalized = normalize_event_payload(
            {
                "event": {
                    "summary": "系统设置",
                    "participants": [{"role": "系统", "seat": ""}],
                    "action": "设置",
                    "causality": "",
                }
            },
            episode_text=episode_text,
        )

        self.assertEqual(normalized["summary"], "")
        self.assertEqual(normalized["action"], "")
        self.assertEqual(normalized["causality"], "")

    def test_skip_dynamic_check_preserves_domain_neutral_event(self):
        normalized = normalize_event_payload(
            {
                "event": {
                    "summary": "Alice has a dentist appointment",
                    "participants": [{"role": "Alice"}],
                    "action": "has a dentist appointment",
                }
            },
            episode_text="Alice has a dentist appointment",
            dynamic_hints=_SKIP_DYNAMIC_CHECK,
            telemetry_markers=(),
            passive_screen_prefix="",
            passive_screen_markers=(),
            passive_screen_dynamic_hints=(),
        )

        self.assertEqual(normalized["summary"], "Alice has a dentist appointment")
        self.assertEqual(normalized["action"], "has a dentist appointment")


if __name__ == "__main__":
    unittest.main()
