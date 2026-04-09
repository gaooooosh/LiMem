# -*- coding: utf-8 -*-
import unittest

from limem.builder.extractor import UnifiedExtractor


def _make_extractor() -> UnifiedExtractor:
    extractor = object.__new__(UnifiedExtractor)
    extractor.api_key = "test-key"
    extractor.base_url = "http://test.local"
    extractor.generation_model = "test-model"
    extractor.enable_thinking = False
    extractor.llm_concurrency = 1
    extractor._system_prompt = "UNIFIED_SYSTEM"
    extractor._user_prompt = "对话内容：\n{episode_text}\n"
    return extractor


class TestUnifiedExtractor(unittest.TestCase):
    def test_single_interaction_returns_one_event_with_contexts(self):
        extractor = _make_extractor()

        def fake_call_generation_json(system_prompt, user_message, default):
            self.assertEqual(system_prompt, "UNIFIED_SYSTEM")
            self.assertIn("导航去公司", user_message)
            self.assertEqual(default, {})
            return {
                "events": [
                    {
                        "summary": "用户请求导航去公司，系统开始导航",
                        "participants": [{"role": "用户"}],
                        "action": "请求导航并收到开始导航响应",
                        "time": {"text": "今天上午"},
                        "causality": "用户要去公司",
                        "contexts": [
                            {
                                "subtype": "situation",
                                "summary": "通勤出行场景",
                                "evidence_span": "导航去公司",
                                "confidence": 0.88,
                            },
                            {
                                "subtype": "goal",
                                "summary": "前往公司",
                                "evidence_span": "去公司",
                                "confidence": 0.83,
                            },
                        ],
                    }
                ]
            }

        extractor._call_generation_json = fake_call_generation_json

        result = extractor.extract("用户说：导航去公司，车机回答：已开始导航。")

        self.assertEqual(len(result.events_data), 1)
        self.assertEqual(result.events_data[0]["summary"], "用户请求导航去公司，系统开始导航")
        self.assertEqual(result.events_data[0]["participants"], [{"role": "用户", "seat": ""}])
        self.assertEqual(len(result.events_data[0]["contexts"]), 2)
        self.assertEqual(result.events_data[0]["contexts"][0]["summary"], "通勤出行场景")

    def test_same_intent_multi_actions_are_merged_into_one_event(self):
        extractor = _make_extractor()

        extractor._call_generation_json = lambda system_prompt, user_message, default: {
            "events": [
                {
                    "summary": "用户要求同时调低空调并播放热身歌，系统完成执行",
                    "participants": [{"role": "用户", "seat": "主驾"}],
                    "action": "调低空调并播放热身歌",
                    "time": {"text": "下午5点半"},
                    "causality": "用户准备出发",
                    "contexts": [
                        {
                            "subtype": "situation",
                            "summary": "出发前准备场景",
                            "evidence_span": "空调先拉满，再给我放点热身歌",
                        }
                    ],
                }
            ]
        }

        result = extractor.extract("空调先拉满，再给我放点热身歌。")

        self.assertEqual(len(result.events_data), 1)
        self.assertEqual(result.events_data[0]["action"], "调低空调并播放热身歌")

    def test_multiple_independent_intents_return_multiple_events(self):
        extractor = _make_extractor()

        extractor._call_generation_json = lambda system_prompt, user_message, default: {
            "events": [
                {
                    "summary": "用户发起导航请求，系统开始导航",
                    "participants": [{"role": "用户"}],
                    "action": "请求导航",
                    "time": {"text": "上午"},
                    "causality": "",
                },
                {
                    "summary": "用户切换到音乐播放，系统开始放歌",
                    "participants": [{"role": "用户"}],
                    "action": "播放音乐",
                    "time": {"text": "上午"},
                    "causality": "",
                },
            ]
        }

        result = extractor.extract("导航去公司。然后播放周杰伦。")

        self.assertEqual(len(result.events_data), 2)
        self.assertEqual(result.events_data[0]["action"], "请求导航")
        self.assertEqual(result.events_data[1]["action"], "播放音乐")

    def test_contexts_are_preserved_in_event_payload(self):
        extractor = _make_extractor()

        extractor._call_generation_json = lambda system_prompt, user_message, default: {
            "event": {
                "summary": "用户在车内播放音乐",
                "participants": [{"role": "用户"}],
                "action": "播放音乐",
                "time": {"text": "今天上午"},
                "causality": "",
                "contexts": [
                    {
                        "subtype": "environment",
                        "summary": "车内音乐播放场景",
                        "evidence_span": "在车内播放音乐",
                        "confidence": 0.91,
                    }
                ],
            }
        }

        result = extractor.extract("今天上午在车内播放音乐。")

        self.assertEqual(result.event_data["contexts"][0]["summary"], "车内音乐播放场景")
        self.assertEqual(result.events_data[0]["contexts"][0]["subtype"], "environment")

    def test_llm_failure_returns_empty_result(self):
        extractor = _make_extractor()

        def fake_call_generation_json(system_prompt, user_message, default):
            raise RuntimeError("dashscope timeout")

        extractor._call_generation_json = fake_call_generation_json

        result = extractor.extract("用户说：导航去公司")

        self.assertEqual(result.event_data, {})
        self.assertEqual(result.events_data, [])
        self.assertEqual(result.entities, [])

    def test_empty_llm_output_returns_no_events(self):
        extractor = _make_extractor()
        extractor._call_generation_json = lambda system_prompt, user_message, default: {"events": []}

        result = extractor.extract("这是一段没有事件的纯背景描述。")

        self.assertEqual(result.event_data, {})
        self.assertEqual(result.events_data, [])


if __name__ == "__main__":
    unittest.main()
