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
                        ],
                    }
                ]
            }

        extractor._call_generation_json = fake_call_generation_json

        result = extractor.extract("用户说：导航去公司，车机回答：已开始导航。")

        self.assertEqual(len(result.events_data), 1)
        self.assertEqual(result.events_data[0]["summary"], "用户请求导航去公司，系统开始导航")
        self.assertEqual(result.events_data[0]["participants"], [{"role": "用户", "seat": ""}])
        self.assertEqual(len(result.events_data[0]["contexts"]), 1)
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
        self.assertEqual(result.orphan_contexts, [])

    def test_orphan_contexts_are_passthrough_without_generating_events(self):
        extractor = _make_extractor()

        extractor._call_generation_json = lambda system_prompt, user_message, default: {
            "events": [],
            "orphan_contexts": [
                {
                    "subtype": "environment",
                    "summary": "办公场所环境",
                    "evidence_span": "位置=某办公楼",
                    "confidence": 0.82,
                },
                {
                    "subtype": "situation",
                    "summary": "",
                    "evidence_span": "噪音34dB",
                    "confidence": 0.6,
                },
                "invalid",
            ],
        }

        result = extractor.extract("环境数据：温度28℃，噪音34dB，位置=某办公楼。")

        self.assertEqual(result.event_data, {})
        self.assertEqual(result.events_data, [])
        self.assertEqual(
            result.orphan_contexts,
            [
                {
                    "subtype": "environment",
                    "summary": "办公场所环境",
                    "condition": "办公场所环境",
                    "subject": "",
                    "facts": {},
                    "applies_when": "",
                    "evidence_span": "位置=某办公楼",
                    "confidence": 0.82,
                }
            ],
        )

    def test_context_payload_preserves_condition_and_free_facts(self):
        extractor = _make_extractor()

        extractor._call_generation_json = lambda system_prompt, user_message, default: {
            "events": [
                {
                    "summary": "用户在高温车内要求调低空调",
                    "participants": ["用户"],
                    "action": "要求调低空调",
                    "contexts": [
                        {
                            "subtype": "environment",
                            "subject": "用户",
                            "condition": "用户处于高温车内出行环境",
                            "facts": {
                                "气温": "38度",
                                "位置": "车内",
                                "时间": "下午",
                            },
                            "applies_when": "用户进行车内舒适度相关交互",
                            "evidence_span": "气温38度，用户在车内要求调低空调",
                            "confidence": 0.9,
                        }
                    ],
                }
            ],
            "orphan_contexts": [],
        }

        result = extractor.extract("气温38度，用户在车内要求调低空调。")

        context = result.events_data[0]["contexts"][0]
        self.assertEqual(context["summary"], "用户处于高温车内出行环境")
        self.assertEqual(context["condition"], "用户处于高温车内出行环境")
        self.assertEqual(context["facts"]["气温"], "38度")
        self.assertEqual(context["applies_when"], "用户进行车内舒适度相关交互")

    def test_information_state_change_is_preserved_as_event_payload(self):
        extractor = _make_extractor()

        extractor._call_generation_json = lambda system_prompt, user_message, default: {
            "events": [
                {
                    "summary": "项目记忆库新增影响大功能更新交付标准的规则",
                    "participants": ["项目记忆库", "LiServer 项目"],
                    "action": "记录项目级规则",
                    "causality": "该规则会影响后续大功能更新完成后的验证和部署行为",
                    "scope": "project:LiServer",
                    "source": "外部记忆写入工具",
                    "memory_type": "rule",
                    "rule_text": "每次完成一个大的功能更新后，都要部署上线并做端到端测试。",
                    "payload": {
                        "principal": "principal_project",
                        "canonical": ["大的功能更新", "部署上线", "端到端测试"],
                    },
                    "evidence": [
                        {
                            "source": "episode",
                            "snippet": "记录 rule 类型记忆：每次完成一个大的功能更新后，都要部署上线并做端到端测试。",
                            "confidence": 0.95,
                        }
                    ],
                }
            ],
            "orphan_contexts": [],
        }

        result = extractor.extract("外部系统记录了一条会影响项目后续执行的规则。")

        self.assertEqual(len(result.events_data), 1)
        event = result.events_data[0]
        self.assertEqual(event["participants"], [{"role": "项目记忆库", "seat": ""}, {"role": "LiServer 项目", "seat": ""}])
        self.assertEqual(event["action"], "记录项目级规则")
        self.assertEqual(event["scope"], "project:LiServer")
        self.assertEqual(event["memory_type"], "rule")
        self.assertEqual(event["rule_text"], "每次完成一个大的功能更新后，都要部署上线并做端到端测试。")
        self.assertEqual(event["payload"]["principal"], "principal_project")

    def test_orphan_contexts_are_preserved_alongside_valid_events(self):
        extractor = _make_extractor()

        extractor._call_generation_json = lambda system_prompt, user_message, default: {
            "events": [
                {
                    "summary": "用户查询天气，系统返回天气预报",
                    "participants": [{"role": "用户"}],
                    "action": "查询天气",
                    "time": {"text": "明天"},
                    "causality": "",
                }
            ],
            "orphan_contexts": [
                {
                    "subtype": "environment",
                    "summary": "用户户外出行环境",
                    "evidence_span": "明天要去户外徒步",
                    "confidence": 0.9,
                }
            ],
        }

        result = extractor.extract("用户说：明天要去户外徒步，帮我查一下明天的天气。")

        self.assertEqual(len(result.events_data), 1)
        self.assertEqual(result.events_data[0]["action"], "查询天气")
        self.assertEqual(result.orphan_contexts[0]["summary"], "用户户外出行环境")

    def test_llm_failure_returns_empty_result(self):
        extractor = _make_extractor()

        def fake_call_generation_json(system_prompt, user_message, default):
            raise RuntimeError("dashscope timeout")

        extractor._call_generation_json = fake_call_generation_json

        result = extractor.extract("用户说：导航去公司")

        self.assertEqual(result.event_data, {})
        self.assertEqual(result.events_data, [])
        self.assertEqual(result.entities, [])
        self.assertEqual(result.orphan_contexts, [])

    def test_empty_llm_output_returns_no_events(self):
        extractor = _make_extractor()
        extractor._call_generation_json = lambda system_prompt, user_message, default: {"events": []}

        result = extractor.extract("这是一段没有事件的纯背景描述。")

        self.assertEqual(result.event_data, {})
        self.assertEqual(result.events_data, [])
        self.assertEqual(result.orphan_contexts, [])


if __name__ == "__main__":
    unittest.main()
