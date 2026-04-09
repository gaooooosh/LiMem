# -*- coding: utf-8 -*-
import unittest

from limem.builder.extractor import TwoStageExtractor


def _make_extractor() -> TwoStageExtractor:
    extractor = object.__new__(TwoStageExtractor)
    extractor.api_key = "test-key"
    extractor.base_url = "http://test.local"
    extractor.generation_model = "test-model"
    extractor.enable_thinking = False
    extractor.llm_concurrency = 1
    extractor._event_segment_system_prompt = "SEGMENT_SYSTEM"
    extractor._event_segment_user_prompt = "{episode_text}"
    extractor._event_struct_system_prompt = "STRUCT_SYSTEM"
    extractor._event_struct_user_prompt = "{segment_text}"
    extractor._event_struct_batch_user_prompt = "{segments_text}"
    extractor._event_system_prompt = "LEGACY_EVENT_SYSTEM"
    extractor._event_user_prompt = "{episode_text}"
    extractor._entity_system_prompt = "ENTITY_SYSTEM"
    extractor._entity_user_prompt = "{episode_text}"
    extractor._TWO_STAGE_TEXT_THRESHOLD = 0
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

    def test_two_stage_pipeline_keeps_vehicle_control_events(self):
        extractor = _make_extractor()

        def fake_call_generation_json(system_prompt, user_message, default):
            if system_prompt == "SEGMENT_SYSTEM":
                return {
                    "segments": [
                        {"order": 1, "span_text": "把后排温度调到26度"},
                        {"order": 2, "span_text": "已调整后排温度到26度并启动安抚模式"},
                    ]
                }
            if system_prompt == "STRUCT_SYSTEM":
                if "把后排温度调到26度" in user_message:
                    return {
                        "event": {
                            "summary": "用户将后排温度调整为26度",
                            "participants": [{"role": "用户", "seat": "主驾"}],
                            "action": "调整后排温度",
                            "time": {"text": "晚上2点左右"},
                            "causality": "",
                        }
                    }
                return {
                    "event": {
                        "summary": "车机启动安抚模式",
                        "participants": [{"role": "车机系统", "seat": ""}],
                        "action": "启动安抚模式",
                        "time": {"text": "晚上2点左右"},
                        "causality": "",
                    }
                }
            raise AssertionError(f"unexpected system prompt: {system_prompt}")

        extractor._call_generation_json = fake_call_generation_json

        episode_text = (
            "2026-03-13 晚上2点左右,坐在主驾的用户说:把后排温度调到26度 -> "
            "车机回答:已调整后排温度到26度并启动安抚模式。（触发前确认）"
        )
        events = extractor._extract_events(episode_text)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["summary"], "用户将后排温度调整为26度")
        self.assertEqual(events[0]["action"], "调整后排温度")
        self.assertEqual(events[1]["summary"], "车机启动安抚模式")
        self.assertEqual(events[1]["action"], "启动安抚模式")

    def test_batch_structuring_reduces_multi_segment_calls_to_one(self):
        extractor = _make_extractor()
        struct_calls = 0

        def fake_call_generation_json(system_prompt, user_message, default):
            nonlocal struct_calls
            if system_prompt == "SEGMENT_SYSTEM":
                return {
                    "segments": [
                        {"order": 1, "span_text": "用户打开座椅加热"},
                        {"order": 2, "span_text": "系统同步调高空调温度"},
                    ]
                }
            if system_prompt == "STRUCT_SYSTEM":
                struct_calls += 1
                self.assertIn("[segment_index=0]", user_message)
                self.assertIn("[segment_index=1]", user_message)
                self.assertIn("用户打开座椅加热", user_message)
                self.assertIn("系统同步调高空调温度", user_message)
                return {
                    "events": [
                        {
                            "segment_index": 0,
                            "event": {
                                "summary": "用户打开座椅加热",
                                "participants": [{"role": "用户"}],
                                "action": "打开座椅加热",
                                "time": {"text": "晚上"},
                                "causality": "",
                            },
                        },
                        {
                            "segment_index": 1,
                            "event": {
                                "summary": "系统调高空调温度",
                                "participants": [{"role": "系统"}],
                                "action": "调高空调温度",
                                "time": {"text": "晚上"},
                                "causality": "响应用户操作",
                            },
                        },
                    ]
                }
            raise AssertionError(f"unexpected system prompt: {system_prompt}")

        extractor._call_generation_json = fake_call_generation_json

        events = extractor._extract_events("用户打开座椅加热，系统同步调高空调温度")

        self.assertEqual(struct_calls, 1)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["action"], "打开座椅加热")
        self.assertEqual(events[1]["action"], "调高空调温度")

    def test_batch_structuring_falls_back_when_segment_count_mismatches(self):
        extractor = _make_extractor()
        batch_attempted = False
        single_segments: list[str] = []

        def fake_call_generation_json(system_prompt, user_message, default):
            nonlocal batch_attempted
            if system_prompt == "SEGMENT_SYSTEM":
                return {
                    "segments": [
                        {"order": 1, "span_text": "用户要求导航去公司"},
                        {"order": 2, "span_text": "系统开始规划路线"},
                    ]
                }
            if system_prompt == "STRUCT_SYSTEM":
                if "[segment_index=0]" in user_message and "[segment_index=1]" in user_message:
                    batch_attempted = True
                    return {
                        "events": [
                            {
                                "segment_index": 0,
                                "event": {
                                    "summary": "用户要求导航去公司",
                                    "participants": [{"role": "用户"}],
                                    "action": "要求导航",
                                    "time": {"text": "上午"},
                                    "causality": "",
                                },
                            }
                        ]
                    }
                single_segments.append(user_message.strip())
                if "用户要求导航去公司" in user_message:
                    return {
                        "event": {
                            "summary": "用户要求导航去公司",
                            "participants": [{"role": "用户"}],
                            "action": "要求导航",
                            "time": {"text": "上午"},
                            "causality": "",
                        }
                    }
                return {
                    "event": {
                        "summary": "系统开始规划路线",
                        "participants": [{"role": "系统"}],
                        "action": "规划路线",
                        "time": {"text": "上午"},
                        "causality": "响应用户导航请求",
                    }
                }
            raise AssertionError(f"unexpected system prompt: {system_prompt}")

        extractor._call_generation_json = fake_call_generation_json

        events = extractor._extract_events("用户要求导航去公司，系统开始规划路线")

        self.assertTrue(batch_attempted)
        self.assertEqual(len(single_segments), 2)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["action"], "要求导航")
        self.assertEqual(events[1]["action"], "规划路线")

    def test_dedupe_keeps_blank_signature_events_for_debugging(self):
        extractor = _make_extractor()

        deduped = extractor._dedupe_events(
            [
                {
                    "summary": "",
                    "action": "",
                    "causality": "",
                    "participants": [{"role": "用户", "seat": ""}],
                },
                {
                    "summary": "",
                    "action": "",
                    "causality": "",
                    "participants": [{"role": "系统", "seat": ""}],
                },
            ]
        )

        self.assertEqual(len(deduped), 2)

    def test_single_pass_keeps_llm_output_without_heuristic_filtering(self):
        extractor = _make_extractor()
        extractor._TWO_STAGE_TEXT_THRESHOLD = 4000

        def fake_call_generation_json(system_prompt, user_message, default):
            if system_prompt == "SEGMENT_SYSTEM":
                return {"segments": []}
            if system_prompt == "LEGACY_EVENT_SYSTEM":
                return {
                    "event": {
                        "summary": "系统设置",
                        "participants": [{"role": "系统"}],
                        "action": "设置",
                        "causality": "",
                    }
                }
            raise AssertionError(f"unexpected system prompt: {system_prompt}")

        extractor._call_generation_json = fake_call_generation_json

        events = extractor._extract_events("[屏幕操作数据] 屏幕: 副驾屏 | 应用: QQ音乐")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "系统设置")
        self.assertEqual(events[0]["action"], "设置")

    def test_single_pass_media_title_summary_is_preserved(self):
        extractor = _make_extractor()

        def fake_call_generation_json(system_prompt, user_message, default):
            if system_prompt == "SEGMENT_SYSTEM":
                return {"segments": []}
            if system_prompt == "LEGACY_EVENT_SYSTEM":
                return {
                    "events": [
                        {
                            "summary": "在芒果TV播放视频《纪录片：城市切片-阶段A》",
                            "participants": [{"role": "芒果TV"}],
                            "action": "播放",
                            "time": {"text": "2026-03-13 下午4点半左右"},
                            "causality": "",
                        }
                    ]
                }
            raise AssertionError(f"unexpected system prompt: {system_prompt}")

        extractor._call_generation_json = fake_call_generation_json

        events = extractor._extract_events("2026-03-13 下午4点半左右,在芒果TV播放视频《纪录片：城市切片-阶段A》")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["summary"], "在芒果TV播放视频《纪录片：城市切片-阶段A》")
        self.assertEqual(events[0]["action"], "播放")


if __name__ == "__main__":
    unittest.main()
