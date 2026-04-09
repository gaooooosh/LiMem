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

        self.assertEqual(normalized["participants"], [{"role": "用户", "seat": ""}])
        self.assertEqual(normalized["time_range"]["display_time_bucket"], "morning")
        self.assertEqual(normalized["summary"], "导航去公司")
        self.assertEqual(normalized["action"], "导航去公司")
        self.assertEqual(normalized["causality"], "系统开始导航")
        self.assertNotIn("actor", normalized)
        self.assertNotIn("context", normalized)
        self.assertNotIn("time", normalized)
        self.assertNotIn("outcome", normalized)
        self.assertNotIn("event_type", normalized)

    def test_event_summary_falls_back_to_action_then_causality(self):
        action_only = normalize_event_payload(
            {"event": {"participants": [{"role": "系统"}], "action": "开始播放音乐"}}
        )
        causality_only = normalize_event_payload(
            {"event": {"participants": [{"role": "系统"}], "causality": "播放完成"}}
        )

        self.assertEqual(action_only["summary"], "开始播放音乐")
        self.assertEqual(causality_only["summary"], "播放完成")

    def test_entity_candidates_only_trim_and_dedupe(self):
        entities = normalize_entity_candidates(
            [
                " 周杰伦 ",
                "周杰伦",
                "QQ音乐",
                "A",
                "",
                {"name": "公司"},
            ]
        )

        self.assertEqual(entities, ["周杰伦", "QQ音乐", "公司"])


if __name__ == "__main__":
    unittest.main()
