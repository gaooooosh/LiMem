# -*- coding: utf-8 -*-
import json
import types
import unittest
from unittest.mock import MagicMock

from limem.builder.context_extractor import ContextExtractionPipeline
from limem.core.context import Context, ContextDraft
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
                                "subtype": "constraint",
                                "summary": "电量低",
                                "description": "设备当前电量仅剩12%，用户正在寻找附近充电桩",
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
                                "description": "用户面临即将迟到的时间压力，需要马上出发",
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
            existing_contexts=prepared.existing_contexts,
        )
        batch_message = pipeline._build_context_batch_user_message([prepared])

        self.assertIn("你记忆中的已有情境条件", user_message)
        self.assertIn('"summary":', user_message)
        self.assertIn("\\u97f3\\u4e50\\u64ad\\u653e\\u573a\\u666f", user_message)
        self.assertIn("保持字面完全一致", user_message)
        self.assertIn('"existing_contexts": [', batch_message)
        self.assertIn("item 中可能包含你记忆里的 existing_contexts", batch_message)

    def test_extract_batch_falls_back_to_single_item_extraction_when_batch_slice_is_missing(self):
        call_count = 0

        def fake_create(**kwargs):
            nonlocal call_count
            call_count += 1
            payload = {"items": []} if call_count == 1 else {
                "contexts": [
                    {
                        "subtype": "situation",
                        "summary": "单条补救",
                        "description": "单条回退路径返回的测试描述",
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
            subtype="situation",
            summary="当前状态",
            description="事件当前所处的状态描述",
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

    def test_dynamic_engine_does_not_pass_existing_contexts_to_batch_extraction(self):
        engine = DynamicEvolutionEngine(
            store=MagicMock(),
            config=DynamicEvolutionConfig(
                context_extraction_batch_size=4,
            ),
        )
        events = [
            Event(id="evt_a", summary="播放周杰伦歌曲", action="播放音乐", timestamp=1, last_active=1),
            Event(id="evt_b", summary="继续播放通勤歌单", action="继续播放", timestamp=2, last_active=2),
        ]
        draft = ContextDraft(
            subtype="environment",
            summary="音乐播放场景",
            description="用户在车内进行音乐播放，当前为娱乐交互环境",
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
        self.assertIsNone(captured_existing_contexts[0])
        self.assertEqual(len(resolved), 2)

    def test_dynamic_engine_reuses_existing_context_only_after_grounded_extraction(self):
        class _Store:
            def __init__(self):
                self.updated = []
                self.context = Context(
                    id="ctx_low_battery",
                    subtype="situation",
                    summary="电量低",
                    description="设备当前电量偏低，需要及时充电",
                    source_refs=[{"evidence_span": "电量只剩12%"}],
                    status="active",
                    support_count=1,
                    last_seen_at=1,
                )

            def find_context_candidates(self, context_type, subtype="", limit=20, only_active=True):
                del context_type, subtype, limit, only_active
                return [self.context]

            def find_contexts_summary_index(self, context_type, only_active=True):
                del context_type, only_active
                return [(self.context.id, self.context.summary)]

            def get_context(self, context_id):
                return self.context if context_id == self.context.id else None

            def update_context(self, context):
                self.updated.append(context.id)

            def save_context(self, context):
                self.context = context

        store = _Store()
        engine = DynamicEvolutionEngine(store=store)
        event = Event(
            id="evt_low_battery",
            summary="系统提示电量低",
            action="提示充电",
            timestamp=100,
            last_active=100,
            valid_from=100,
            payload={"episode_text": "电量只剩12%，系统提示尽快充电"},
        )
        draft = ContextDraft(
            subtype="situation",
            summary="电量低",
            description="设备当前电量只剩12%",
            evidence_span="电量只剩12%",
            valid_from=100,
        )
        captured_existing_contexts = []

        def fake_extract(record, event=None, existing_contexts=None):
            del record, event
            captured_existing_contexts.append(existing_contexts)
            return [draft]

        engine.context_extractor.extract = fake_extract
        engine._maybe_embed_context = lambda _text: []

        resolved = engine.resolve_context_pairs(event)

        self.assertEqual(captured_existing_contexts, [None])
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0][0].id, "ctx_low_battery")
        self.assertEqual(store.updated, ["ctx_low_battery"])


if __name__ == "__main__":
    unittest.main()
