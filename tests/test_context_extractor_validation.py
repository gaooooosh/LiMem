# -*- coding: utf-8 -*-
import unittest

from limem.builder.context_extractor import ContextExtractionPipeline
from limem.core.context import ContextDraft


class TestContextExtractorValidation(unittest.TestCase):
    def test_validate_context_drafts_rejects_raw_payload_summary(self):
        pipeline = ContextExtractionPipeline()
        raw_summary = '[日程数据] 来源: 日程数据 | {"cal_evt": "固定通勤用车"}'

        validated = pipeline.validate_context_drafts(
            [
                ContextDraft(
                    subtype="situation",
                    summary=raw_summary,
                    description=raw_summary,
                    evidence_span=raw_summary,
                )
            ],
            record_text=raw_summary,
            event=None,
        )

        self.assertEqual(validated, [])

    def test_validate_context_drafts_maps_legacy_emotion_alias_to_situation(self):
        pipeline = ContextExtractionPipeline()

        validated = pipeline.validate_context_drafts(
            [
                ContextDraft(
                    subtype="feeling",
                    summary="用户心情低落",
                    description="用户在语音交互中表达今天心情不太好，希望听轻松音乐调节情绪",
                    evidence_span="今天心情不太好，放点轻松的音乐吧",
                )
            ],
            record_text="今天心情不太好，放点轻松的音乐吧",
            event=None,
        )

        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0].subtype, "situation")
        self.assertIn("轻松音乐", validated[0].description)

    def test_validate_context_drafts_rejects_current_intent(self):
        pipeline = ContextExtractionPipeline()

        validated = pipeline.validate_context_drafts(
            [
                ContextDraft(
                    subtype="situation",
                    summary="用户想找酒店",
                    description="用户当前希望找一家机场附近酒店",
                    evidence_span="想找个便宜点的机场附近酒店",
                )
            ],
            record_text="预算不太够，所以想找个便宜点的机场附近酒店",
            event=None,
        )

        self.assertEqual(validated, [])

    def test_validate_context_drafts_accepts_subject_specific_backgrounds(self):
        pipeline = ContextExtractionPipeline()

        validated = pipeline.validate_context_drafts(
            [
                ContextDraft(
                    subtype="constraint",
                    summary="用户旅行预算受限",
                    description="Agent 观察到用户在旅行住宿安排中受到预算限制",
                    evidence_span="预算也不太够",
                ),
                ContextDraft(
                    subtype="environment",
                    summary="车机弱网环境",
                    description="Agent 观察到车机系统处于网络不稳定的运行环境",
                    evidence_span="车机网络信号弱",
                ),
            ],
            record_text="用户说预算也不太够；车机网络信号弱。",
            event=None,
        )

        self.assertEqual([item.subtype for item in validated], ["constraint", "environment"])
        self.assertEqual(validated[0].summary, "用户旅行预算受限")
        self.assertEqual(validated[1].summary, "车机弱网环境")

    def test_summary_and_description_length_limits_follow_new_schema(self):
        pipeline = ContextExtractionPipeline()
        overlong_summary = "场" * 129
        overlong_description = "描" * 513

        validated = pipeline.validate_context_drafts(
            [
                ContextDraft(
                    subtype="situation",
                    summary=overlong_summary,
                    description=overlong_description,
                    evidence_span="会议讨论Q2目标",
                )
            ],
            record_text="会议讨论Q2目标",
            event=None,
        )

        self.assertEqual(validated, [])

    def test_extract_returns_empty_when_llm_produces_no_contexts(self):
        pipeline = ContextExtractionPipeline()
        pipeline._call_context_llm_json = lambda _msg: {}

        drafts = pipeline.extract(record="车机主动播报：趁着充电这会儿，看部电视剧放松一下吧？")

        self.assertEqual(drafts, [])


if __name__ == "__main__":
    unittest.main()
