# -*- coding: utf-8 -*-
import json
import types
import unittest

from limem.builder.context_extractor import ContextExtractionPipeline
from limem.core.context import ContextDraft, ContextSpan
from limem.core.event import Event
from limem.evolution.dynamic_engine import DynamicEvolutionConfig, DynamicEvolutionEngine

import limem.builder.context_extractor as context_extractor_module


class TestContextExtractorBatch(unittest.TestCase):
    def _make_test_pipeline(self):
        pipeline = ContextExtractionPipeline(
            api_key="test-key",
            base_url="http://test.local",
            generation_model="test-model",
            offline_mode=False,
        )
        pipeline.detect_context_candidates = lambda record, event=None: [
            ContextSpan(
                text=str(record),
                signal=event.id if event is not None else "record",
                subtype_hint="state",
                source="record",
            )
        ]
        pipeline.validate_context_drafts = lambda drafts, record_text, event: drafts
        pipeline._rerank_context_drafts = lambda drafts, record_text, event: drafts
        pipeline.canonicalize_context = lambda draft: draft
        pipeline._dedupe_drafts = lambda drafts: drafts
        pipeline._target_context_count = lambda record_text: 1
        pipeline._fallback_extract_contexts = (
            lambda record_text, event, candidate_spans: [
                ContextDraft(
                    subtype="state",
                    summary=f"fallback:{event.id if event is not None else record_text}",
                    structured_slots={"state": event.id if event is not None else "fallback"},
                    evidence_span=str(record_text),
                )
            ]
        )
        return pipeline

    def test_extract_batch_uses_single_llm_call_for_multiple_events(self):
        pipeline = ContextExtractionPipeline(
            api_key="test-key",
            base_url="http://test.local",
            generation_model="test-model",
            offline_mode=False,
        )
        pipeline.detect_context_candidates = lambda record, event=None: (
            [ContextSpan(text="电量只剩12%", signal="battery", subtype_hint="state", source="record")]
            if "12%" in str(record)
            else [ContextSpan(text="快迟到了", signal="deadline", subtype_hint="constraint", source="record")]
        )
        pipeline.validate_context_drafts = lambda drafts, record_text, event: drafts
        pipeline._rerank_context_drafts = lambda drafts, record_text, event: drafts
        pipeline.canonicalize_context = lambda draft: draft
        pipeline._fallback_extract_contexts = lambda record_text, event, candidate_spans: []

        original_generation = context_extractor_module.Generation
        original_dashscope = context_extractor_module.dashscope

        class _FakeGeneration:
            calls = 0

            @staticmethod
            def call(**kwargs):
                _FakeGeneration.calls += 1
                del kwargs
                payload = {
                    "items": [
                        {
                            "item_index": 0,
                            "contexts": [
                                {
                                    "subtype": "state",
                                    "summary": "电量低",
                                    "structured_slots": {"battery_level": 12},
                                    "confidence": 0.91,
                                    "evidence_span": "电量只剩12%",
                                }
                            ],
                        },
                        {
                            "item_index": 1,
                            "contexts": [
                                {
                                    "subtype": "constraint",
                                    "summary": "时间紧张",
                                    "structured_slots": {"deadline": "马上出发"},
                                    "confidence": 0.88,
                                    "evidence_span": "快迟到了",
                                }
                            ],
                        },
                    ]
                }
                return types.SimpleNamespace(
                    status_code=200,
                    output=types.SimpleNamespace(
                        choices=[
                            types.SimpleNamespace(
                                message=types.SimpleNamespace(
                                    content=json.dumps(payload, ensure_ascii=False)
                                )
                            )
                        ]
                    ),
                )

        context_extractor_module.Generation = _FakeGeneration
        context_extractor_module.dashscope = types.SimpleNamespace(
            base_http_api_url="",
            api_key="",
        )
        try:
            results = pipeline.extract_batch(
                records=[
                    "电量只剩12%，用户开始寻找附近充电桩",
                    "快迟到了，系统建议立即出发",
                ],
                events=[
                    Event(summary="用户寻找充电桩", timestamp=1, last_active=1),
                    Event(summary="系统建议立即出发", timestamp=2, last_active=2),
                ],
            )
        finally:
            context_extractor_module.Generation = original_generation
            context_extractor_module.dashscope = original_dashscope

        self.assertEqual(_FakeGeneration.calls, 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0].summary, "电量低")
        self.assertEqual(results[1][0].summary, "时间紧张")

    def test_extract_batch_preserves_successful_slices_when_later_slice_mismatches(self):
        pipeline = self._make_test_pipeline()

        original_generation = context_extractor_module.Generation
        original_dashscope = context_extractor_module.dashscope
        original_batch_size = context_extractor_module.CONTEXT_EXTRACTION_BATCH_SIZE

        class _FakeGeneration:
            calls = 0

            @staticmethod
            def call(**kwargs):
                _FakeGeneration.calls += 1
                del kwargs
                payload = (
                    {
                        "items": [
                            {
                                "item_index": 0,
                                "contexts": [
                                    {
                                        "subtype": "state",
                                        "summary": "批量成功-0",
                                        "structured_slots": {"state": "ok0"},
                                        "confidence": 0.9,
                                        "evidence_span": "record-0",
                                    }
                                ],
                            },
                            {
                                "item_index": 1,
                                "contexts": [
                                    {
                                        "subtype": "state",
                                        "summary": "批量成功-1",
                                        "structured_slots": {"state": "ok1"},
                                        "confidence": 0.9,
                                        "evidence_span": "record-1",
                                    }
                                ],
                            },
                        ]
                    }
                    if _FakeGeneration.calls == 1
                    else {"items": []}
                )
                return types.SimpleNamespace(
                    status_code=200,
                    output=types.SimpleNamespace(
                        choices=[
                            types.SimpleNamespace(
                                message=types.SimpleNamespace(
                                    content=json.dumps(payload, ensure_ascii=False)
                                )
                            )
                        ]
                    ),
                )

        context_extractor_module.Generation = _FakeGeneration
        context_extractor_module.dashscope = types.SimpleNamespace(
            base_http_api_url="",
            api_key="",
        )
        context_extractor_module.CONTEXT_EXTRACTION_BATCH_SIZE = 2
        try:
            results = pipeline.extract_batch(
                records=["record-0", "record-1", "record-2"],
                events=[
                    Event(id="evt_0", summary="事件0", timestamp=1, last_active=1),
                    Event(id="evt_1", summary="事件1", timestamp=2, last_active=2),
                    Event(id="evt_2", summary="事件2", timestamp=3, last_active=3),
                ],
            )
        finally:
            context_extractor_module.Generation = original_generation
            context_extractor_module.dashscope = original_dashscope
            context_extractor_module.CONTEXT_EXTRACTION_BATCH_SIZE = original_batch_size

        self.assertEqual(_FakeGeneration.calls, 2)
        self.assertEqual([drafts[0].summary for drafts in results], ["批量成功-0", "批量成功-1", "fallback:evt_2"])

    def test_extract_batch_skips_invalid_item_index_without_aborting_the_batch(self):
        pipeline = self._make_test_pipeline()

        original_generation = context_extractor_module.Generation
        original_dashscope = context_extractor_module.dashscope

        class _FakeGeneration:
            @staticmethod
            def call(**kwargs):
                del kwargs
                payload = {
                    "items": [
                        {
                            "item_index": "two",
                            "contexts": [
                                {
                                    "subtype": "state",
                                    "summary": "坏索引",
                                    "structured_slots": {"state": "bad"},
                                    "confidence": 0.2,
                                    "evidence_span": "record-0",
                                }
                            ],
                        },
                        {
                            "item_index": 1,
                            "contexts": [
                                {
                                    "subtype": "goal",
                                    "summary": "批量成功-1",
                                    "structured_slots": {"goal": "ok1"},
                                    "confidence": 0.91,
                                    "evidence_span": "record-1",
                                }
                            ],
                        },
                    ]
                }
                return types.SimpleNamespace(
                    status_code=200,
                    output=types.SimpleNamespace(
                        choices=[
                            types.SimpleNamespace(
                                message=types.SimpleNamespace(
                                    content=json.dumps(payload, ensure_ascii=False)
                                )
                            )
                        ]
                    ),
                )

        context_extractor_module.Generation = _FakeGeneration
        context_extractor_module.dashscope = types.SimpleNamespace(
            base_http_api_url="",
            api_key="",
        )
        try:
            results = pipeline.extract_batch(
                records=["record-0", "record-1"],
                events=[
                    Event(id="evt_0", summary="事件0", timestamp=1, last_active=1),
                    Event(id="evt_1", summary="事件1", timestamp=2, last_active=2),
                ],
            )
        finally:
            context_extractor_module.Generation = original_generation
            context_extractor_module.dashscope = original_dashscope

        self.assertEqual([drafts[0].summary for drafts in results], ["fallback:evt_0", "批量成功-1"])

    def test_dynamic_engine_prefers_batch_context_extraction(self):
        engine = DynamicEvolutionEngine(
            store=types.SimpleNamespace(),
            config=DynamicEvolutionConfig(context_extraction_batch_size=4),
        )
        event_a = Event(
            id="evt_a",
            summary="用户寻找充电桩",
            timestamp=1,
            last_active=1,
            payload={"episode_text": "电量只剩12%，用户寻找附近充电桩"},
        )
        event_b = Event(
            id="evt_b",
            summary="系统建议立即出发",
            timestamp=2,
            last_active=2,
            payload={"episode_text": "快迟到了，系统建议立即出发"},
        )
        draft = ContextDraft(
            subtype="state",
            summary="当前状态",
            structured_slots={"state": "当前状态"},
            evidence_span="当前状态",
        )
        captured_batches: list[tuple[list[str], list[str]]] = []

        def fake_extract_batch(records, events):
            captured_batches.append(
                (
                    [str(record) for record in records],
                    [event.id for event in events],
                )
            )
            return [[draft], [draft]]

        engine.context_extractor.extract_batch = fake_extract_batch
        engine.extract_context_drafts = lambda *args, **kwargs: self.fail(
            "single-event context extraction should not run when batch path succeeds"
        )
        engine.resolve_context = lambda draft, event=None: {
            "event_id": event.id if event else "",
            "summary": draft.summary,
        }

        resolved = engine._resolve_context_pairs_for_event_batch(
            events=[event_a, event_b],
            record="共享 episode 文本",
        )

        self.assertEqual(len(captured_batches), 1)
        self.assertEqual(captured_batches[0][0], ["共享 episode 文本", "共享 episode 文本"])
        self.assertEqual(captured_batches[0][1], ["evt_a", "evt_b"])
        self.assertEqual(len(resolved), 2)
        self.assertEqual(resolved[0][0][0]["event_id"], "evt_a")
        self.assertEqual(resolved[1][0][0]["event_id"], "evt_b")

    def test_dynamic_engine_logs_and_falls_back_only_for_failed_batch_slice(self):
        engine = DynamicEvolutionEngine(
            store=types.SimpleNamespace(),
            config=DynamicEvolutionConfig(context_extraction_batch_size=2, llm_concurrency=1),
        )
        events = [
            Event(id="evt_a", summary="事件A", timestamp=1, last_active=1),
            Event(id="evt_b", summary="事件B", timestamp=2, last_active=2),
            Event(id="evt_c", summary="事件C", timestamp=3, last_active=3),
        ]
        batch_draft_a = ContextDraft(
            subtype="state",
            summary="batch-a",
            structured_slots={"state": "a"},
            evidence_span="batch-a",
        )
        batch_draft_b = ContextDraft(
            subtype="state",
            summary="batch-b",
            structured_slots={"state": "b"},
            evidence_span="batch-b",
        )
        single_draft_c = ContextDraft(
            subtype="state",
            summary="single-c",
            structured_slots={"state": "c"},
            evidence_span="single-c",
        )
        captured_batches: list[list[str]] = []
        single_calls: list[str] = []

        def fake_extract_batch(records, events):
            del records
            captured_batches.append([event.id for event in events])
            if events[0].id == "evt_c":
                raise RuntimeError("batch boom")
            return [[batch_draft_a], [batch_draft_b]]

        engine.context_extractor.extract_batch = fake_extract_batch
        engine.extract_context_drafts = lambda event, record=None: (
            single_calls.append(event.id) or [single_draft_c]
        )
        engine.resolve_context = lambda draft, event=None: {
            "event_id": event.id if event else "",
            "summary": draft.summary,
        }

        with self.assertLogs("limem.evolution.dynamic_engine", level="WARNING") as captured_logs:
            resolved = engine._resolve_context_pairs_for_event_batch(
                events=events,
                record="共享 episode 文本",
            )

        self.assertEqual(captured_batches, [["evt_a", "evt_b"], ["evt_c"]])
        self.assertEqual(single_calls, ["evt_c"])
        self.assertEqual(
            [item[0][0]["summary"] for item in resolved],
            ["batch-a", "batch-b", "single-c"],
        )
        self.assertTrue(
            any("falling back to per-event extraction for this slice" in line for line in captured_logs.output)
        )


if __name__ == "__main__":
    unittest.main()
