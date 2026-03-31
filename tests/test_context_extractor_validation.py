# -*- coding: utf-8 -*-
import unittest

from limem.builder.context_extractor import ContextExtractionPipeline
from limem.core.context import ContextDraft, ContextSpan
from limem.core.event import Event


class TestContextExtractorValidation(unittest.TestCase):
    def test_validate_context_drafts_rejects_raw_payload_summary(self):
        pipeline = ContextExtractionPipeline(offline_mode=True)
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

    def test_extract_filters_dialogue_like_context_from_fallback(self):
        pipeline = ContextExtractionPipeline(offline_mode=True)
        dialogue = "车机主动播报：趁着充电这会儿，看部电视剧放松一下吧？"
        pipeline.detect_context_candidates = lambda record, event=None: [
            ContextSpan(
                text=dialogue,
                signal="record",
                subtype_hint="situation",
                source="record",
            )
        ]
        pipeline._looks_like_event_or_result = lambda text, event=None: False
        pipeline._looks_low_reusability_context = lambda summary, evidence_span, strict=True: False

        drafts = pipeline.extract(record=dialogue, event=None)

        self.assertEqual(drafts, [])

    def test_infer_minimum_context_rejects_raw_sensor_summary(self):
        pipeline = ContextExtractionPipeline(offline_mode=True)
        event = Event(
            summary='[车辆状态] 来源: 车辆状态 | {"door_status":"open"}',
            timestamp=1,
            last_active=1,
        )

        inferred = pipeline._infer_minimum_context(event=event, record_text=event.summary)

        self.assertIsNone(inferred)


if __name__ == "__main__":
    unittest.main()
