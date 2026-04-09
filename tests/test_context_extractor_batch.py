# -*- coding: utf-8 -*-
import json
import types
import unittest
from unittest.mock import MagicMock

from limem.builder.context_extractor import ContextExtractionPipeline
from limem.core.context import Context, ContextDraft, ContextSpan
from limem.core.event import Event
from limem.evolution.dynamic_engine import DynamicEvolutionConfig, DynamicEvolutionEngine
from limem.llm import DashScopeClient

import limem.builder.context_extractor as context_extractor_module


def _make_openai_chat_response(content: str):
    """Create a mock OpenAI ChatCompletion response."""
    return types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(content=content),
            )
        ],
    )


def _make_mock_client(side_effect_fn):
    """Create a DashScopeClient with a mocked OpenAI client."""
    client = DashScopeClient(
        api_key="test-key",
        base_url="http://test.local",
        generation_model="test-model",
    )
    mock_openai = MagicMock()
    mock_openai.chat.completions.create.side_effect = side_effect_fn
    client._openai_client = mock_openai
    return client


class TestContextExtractorBatch(unittest.TestCase):
    def _make_test_pipeline(self, call_side_effect=None):
        if call_side_effect is not None:
            client = _make_mock_client(call_side_effect)
        else:
            client = DashScopeClient(
                api_key="test-key",
                base_url="http://test.local",
                generation_model="test-model",
            )
        pipeline = ContextExtractionPipeline(
            generation_model="test-model",
            offline_mode=False,
            llm_client=client,
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
        call_count = 0

        def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
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
            return _make_openai_chat_response(json.dumps(payload, ensure_ascii=False))

        client = _make_mock_client(fake_create)
        pipeline = ContextExtractionPipeline(
            generation_model="test-model",
            offline_mode=False,
            llm_client=client,
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

        self.assertEqual(call_count, 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][0].summary, "电量低")
        self.assertEqual(results[1][0].summary, "时间紧张")

    def test_build_context_messages_include_existing_contexts_when_provided(self):
        pipeline = self._make_test_pipeline()
        event = Event(
            id="evt_music",
            summary="用户想听周杰伦",
            timestamp=1,
            last_active=1,
        )
        prepared = pipeline._prepare_context_request(
            record="用户让我放歌",
            event=event,
            existing_contexts=[
                {"summary": "音乐播放场景", "subtype": "environment"},
            ],
        )

        user_message = pipeline._build_context_user_message(
            record_text=prepared.record_text,
            event=prepared.event,
            candidate_spans=prepared.candidate_spans,
            existing_contexts=prepared.existing_contexts,
        )
        batch_message = pipeline._build_context_batch_user_message([prepared])

        self.assertIn("可复用已有 Context", user_message)
        self.assertIn('"summary":', user_message)
        self.assertIn("\\u97f3\\u4e50\\u64ad\\u653e\\u573a\\u666f", user_message)
        self.assertIn("保持字面完全一致", user_message)
        self.assertIn('"existing_contexts": [', batch_message)
        self.assertIn("item 中可能包含 existing_contexts", batch_message)

    def test_extract_batch_preserves_successful_slices_when_later_slice_mismatches(self):
        call_count = 0

        def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
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
                if call_count == 1
                else {"items": []}
            )
            return _make_openai_chat_response(json.dumps(payload, ensure_ascii=False))

        original_batch_size = context_extractor_module.CONTEXT_EXTRACTION_BATCH_SIZE
        context_extractor_module.CONTEXT_EXTRACTION_BATCH_SIZE = 2
        try:
            pipeline = self._make_test_pipeline(call_side_effect=fake_create)
            results = pipeline.extract_batch(
                records=["record-0", "record-1", "record-2"],
                events=[
                    Event(id="evt_0", summary="事件0", timestamp=1, last_active=1),
                    Event(id="evt_1", summary="事件1", timestamp=2, last_active=2),
                    Event(id="evt_2", summary="事件2", timestamp=3, last_active=3),
                ],
            )
        finally:
            context_extractor_module.CONTEXT_EXTRACTION_BATCH_SIZE = original_batch_size

        self.assertEqual(call_count, 2)
        self.assertEqual([drafts[0].summary for drafts in results], ["批量成功-0", "批量成功-1", "fallback:evt_2"])

    def test_extract_batch_skips_invalid_item_index_without_aborting_the_batch(self):
        def fake_create(**kwargs):
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
            return _make_openai_chat_response(json.dumps(payload, ensure_ascii=False))

        pipeline = self._make_test_pipeline(call_side_effect=fake_create)
        results = pipeline.extract_batch(
            records=["record-0", "record-1"],
            events=[
                Event(id="evt_0", summary="事件0", timestamp=1, last_active=1),
                Event(id="evt_1", summary="事件1", timestamp=2, last_active=2),
            ],
        )

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

        def fake_extract_batch(records, events, existing_contexts_by_index=None):
            del existing_contexts_by_index
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

    def test_dynamic_engine_passes_ranked_existing_contexts_to_batch_extraction(self):
        class _Store:
            def __init__(self):
                self._contexts = {
                    "ctx_music": Context(
                        id="ctx_music",
                        subtype="environment",
                        summary="音乐播放场景",
                    ),
                    "ctx_meeting": Context(
                        id="ctx_meeting",
                        subtype="situation",
                        summary="会议场景",
                    ),
                    "ctx_nav": Context(
                        id="ctx_nav",
                        subtype="situation",
                        summary="出行导航场景",
                    ),
                }

            def find_contexts_summary_index(self, context_type, only_active=True):
                del context_type, only_active
                return [
                    ("ctx_meeting", "会议场景"),
                    ("ctx_music", "音乐播放场景"),
                    ("ctx_nav", "出行导航场景"),
                ]

            def get_context(self, context_id):
                return self._contexts.get(context_id)

        engine = DynamicEvolutionEngine(
            store=_Store(),
            config=DynamicEvolutionConfig(
                context_extraction_batch_size=4,
                context_aware_extraction_limit=2,
            ),
        )
        events = [
            Event(
                id="evt_a",
                summary="播放周杰伦歌曲",
                action="播放音乐",
                timestamp=1,
                last_active=1,
            ),
            Event(
                id="evt_b",
                summary="继续播放通勤歌单",
                action="继续播放",
                timestamp=2,
                last_active=2,
            ),
        ]
        draft = ContextDraft(
            subtype="environment",
            summary="音乐播放场景",
            structured_slots={"environment": "音乐播放场景"},
            evidence_span="放歌",
        )
        captured_existing_contexts = []

        def fake_extract_batch(records, events, existing_contexts_by_index=None):
            del records, events
            captured_existing_contexts.append(existing_contexts_by_index)
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
            events=events,
            record="用户在车内想听周杰伦",
        )

        self.assertEqual(len(captured_existing_contexts), 1)
        self.assertEqual(captured_existing_contexts[0][0][0]["summary"], "音乐播放场景")
        self.assertEqual(captured_existing_contexts[0][0][0]["subtype"], "environment")
        self.assertEqual(len(captured_existing_contexts[0][0]), 2)
        self.assertEqual(len(resolved), 2)

    def test_dynamic_engine_passes_existing_contexts_to_single_event_fallback(self):
        class _Store:
            def __init__(self):
                self._contexts = {
                    "ctx_music": Context(
                        id="ctx_music",
                        subtype="environment",
                        summary="音乐播放场景",
                    ),
                    "ctx_meeting": Context(
                        id="ctx_meeting",
                        subtype="situation",
                        summary="会议场景",
                    ),
                }

            def find_contexts_summary_index(self, context_type, only_active=True):
                del context_type, only_active
                return [
                    ("ctx_meeting", "会议场景"),
                    ("ctx_music", "音乐播放场景"),
                ]

            def get_context(self, context_id):
                return self._contexts.get(context_id)

        engine = DynamicEvolutionEngine(
            store=_Store(),
            config=DynamicEvolutionConfig(
                context_extraction_batch_size=4,
                context_aware_extraction_limit=2,
                llm_concurrency=1,
            ),
        )
        events = [
            Event(
                id="evt_a",
                summary="播放周杰伦歌曲",
                action="播放音乐",
                timestamp=1,
                last_active=1,
            ),
            Event(
                id="evt_b",
                summary="继续播放通勤歌单",
                action="继续播放",
                timestamp=2,
                last_active=2,
            ),
        ]
        draft = ContextDraft(
            subtype="environment",
            summary="音乐播放场景",
            structured_slots={"environment": "音乐播放场景"},
            evidence_span="放歌",
        )
        captured_single_calls = []

        def fake_extract_batch(records, events, existing_contexts_by_index=None):
            del records, events, existing_contexts_by_index
            raise RuntimeError("batch boom")

        def fake_extract_context_drafts(event, record=None, existing_contexts=None):
            captured_single_calls.append((event.id, record, existing_contexts))
            return [draft]

        engine.context_extractor.extract_batch = fake_extract_batch
        engine.extract_context_drafts = fake_extract_context_drafts
        engine.resolve_context = lambda draft, event=None: {
            "event_id": event.id if event else "",
            "summary": draft.summary,
        }

        resolved = engine._resolve_context_pairs_for_event_batch(
            events=events,
            record="用户在车内想听周杰伦",
        )

        self.assertEqual([item[0] for item in captured_single_calls], ["evt_a", "evt_b"])
        self.assertEqual(captured_single_calls[0][2][0]["summary"], "音乐播放场景")
        self.assertEqual(captured_single_calls[0][2][0]["subtype"], "environment")
        self.assertEqual(len(resolved), 2)

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

        def fake_extract_batch(records, events, existing_contexts_by_index=None):
            del records, existing_contexts_by_index
            captured_batches.append([event.id for event in events])
            if events[0].id == "evt_c":
                raise RuntimeError("batch boom")
            return [[batch_draft_a], [batch_draft_b]]

        engine.context_extractor.extract_batch = fake_extract_batch
        engine.extract_context_drafts = lambda event, record=None, existing_contexts=None: (
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
