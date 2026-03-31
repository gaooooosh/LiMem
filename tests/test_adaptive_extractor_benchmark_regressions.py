# -*- coding: utf-8 -*-
import unittest

from limem.builder.extractor import ExtractionResult
from limem.builder.input_classifier import InputClassifier, StructureLevel
from limem.builder.semi_structured_extractor import SemiStructuredExtractor
from limem.builder.structured_mapper import StructuredFieldMapper


class _FallbackExtractor:
    def __init__(self):
        self.called = False

    def extract(self, text):
        del text
        self.called = True
        return ExtractionResult(
            event_data={"summary": "Fallback event", "action": "fallback action", "causality": ""},
            events_data=[{"summary": "Fallback event", "action": "fallback action", "causality": ""}],
            entities=[],
            confidence=0.4,
        )


class TestAdaptiveExtractorBenchmarkRegressions(unittest.TestCase):
    def test_structured_mapper_extracts_calendar_event_from_cal_evt(self):
        mapper = StructuredFieldMapper()

        result = mapper.extract(
            {"cal_evt": "里程碑同步会", "cal_src": "teams", "cal_start": "2026-03-12 18:48:24"},
            source_text="",
        )

        self.assertEqual(result.event_data["summary"], "里程碑同步会")
        self.assertEqual(result.event_data["action"], "里程碑同步会")

    def test_structured_mapper_ignores_none_calendar_placeholders(self):
        mapper = StructuredFieldMapper()

        result = mapper.extract(
            {"cal_evt": "none", "cal_start": "none", "cal_end": "none", "cal_src": "none"},
            source_text="",
        )

        self.assertEqual(result.event_data, {})
        self.assertEqual(result.events_data, [])

    def test_input_classifier_detects_embedded_json_blob(self):
        classifier = InputClassifier()

        text = (
            '[环境感知数据] 来源: 环境感知 | {"source":"环境感知","payload":'
            '{"weather":{"condition":"sunny"},"spatial":{"geo_type":"urban"}}}'
        )
        result = classifier.classify(text)

        self.assertEqual(result.level, StructureLevel.STRUCTURED)
        self.assertEqual(result.parsed_json["payload"]["weather"]["condition"], "sunny")

    def test_semi_structured_extractor_extracts_media_narrative_without_fallback(self):
        fallback = _FallbackExtractor()
        extractor = SemiStructuredExtractor(fallback_extractor=fallback)

        result = extractor.extract(
            "2026-03-12 下午5点半左右,在QQ音乐播放新裤子的歌曲《你要跳舞吗》",
            detected_patterns=("timestamp", "kv"),
        )

        self.assertFalse(fallback.called)
        self.assertEqual(len(result.events_data), 1)
        self.assertIn("播放", result.event_data["action"])
        self.assertEqual(result.entities, [])

    def test_semi_structured_extractor_extracts_navigation_narrative_without_fallback(self):
        fallback = _FallbackExtractor()
        extractor = SemiStructuredExtractor(fallback_extractor=fallback)

        result = extractor.extract(
            "2026-03-12 下午5点半左右,用户发起导航,从当前位置导航到XX健身房,用时8分钟",
            detected_patterns=("timestamp", "kv"),
        )

        self.assertFalse(fallback.called)
        self.assertEqual(len(result.events_data), 1)
        self.assertIn("导航到XX健身房", result.event_data["action"])
        self.assertEqual(result.entities, [])

    def test_semi_structured_extractor_skips_fallback_for_non_actionable_noise(self):
        fallback = _FallbackExtractor()
        extractor = SemiStructuredExtractor(fallback_extractor=fallback)

        result = extractor.extract(
            '[环境感知数据] 来源: 环境感知 | {"source":"环境感知","payload":'
            '{"cabin_env":{"noise_db":45},"weather":{"condition":"sunny"}}}',
            detected_patterns=("kv",),
        )

        self.assertFalse(fallback.called)
        self.assertEqual(result.event_data, {})
        self.assertEqual(result.events_data, [])

    def test_semi_structured_extractor_keeps_fallback_for_actionable_text(self):
        fallback = _FallbackExtractor()
        extractor = SemiStructuredExtractor(fallback_extractor=fallback)

        result = extractor.extract(
            "2026-03-12 Alice started onboarding workflow",
            detected_patterns=("timestamp", "kv"),
        )

        self.assertTrue(fallback.called)
        self.assertEqual(result.event_data["summary"], "Fallback event")


if __name__ == "__main__":
    unittest.main()
