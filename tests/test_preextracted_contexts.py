# -*- coding: utf-8 -*-
import types
import unittest

from limem.core.context import ContextDraft
from limem.core.event import Event
from limem.evolution.dynamic_engine import DynamicEvolutionConfig, DynamicEvolutionEngine


class TestPreExtractedContexts(unittest.TestCase):
    def test_events_with_payload_contexts_skip_llm_in_mixed_batch(self):
        engine = DynamicEvolutionEngine(
            store=types.SimpleNamespace(),
            config=DynamicEvolutionConfig(context_extraction_batch_size=4),
        )
        event_with_context = Event(
            id="evt_with",
            summary="用户在车内播放音乐",
            action="播放音乐",
            timestamp=1,
            last_active=1,
            valid_from=1,
            payload={
                "episode_text": "用户在车内播放音乐",
                "contexts": [
                    {
                        "subtype": "environment",
                        "summary": "车内音乐播放场景",
                        "evidence_span": "在车内播放音乐",
                        "confidence": 0.9,
                    }
                ],
            },
        )
        event_missing_context_a = Event(
            id="evt_missing_a",
            summary="系统建议立即出发",
            action="建议立即出发",
            timestamp=2,
            last_active=2,
            valid_from=2,
            payload={"episode_text": "快迟到了，系统建议立即出发"},
        )
        event_missing_context_b = Event(
            id="evt_missing_b",
            summary="系统提示电量偏低",
            action="提示充电",
            timestamp=3,
            last_active=3,
            valid_from=3,
            payload={"episode_text": "电量只剩12%，系统提示尽快充电"},
        )
        captured_event_ids = []
        llm_draft_a = ContextDraft(
            subtype="constraint",
            summary="时间紧张",
            structured_slots={"constraint": "时间紧张"},
            confidence=0.88,
            evidence_span="快迟到了",
        )
        llm_draft_b = ContextDraft(
            subtype="state",
            summary="电量低",
            structured_slots={"state": "电量低"},
            confidence=0.9,
            evidence_span="电量只剩12%",
        )

        def fake_extract_batch(records, events, existing_contexts_by_index=None):
            del records, existing_contexts_by_index
            captured_event_ids.append([event.id for event in events])
            return [[llm_draft_a], [llm_draft_b]]

        engine.context_extractor.extract_batch = fake_extract_batch
        engine.extract_context_drafts = lambda *args, **kwargs: self.fail(
            "single-event context extraction should not run when batch extraction succeeds"
        )
        engine.resolve_context = lambda draft, event=None: {
            "event_id": event.id if event else "",
            "summary": draft.summary,
        }

        resolved = engine._resolve_context_pairs_for_event_batch(
            events=[event_with_context, event_missing_context_a, event_missing_context_b],
            record="共享 episode 文本",
        )

        self.assertEqual(captured_event_ids, [["evt_missing_a", "evt_missing_b"]])
        self.assertEqual(len(resolved[0]), 1)
        self.assertEqual(resolved[0][0][0]["event_id"], "evt_with")
        self.assertEqual(resolved[0][0][1].summary, "车内音乐播放场景")
        self.assertEqual(resolved[1][0][0]["event_id"], "evt_missing_a")
        self.assertEqual(resolved[1][0][1].summary, "时间紧张")
        self.assertEqual(resolved[2][0][0]["event_id"], "evt_missing_b")
        self.assertEqual(resolved[2][0][1].summary, "电量低")

    def test_event_without_payload_contexts_falls_back_to_llm_extraction(self):
        engine = DynamicEvolutionEngine(
            store=types.SimpleNamespace(),
            config=DynamicEvolutionConfig(context_extraction_batch_size=4),
        )
        event = Event(
            id="evt_plain",
            summary="系统建议立即出发",
            action="建议立即出发",
            timestamp=2,
            last_active=2,
            valid_from=2,
            payload={"episode_text": "快迟到了，系统建议立即出发"},
        )
        fallback_calls = []
        draft = ContextDraft(
            subtype="constraint",
            summary="时间紧张",
            structured_slots={"constraint": "时间紧张"},
            evidence_span="快迟到了",
        )

        def fake_extract_context_drafts(event, record=None, existing_contexts=None):
            del record, existing_contexts
            fallback_calls.append(event.id)
            return [draft]

        engine.extract_context_drafts = fake_extract_context_drafts
        engine.resolve_context = lambda draft, event=None: {
            "event_id": event.id if event else "",
            "summary": draft.summary,
        }

        resolved = engine._resolve_context_pairs_for_event_batch(events=[event], record="共享 episode 文本")

        self.assertEqual(fallback_calls, ["evt_plain"])
        self.assertEqual(len(resolved[0]), 1)
        self.assertEqual(resolved[0][0][1].summary, "时间紧张")

    def test_payload_contexts_reuse_existing_validation_logic(self):
        engine = DynamicEvolutionEngine(
            store=types.SimpleNamespace(),
            config=DynamicEvolutionConfig(),
        )
        event = Event(
            id="evt_validate",
            summary="用户在车内播放音乐",
            action="播放音乐",
            timestamp=3,
            last_active=3,
            valid_from=3,
            payload={
                "episode_text": "用户在车内播放音乐",
                "contexts": [
                    {
                        "subtype": "scene",
                        "summary": "  车内音乐播放场景  ",
                        "evidence_span": "在车内播放音乐",
                    },
                    {
                        "subtype": "state",
                        "summary": "",
                        "evidence_span": "电量低",
                    },
                ],
            },
        )

        drafts = engine._build_context_drafts_from_payload(event)

        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].subtype, "situation")
        self.assertEqual(drafts[0].summary, "车内音乐播放场景")
        self.assertEqual(drafts[0].structured_slots["situation"], "车内音乐播放场景")


if __name__ == "__main__":
    unittest.main()
