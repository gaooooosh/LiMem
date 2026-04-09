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


def _make_openai_chat_response(content: str):
    return types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(content=content),
            )
        ],
    )


def _make_mock_client(side_effect_fn):
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

        pipeline = ContextExtractionPipeline(
            generation_model="test-model",
            llm_client=_make_mock_client(fake_create),
        )
        pipeline.detect_context_candidates = lambda record, event=None: (
            [ContextSpan(text="电量只剩12%", signal="battery", subtype_hint="state", source="record")]
            if "12%" in str(record)
            else [ContextSpan(text="快迟到了", signal="deadline", subtype_hint="constraint", source="record")]
        )
        pipeline.validate_context_drafts = lambda drafts, record_text, event: drafts
        pipeline.canonicalize_context = lambda draft: draft

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
        pipeline = ContextExtractionPipeline(
            generation_model="test-model",
            llm_client=DashScopeClient(
                api_key="test-key",
                base_url="http://test.local",
                generation_model="test-model",
            ),
        )
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

    def test_extract_batch_falls_back_to_single_item_extraction_when_batch_slice_is_missing(self):
        call_count = 0

        def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            payload = {"items": []} if call_count == 1 else {
                "contexts": [
                    {
                        "subtype": "goal",
                        "summary": "单条补救",
                        "structured_slots": {"goal": "ok"},
                        "confidence": 0.9,
                        "evidence_span": "record-0",
                    }
                ]
            }
            return _make_openai_chat_response(json.dumps(payload, ensure_ascii=False))

        pipeline = ContextExtractionPipeline(
            generation_model="test-model",
            llm_client=_make_mock_client(fake_create),
        )
        pipeline.detect_context_candidates = lambda record, event=None: [
            ContextSpan(text=str(record), signal="record", subtype_hint="goal", source="record")
        ]
        pipeline.validate_context_drafts = lambda drafts, record_text, event: drafts
        pipeline.canonicalize_context = lambda draft: draft

        results = pipeline.extract_batch(
            records=["record-0", "record-1"],
            events=[
                Event(id="evt_0", summary="事件0", timestamp=1, last_active=1),
                Event(id="evt_1", summary="事件1", timestamp=2, last_active=2),
            ],
        )

        self.assertEqual(call_count, 3)
        self.assertEqual(results[0][0].summary, "单条补救")
        self.assertEqual(results[1][0].summary, "单条补救")

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
            Event(id="evt_a", summary="播放周杰伦歌曲", action="播放音乐", timestamp=1, last_active=1),
            Event(id="evt_b", summary="继续播放通勤歌单", action="继续播放", timestamp=2, last_active=2),
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
        def fake_embed(text):
            text = str(text or "")
            if any(token in text for token in ["周杰伦", "播放", "音乐"]):
                return [1.0, 0.0, 0.0]
            if "导航" in text:
                return [0.4, 0.6, 0.0]
            if "会议" in text:
                return [0.0, 1.0, 0.0]
            return [0.0, 0.0, 1.0]

        engine._maybe_embed_context = fake_embed

        resolved = engine._resolve_context_pairs_for_event_batch(
            events=events,
            record="用户在车内想听周杰伦",
        )

        self.assertEqual(len(captured_existing_contexts), 1)
        self.assertEqual(captured_existing_contexts[0][0][0]["summary"], "音乐播放场景")
        self.assertEqual(captured_existing_contexts[0][0][0]["subtype"], "environment")
        self.assertEqual(len(captured_existing_contexts[0][0]), 2)
        self.assertEqual(len(resolved), 2)


if __name__ == "__main__":
    unittest.main()
