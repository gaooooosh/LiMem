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
                    subtype="state",
                    summary=raw_summary,
                    structured_slots={"state": raw_summary},
                    evidence_span=raw_summary,
                )
            ],
            record_text=raw_summary,
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
