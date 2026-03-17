# -*- coding: utf-8 -*-
import unittest

from limem.builder.extractor import TwoStageExtractor


def _make_extractor() -> TwoStageExtractor:
    extractor = object.__new__(TwoStageExtractor)
    extractor.api_key = "test-key"
    extractor.base_url = "http://test.local"
    extractor.generation_model = "test-model"
    extractor.enable_thinking = False
    extractor._event_segment_system_prompt = "SEGMENT_SYSTEM"
    extractor._event_segment_user_prompt = "{episode_text}"
    extractor._event_struct_system_prompt = "STRUCT_SYSTEM"
    extractor._event_struct_user_prompt = "{segment_text}"
    extractor._event_system_prompt = "LEGACY_EVENT_SYSTEM"
    extractor._event_user_prompt = "{episode_text}"
    extractor._entity_system_prompt = "ENTITY_SYSTEM"
    extractor._entity_user_prompt = "{episode_text}"
    return extractor


class TestTwoStageEventExtractor(unittest.TestCase):
    def test_two_stage_pipeline_splits_and_structures_multiple_events(self):
        extractor = _make_extractor()

        def fake_call_generation_json(system_prompt, user_message, default):
            if system_prompt == "SEGMENT_SYSTEM":
                return {
                    "segments": [
                        {"order": 1, "span_text": "坐在主驾的用户说: 空调先拉满"},
                        {"order": 2, "span_text": "再给我放点热身歌"},
                        {"order": 3, "span_text": "车机回答: 已开启极速制冷并切到运动热身歌单"},
                    ]
                }
            if system_prompt == "STRUCT_SYSTEM":
                if "空调先拉满" in user_message:
                    return {
                        "event": {
                            "summary": "用户开启空调最大风量",
                            "participants": [{"role": "用户"}],
                            "action": "开启空调最大风量",
                            "time": {"text": "下午5点半左右"},
                            "causality": "",
                        }
                    }
                if "热身歌" in user_message and "车机回答" not in user_message:
                    return {
                        "event": {
                            "summary": "用户播放热身歌",
                            "participants": [{"role": "用户"}],
                            "action": "播放热身歌",
                            "time": {"text": "下午5点半左右"},
                            "causality": "",
                        }
                    }
                return {
                    "event": {
                        "summary": "系统开启极速制冷并切换到运动热身歌单",
                        "participants": [{"role": "系统"}],
                        "action": "开启极速制冷并切换歌单",
                        "time": {"text": "下午5点半左右"},
                        "causality": "响应用户指令",
                    }
                }
            raise AssertionError(f"unexpected system prompt: {system_prompt}")

        extractor._call_generation_json = fake_call_generation_json

        episode_text = (
            "2026-03-12 下午5点半左右,坐在主驾的用户说:空调先拉满，再给我放点热身歌 -> "
            "车机回答:已开启极速制冷并切到运动热身歌单。（触发前确认）"
        )
        events = extractor._extract_events(episode_text)

        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["action"], "开启空调最大风量")
        self.assertEqual(events[1]["action"], "播放热身歌")
        self.assertIn("极速制冷", events[2]["action"])
        self.assertEqual(events[2]["participants"], [{"role": "系统", "seat": ""}])

    def test_fallback_to_single_pass_when_segmentation_stage_fails(self):
        extractor = _make_extractor()

        def fake_call_generation_json(system_prompt, user_message, default):
            if system_prompt == "SEGMENT_SYSTEM":
                raise ValueError("segment model output invalid")
            if system_prompt == "LEGACY_EVENT_SYSTEM":
                return {
                    "event": {
                        "summary": "用户发起导航请求",
                        "participants": [{"role": "用户"}],
                        "action": "发起导航请求",
                        "time": {"text": "上午"},
                        "causality": "",
                    }
                }
            raise AssertionError(f"unexpected system prompt: {system_prompt}")

        extractor._call_generation_json = fake_call_generation_json

        events = extractor._extract_events("用户说: 导航去公司")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "用户发起导航请求")
        self.assertEqual(events[0]["action"], "发起导航请求")


if __name__ == "__main__":
    unittest.main()
