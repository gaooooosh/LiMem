# -*- coding: utf-8 -*-
import unittest

from limem.utils import normalize_entity_candidates, normalize_event_payload


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

        self.assertEqual(normalized["event_type"], "interaction")
        self.assertEqual(normalized["participants"], [{"role": "用户", "seat": ""}])
        self.assertEqual(normalized["location"]["geo_context"], "车内")
        self.assertEqual(normalized["location"]["digital_context"], "导航系统")
        self.assertEqual(normalized["time_range"]["display_time_bucket"], "morning")
        self.assertTrue(normalized["summary"])
        self.assertNotIn("actor", normalized)
        self.assertNotIn("context", normalized)
        self.assertNotIn("time", normalized)
        self.assertNotIn("outcome", normalized)

    def test_event_type_is_inferred_from_change_semantics(self):
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

        self.assertEqual(normalized["event_type"], "observation")

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


if __name__ == "__main__":
    unittest.main()
