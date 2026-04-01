# -*- coding: utf-8 -*-
"""LLM-first context extraction pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import json
import logging
import re

try:
    import dashscope
    from dashscope import Generation
except Exception:  # pragma: no cover - optional dependency for offline mode
    dashscope = None
    Generation = None

from ..config import (
    CONTEXT_EXTRACTION_BATCH_SIZE,
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    GENERATION_MODEL,
    normalize_dashscope_base_url,
)
from ..llm import DashScopeClient
from ..core.context import (
    ALLOWED_CONTEXT_SUBTYPES,
    CanonicalContextKey,
    ContextDraft,
    ContextSpan,
    normalize_context_subtype,
)
from ..core.event import Event
from ..utils import load_prompt, robust_json_loads, safe_json_dumps

logger = logging.getLogger(__name__)

_DEFAULT_HABIT_LIKE_MARKERS = ("通常", "经常", "总是", "一向", "偏好", "习惯")
_DEFAULT_EVENT_LIKE_MARKERS = (
    "打开", "搜索", "开始", "完成", "成功", "失败", "找到", "支付", "导航", "播放", "推荐", "决定",
    "说", "回答", "开启", "关闭", "调高", "调低", "前往", "去",
)
_DEFAULT_STRONG_CONTEXT_SIGNAL_MARKERS = (
    "由于", "因为", "当前", "目前", "此时", "只剩", "仅剩", "不足", "需要", "希望", "目标", "条件",
    "限制", "状态", "环境", "情况下",
)
_DEFAULT_GENERIC_CLAUSE_PATTERNS = (
    (r"由于[^，。；,;]+", "constraint", "causal_clause"),
    (r"因为[^，。；,;]+", "constraint", "causal_clause"),
    (r"在[^，。；,;]{1,40}情况下", "situation", "situation_clause"),
    (r"(当前|目前|此时)[^，。；,;]+", "state", "current_clause"),
    (r"(只剩|仅剩|不足)[^，。；,;]+", "constraint", "remaining_clause"),
    (r"(需要|希望|想要|目标是)[^，。；,;]+", "goal", "goal_clause"),
    (r"(正在|处于)[^，。；,;]{0,24}(阶段|中)", "phase", "phase_clause"),
    (r"(执行中|准备中|进行中|等待中|收尾中)", "phase", "phase_clause"),
)
_FALLBACK_SLOT_KEYS = {
    "situation": "situation",
    "state": "state",
    "constraint": "constraint",
    "goal": "goal",
    "environment": "environment",
    "phase": "phase",
}
_SUMMARY_PREFIXES = (
    "由于",
    "因为",
    "当前",
    "目前",
    "此时",
    "需要",
    "希望",
    "想要",
    "目标是",
    "正在",
    "处于",
)
_LOW_REUSE_TIME_PATTERNS = (
    r"\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?",
    r"(上午|下午|晚上|凌晨|中午)?\d{1,2}点(半|\d{1,2}分)?",
    r"\d{1,2}:\d{2}",
)
_DEFAULT_DOMAIN_LITERAL_PATTERNS = (
    r"《[^》]{1,40}》",  # media titles, often event payload details
    r"-阶段[A-Za-z0-9一二三四五六七八九十]+",
)
_DEFAULT_LOW_VALUE_LITERAL_MARKERS = (
    "qq音乐",
    "网易云",
    "酷狗",
    "酷我",
    "喜马拉雅",
    "app",
    "阶段a",
    "阶段b",
    "阶段c",
)
_DEFAULT_MINIMUM_CONTEXT_SUBTYPE_RULES = (
    (("需要", "不足", "紧张", "受限"), "constraint"),
    (("目标", "希望", "想要"), "goal"),
    (("车内", "app", "系统", "设备"), "environment"),
)
_DEFAULT_MINIMUM_CONTEXT_PHRASE_RULES = (
    (("会议", "开会"), "situation", "会议场景"),
)
_DEFAULT_EVENT_LIKE_MINIMUM_CONTEXT_RULES = (
    (("导航",), "situation", "出行导航场景"),
    (("勿扰",), "constraint", "减少打扰需求"),
)
_DEFAULT_ABSTRACT_CONTEXT_RULES = (
    (("qq音乐", "网易云", "酷狗", "酷我", "歌曲", "歌单", "播放"), None, "音乐播放场景"),
    (("导航", "地图", "路线", "充电桩", "目的地"), None, "出行导航场景"),
    (("会议", "开会", "勿扰"), "constraint", "减少打扰需求"),
    (("会议", "开会", "勿扰"), None, "会议场景"),
)
_DEFAULT_ABSTRACT_CONTEXT_FALLBACK_BY_SUBTYPE = {
    "constraint": "当前约束条件",
    "goal": "当前目标",
    "state": "当前状态",
    "environment": "当前环境",
    "phase": "当前阶段",
}
_DEFAULT_INVALID_CONTEXT_DATA_MARKERS = (
    "[车辆状态]",
    "[舱内摄像头]",
    "[日程数据]",
    "[屏幕]",
    "[环境感知]",
    "来源:",
    "payload",
    "cal_evt",
    "door_status",
    "vehicle_stat",
)
_DEFAULT_INVALID_CONTEXT_DIALOGUE_MARKERS = (
    "->",
    "车机回答",
    "车机主动播报",
)
_DEFAULT_CONTEXT_DOMAIN_CONFIG = {
    "habit_like_markers": _DEFAULT_HABIT_LIKE_MARKERS,
    "event_like_markers": _DEFAULT_EVENT_LIKE_MARKERS,
    "strong_context_signal_markers": _DEFAULT_STRONG_CONTEXT_SIGNAL_MARKERS,
    "generic_clause_patterns": _DEFAULT_GENERIC_CLAUSE_PATTERNS,
    "domain_literal_patterns": _DEFAULT_DOMAIN_LITERAL_PATTERNS,
    "low_value_literal_markers": _DEFAULT_LOW_VALUE_LITERAL_MARKERS,
    "minimum_context_subtype_rules": _DEFAULT_MINIMUM_CONTEXT_SUBTYPE_RULES,
    "minimum_context_phrase_rules": _DEFAULT_MINIMUM_CONTEXT_PHRASE_RULES,
    "event_like_minimum_context_rules": _DEFAULT_EVENT_LIKE_MINIMUM_CONTEXT_RULES,
    "abstract_context_rules": _DEFAULT_ABSTRACT_CONTEXT_RULES,
    "abstract_context_fallback_by_subtype": _DEFAULT_ABSTRACT_CONTEXT_FALLBACK_BY_SUBTYPE,
    "invalid_context_data_markers": _DEFAULT_INVALID_CONTEXT_DATA_MARKERS,
    "invalid_context_dialogue_markers": _DEFAULT_INVALID_CONTEXT_DIALOGUE_MARKERS,
}


@dataclass
class _PreparedContextRequest:
    record_text: str
    event: Optional[Event]
    candidate_spans: list[ContextSpan]


def _merge_context_domain_config(overrides: Optional[dict[str, Any]]) -> dict[str, Any]:
    config = dict(_DEFAULT_CONTEXT_DOMAIN_CONFIG)
    fallback_by_subtype = dict(_DEFAULT_ABSTRACT_CONTEXT_FALLBACK_BY_SUBTYPE)
    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            if key == "abstract_context_fallback_by_subtype" and isinstance(value, dict):
                fallback_by_subtype.update(value)
                continue
            config[key] = value
    config["abstract_context_fallback_by_subtype"] = fallback_by_subtype
    return config


@dataclass
class ContextExtractionPipeline:
    """Extract reusable ContextDraft objects from record/Event input."""

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    generation_model: Optional[str] = None
    offline_mode: bool = False
    domain_config: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        self.api_key = self.api_key or DASHSCOPE_API_KEY
        self.base_url = normalize_dashscope_base_url(
            self.base_url or DASHSCOPE_BASE_URL
        )
        self.generation_model = self.generation_model or GENERATION_MODEL
        self.llm_client = DashScopeClient(
            api_key=self.api_key,
            base_url=self.base_url,
            generation_api_resolver=lambda: Generation,
        )
        self._domain_config = _merge_context_domain_config(self.domain_config)
        self._habit_like_markers = tuple(self._domain_config["habit_like_markers"])
        self._event_like_markers = tuple(self._domain_config["event_like_markers"])
        self._strong_context_signal_markers = tuple(
            self._domain_config["strong_context_signal_markers"]
        )
        self._generic_clause_patterns = tuple(self._domain_config["generic_clause_patterns"])
        self._domain_literal_patterns = tuple(self._domain_config["domain_literal_patterns"])
        self._low_value_literal_markers = tuple(self._domain_config["low_value_literal_markers"])
        self._minimum_context_subtype_rules = tuple(
            (tuple(markers), subtype)
            for markers, subtype in self._domain_config["minimum_context_subtype_rules"]
        )
        self._minimum_context_phrase_rules = tuple(
            (tuple(markers), subtype, summary)
            for markers, subtype, summary in self._domain_config["minimum_context_phrase_rules"]
        )
        self._event_like_minimum_context_rules = tuple(
            (tuple(markers), subtype, summary)
            for markers, subtype, summary in self._domain_config["event_like_minimum_context_rules"]
        )
        self._abstract_context_rules = tuple(
            (tuple(markers), required_subtype, summary)
            for markers, required_subtype, summary in self._domain_config["abstract_context_rules"]
        )
        self._abstract_context_fallback_by_subtype = dict(
            self._domain_config["abstract_context_fallback_by_subtype"]
        )
        self._invalid_context_data_markers = tuple(
            self._domain_config["invalid_context_data_markers"]
        )
        self._invalid_context_dialogue_markers = tuple(
            self._domain_config["invalid_context_dialogue_markers"]
        )
        self._system_prompt = load_prompt("extract_context_system.txt")
        self._user_prompt = load_prompt("extract_context_user.txt")
        self._batch_user_prompt = load_prompt("extract_context_batch_user.txt")

    def extract(self, record: Any, event: Optional[Event] = None) -> list[ContextDraft]:
        prepared = self._prepare_context_request(record=record, event=event)
        llm_drafts = self.llm_extract_contexts(
            prepared.record_text,
            prepared.event,
            prepared.candidate_spans,
        )
        return self._finalize_context_extraction(prepared=prepared, llm_drafts=llm_drafts)

    def extract_batch(
        self,
        records: list[Any],
        events: list[Optional[Event]],
    ) -> list[list[ContextDraft]]:
        if not records or not events or len(records) != len(events):
            return []
        if len(events) == 1:
            return [self.extract(records[0], event=events[0])]

        prepared_requests = [
            self._prepare_context_request(record=record, event=event)
            for record, event in zip(records, events)
        ]

        batch_used, llm_drafts_by_index = self.llm_extract_contexts_batch(prepared_requests)
        if not batch_used:
            return [
                self.extract(record, event=event)
                for record, event in zip(records, events)
            ]

        return [
            self._finalize_context_extraction(
                prepared=prepared,
                llm_drafts=llm_drafts_by_index.get(idx, []),
            )
            for idx, prepared in enumerate(prepared_requests)
        ]

    def _prepare_context_request(
        self,
        record: Any,
        event: Optional[Event],
    ) -> _PreparedContextRequest:
        record_text = self._record_text(record, event)
        candidate_spans = self.detect_context_candidates(record, event)
        return _PreparedContextRequest(
            record_text=record_text,
            event=event,
            candidate_spans=candidate_spans,
        )

    def _finalize_context_extraction(
        self,
        prepared: _PreparedContextRequest,
        llm_drafts: Optional[list[ContextDraft]] = None,
    ) -> list[ContextDraft]:
        record_text = prepared.record_text
        event = prepared.event
        candidate_spans = prepared.candidate_spans
        fallback_drafts = self._fallback_extract_contexts(record_text, event, candidate_spans)
        drafts: list[ContextDraft] = []
        if llm_drafts:
            drafts.extend(llm_drafts)
            # LLM is primary, but keep a small amount of heuristic recall to avoid sparse misses.
            if len(llm_drafts) < self._target_context_count(record_text):
                drafts.extend(fallback_drafts)
        else:
            drafts = fallback_drafts
        validated = self.validate_context_drafts(drafts, record_text, event)
        reranked = self._rerank_context_drafts(validated, record_text, event)
        canonicalized = [
            draft for draft in (self.canonicalize_context(item) for item in reranked)
            if self.is_valid_context_draft(draft)
        ]
        if not canonicalized:
            inferred = self._infer_minimum_context(event=event, record_text=record_text)
            if inferred is not None:
                inferred_canonical = self.canonicalize_context(inferred)
                if self.is_valid_context_draft(inferred_canonical):
                    canonicalized = [inferred_canonical]
        return self._dedupe_drafts(canonicalized)

    def detect_context_candidates(
        self,
        record: Any,
        event: Optional[Event] = None,
    ) -> list[ContextSpan]:
        text = self._record_text(record, event)
        spans: list[ContextSpan] = []
        seen: set[tuple[str, str]] = set()

        for clause, start, end in self._split_text_clauses(text):
            if not clause:
                continue
            for pattern, subtype, signal in self._generic_clause_patterns:
                if not re.search(pattern, clause):
                    continue
                key = (clause, signal)
                if key in seen:
                    continue
                seen.add(key)
                spans.append(
                    ContextSpan(
                        text=clause,
                        signal=signal,
                        subtype_hint=subtype,
                        source="record",
                        start=start,
                        end=end,
                    )
                )
                break

        if event is not None:
            payload = event.payload if isinstance(event.payload, dict) else {}
            for key, subtype in (
                ("context_note", "situation"),
                ("state", "state"),
                ("constraint", "constraint"),
                ("constraint_hint", "constraint"),
                ("goal", "goal"),
                ("goal_hint", "goal"),
                ("phase", "phase"),
                ("task_stage", "phase"),
            ):
                value = payload.get(key)
                text_value = str(value or "").strip()
                if not text_value:
                    continue
                dedupe_key = (text_value, key)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                spans.append(
                    ContextSpan(
                        text=text_value,
                        signal=f"event_payload:{key}",
                        subtype_hint=subtype,
                        source="event",
                    )
                )
            context_payload = payload.get("context", {}) if isinstance(payload.get("context", {}), dict) else {}
            context_key_map: list[tuple[str, str]] = [
                ("scene", "situation"),
                ("state", "state"),
                ("constraint", "constraint"),
                ("goal", "goal"),
                ("phase", "phase"),
                ("environment", "environment"),
                ("geo_context", "environment"),
                ("digital_context", "environment"),
                ("time_bucket", "state"),
                ("task_stage", "phase"),
            ]
            for key, subtype in context_key_map:
                value = context_payload.get(key)
                text_value = str(value or "").strip()
                if not text_value:
                    continue
                dedupe_key = (text_value, f"context:{key}")
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                spans.append(
                    ContextSpan(
                        text=text_value,
                        signal=f"event_payload:context.{key}",
                        subtype_hint=subtype,
                        source="event",
                    )
                )
        return spans[: self._candidate_span_limit(text=text, event=event)]

    def llm_extract_contexts(
        self,
        record_text: str,
        event: Optional[Event],
        candidate_spans: list[ContextSpan],
    ) -> list[ContextDraft]:
        if not self._llm_available():
            return []
        parsed = self._call_context_llm_json(
            self._build_context_user_message(
                record_text=record_text,
                event=event,
                candidate_spans=candidate_spans,
            )
        )
        raw_contexts = parsed.get("contexts", []) if isinstance(parsed, dict) else []
        return self._build_context_drafts_from_raw(
            raw_contexts=raw_contexts,
            event=event,
            candidate_spans=candidate_spans,
        )

    def llm_extract_contexts_batch(
        self,
        prepared_requests: list[_PreparedContextRequest],
    ) -> tuple[bool, dict[int, list[ContextDraft]]]:
        if (
            len(prepared_requests) <= 1
            or not self._llm_available()
            or not str(self._batch_user_prompt or "").strip()
        ):
            return False, {}

        batches = [
            (start, prepared_requests[start : start + CONTEXT_EXTRACTION_BATCH_SIZE])
            for start in range(0, len(prepared_requests), CONTEXT_EXTRACTION_BATCH_SIZE)
        ]
        drafts_by_index: dict[int, list[ContextDraft]] = {}

        for batch_start, batch_requests in batches:
            parsed = self._call_context_llm_json(
                self._build_context_batch_user_message(batch_requests)
            )
            raw_items = self._collect_batched_context_items(parsed)
            if len(raw_items) != len(batch_requests):
                logger.warning(
                    "context batch extraction returned %s items for %s requests; "
                    "falling back to heuristic extraction for this batch",
                    len(raw_items),
                    len(batch_requests),
                )
                continue

            for item in raw_items:
                try:
                    item_index = self._parse_context_item_index(
                        item,
                        item_total=len(batch_requests),
                    )
                except ValueError as exc:
                    logger.warning(
                        "context batch extraction returned invalid item index; "
                        "falling back to heuristic extraction for this item: %s",
                        exc,
                    )
                    continue
                absolute_index = batch_start + item_index
                prepared = batch_requests[item_index]
                drafts_by_index[absolute_index] = self._build_context_drafts_from_raw(
                    raw_contexts=item.get("contexts", []),
                    event=prepared.event,
                    candidate_spans=prepared.candidate_spans,
                )

        return True, drafts_by_index

    def _build_context_user_message(
        self,
        record_text: str,
        event: Optional[Event],
        candidate_spans: list[ContextSpan],
    ) -> str:
        return self._user_prompt.format(
            record_text=record_text or "",
            event_json=safe_json_dumps(self._event_payload(event)),
            candidate_spans_json=safe_json_dumps(
                self._candidate_spans_payload(candidate_spans)
            ),
        )

    def _build_context_batch_user_message(
        self,
        prepared_requests: list[_PreparedContextRequest],
    ) -> str:
        items = []
        for idx, prepared in enumerate(prepared_requests):
            items.append(
                {
                    "item_index": idx,
                    "record_text": prepared.record_text or "",
                    "event": self._event_payload(prepared.event),
                    "candidate_spans": self._candidate_spans_payload(prepared.candidate_spans),
                }
            )
        return self._batch_user_prompt.format(
            item_total=len(prepared_requests),
            items_json=safe_json_dumps(items),
        )

    def _candidate_spans_payload(
        self,
        candidate_spans: list[ContextSpan],
    ) -> list[dict[str, Any]]:
        return [
            {
                "text": span.text,
                "signal": span.signal,
                "subtype_hint": span.subtype_hint,
                "source": span.source,
            }
            for span in candidate_spans
        ]

    def _call_context_llm_json(self, user_msg: str) -> Any:
        try:
            resp = self.llm_client.call_generation(
                model=self.generation_model,
                messages=self.llm_client.build_messages(self._system_prompt, user_msg),
                result_format="message",
                enable_thinking=False,
            )
            if not self.llm_client.is_success(resp):
                return {}
            return robust_json_loads(self.llm_client.message_content(resp), {})
        except Exception:
            return {}

    def _build_context_drafts_from_raw(
        self,
        raw_contexts: Any,
        event: Optional[Event],
        candidate_spans: list[ContextSpan],
    ) -> list[ContextDraft]:
        if not isinstance(raw_contexts, list):
            return []

        drafts: list[ContextDraft] = []
        for item in raw_contexts:
            if not isinstance(item, dict) or item.get("not_context") is True:
                continue
            subtype = normalize_context_subtype(item.get("subtype", "situation"))
            summary = str(item.get("summary", "") or "").strip()
            evidence_span = str(item.get("evidence_span", "") or "").strip()
            structured_slots = item.get("structured_slots", {})
            if not isinstance(structured_slots, dict):
                structured_slots = {}
            signal = self._match_signal(evidence_span, candidate_spans)
            drafts.append(
                ContextDraft(
                    subtype=subtype,
                    summary=summary,
                    structured_slots=structured_slots,
                    confidence=float(item.get("confidence", 0.6) or 0.6),
                    evidence_span=evidence_span,
                    source_refs=self._make_source_refs(
                        event=event,
                        evidence_span=evidence_span,
                        signal=signal,
                        source="llm_context_extraction",
                    ),
                    valid_from=self._event_timestamp(event),
                )
            )
        return drafts

    def _collect_batched_context_items(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("items", "contexts_by_item", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _parse_context_item_index(self, payload: dict[str, Any], item_total: int) -> int:
        raw_value = payload.get("item_index", payload.get("index"))
        try:
            item_index = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid item_index: {raw_value!r}") from exc
        if item_index < 0 or item_index >= item_total:
            raise ValueError(f"item_index out of range: {item_index}")
        return item_index

    def validate_context_drafts(
        self,
        context_drafts: list[ContextDraft],
        record_text: str,
        event: Optional[Event],
    ) -> list[ContextDraft]:
        validated: list[ContextDraft] = []
        for draft in context_drafts:
            if not isinstance(draft, ContextDraft):
                continue
            if draft.subtype not in ALLOWED_CONTEXT_SUBTYPES:
                continue
            if not draft.summary:
                continue
            if not isinstance(draft.structured_slots, dict):
                continue
            if not self.is_valid_context_draft(draft):
                continue

            evidence_span = draft.evidence_span or draft.summary
            evidence_overlap = self._evidence_overlap_ratio(evidence_span, record_text)
            if evidence_overlap <= 0.0:
                continue
            primary_source = ""
            if draft.source_refs and isinstance(draft.source_refs[0], dict):
                primary_source = str(draft.source_refs[0].get("source", "") or "")
            from_llm = "llm_context_extraction" in primary_source
            quality = self._context_quality_score(
                draft=draft,
                evidence_span=evidence_span,
                record_text=record_text,
                event=event,
                from_llm=from_llm,
                evidence_overlap=evidence_overlap,
            )
            accept_gate = 0.42 if from_llm else 0.54
            if quality < accept_gate:
                continue

            cleaned_slots = self._clean_structured_slots(draft.structured_slots)
            if not cleaned_slots:
                slot_key = _FALLBACK_SLOT_KEYS.get(draft.subtype, "condition")
                cleaned_slots = {
                    slot_key: self._normalize_free_text(
                        self._abstract_context_phrase(
                            self._normalize_context_surface(draft.summary),
                            evidence_span=evidence_span,
                            subtype=draft.subtype,
                        )
                    )
                }

            normalized_summary = self._abstract_context_phrase(
                self._normalize_context_surface(draft.summary),
                evidence_span=evidence_span,
                subtype=draft.subtype,
            )
            if not normalized_summary:
                continue

            candidate = ContextDraft(
                subtype=draft.subtype,
                summary=self._normalize_free_text(normalized_summary),
                structured_slots=cleaned_slots,
                confidence=max(0.1, min(1.0, 0.75 * draft.confidence + 0.25 * quality)),
                evidence_span=evidence_span,
                source_refs=draft.source_refs or self._make_source_refs(
                    event=event,
                    evidence_span=evidence_span,
                    signal="validated_context",
                    source="context_validation",
                ),
                valid_from=draft.valid_from or self._event_timestamp(event),
                valid_to=draft.valid_to,
            )
            if not self.is_valid_context_draft(candidate):
                continue
            validated.append(candidate)
        return validated

    def is_valid_context_draft(self, draft: ContextDraft) -> bool:
        if not isinstance(draft, ContextDraft):
            return False
        summary = str(draft.summary or "").strip()
        if not summary:
            return False
        if len(summary) > 40:
            return False

        summary_lower = summary.lower()
        if any(marker.lower() in summary_lower for marker in self._invalid_context_data_markers):
            return False
        if any(ch in summary for ch in ('{', '}', '"')):
            return False
        if any(marker in summary for marker in self._invalid_context_dialogue_markers):
            return False

        evidence_span = str(draft.evidence_span or "").strip()
        if evidence_span and summary == evidence_span and len(summary) > 15:
            return False
        return True

    def canonicalize_context(self, context_draft: ContextDraft) -> ContextDraft:
        subtype = normalize_context_subtype(context_draft.subtype)
        slots = self._canonicalize_slots(context_draft.structured_slots)
        summary = self._canonicalize_summary(context_draft.summary, context_draft.evidence_span)
        canonical_key = CanonicalContextKey(
            context_type="context",
            subtype=subtype,
            summary=summary,
            structured_slots=slots,
        )
        return ContextDraft(
            subtype=subtype,
            summary=summary,
            structured_slots=slots,
            confidence=context_draft.confidence,
            evidence_span=context_draft.evidence_span or context_draft.summary,
            source_refs=context_draft.source_refs,
            valid_from=context_draft.valid_from,
            valid_to=context_draft.valid_to,
            canonical_key=canonical_key,
        )

    def _fallback_extract_contexts(
        self,
        record_text: str,
        event: Optional[Event],
        candidate_spans: list[ContextSpan],
    ) -> list[ContextDraft]:
        drafts: list[ContextDraft] = []
        for span in candidate_spans:
            normalized_text = self._strip_discourse_prefix(span.text)
            if not normalized_text:
                continue
            if self._looks_like_habit_not_context(normalized_text):
                continue
            if self._looks_like_event_or_result(normalized_text, event):
                continue
            if self._looks_low_reusability_context(normalized_text, span.text, strict=False):
                continue
            subtype = normalize_context_subtype(span.subtype_hint)
            summary = self._abstract_context_phrase(
                self._normalize_context_surface(normalized_text),
                evidence_span=span.text,
                subtype=subtype,
            )
            draft = ContextDraft(
                subtype=subtype,
                summary=summary,
                structured_slots={
                    _FALLBACK_SLOT_KEYS.get(subtype, "condition"): summary
                },
                confidence=0.58,
                evidence_span=span.text,
                source_refs=self._make_source_refs(
                    event=event,
                    evidence_span=span.text,
                    signal=span.signal or "fallback_context",
                    source="fallback_context_extraction",
                ),
                valid_from=self._event_timestamp(event),
            )
            if not self.is_valid_context_draft(draft):
                continue
            drafts.append(draft)
        return drafts

    def _clean_structured_slots(self, structured_slots: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in structured_slots.items():
            key = self._normalize_slot_key(raw_key)
            value = self._normalize_slot_value(raw_value)
            if not key or value in (None, "", [], {}):
                continue
            cleaned[key] = value
        return cleaned

    def _canonicalize_slots(self, structured_slots: dict[str, Any]) -> dict[str, Any]:
        cleaned = self._clean_structured_slots(structured_slots)
        return {
            key: cleaned[key]
            for key in sorted(cleaned.keys())
        }

    def _normalize_slot_key(self, value: Any) -> str:
        text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        return re.sub(r"[^a-z0-9_\u4e00-\u9fff]", "", text)

    def _normalize_slot_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            nested = {
                self._normalize_slot_key(key): self._normalize_slot_value(sub_value)
                for key, sub_value in value.items()
            }
            return {key: nested[key] for key in sorted(nested.keys()) if key and nested[key] not in (None, "", [], {})}
        if isinstance(value, list):
            result = []
            for item in value:
                normalized = self._normalize_slot_value(item)
                if normalized not in (None, "", [], {}) and normalized not in result:
                    result.append(normalized)
            return result
        if isinstance(value, (int, float, bool)):
            return value
        text = str(value or "").strip()
        if not text:
            return ""
        if re.fullmatch(r"\d+", text):
            return int(text)
        if re.fullmatch(r"\d+\.\d+", text):
            return float(text)
        percent = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", text)
        if percent:
            number = float(percent.group(1))
            return int(number) if number.is_integer() else number
        return self._normalize_free_text(text)

    def _canonicalize_summary(self, summary: str, evidence_span: str) -> str:
        text = self._normalize_free_text(summary or evidence_span)
        text = self._strip_discourse_prefix(text)
        return text or self._normalize_free_text(evidence_span)

    def _dedupe_drafts(self, drafts: list[ContextDraft]) -> list[ContextDraft]:
        deduped: list[ContextDraft] = []
        seen: set[str] = set()
        for draft in drafts:
            canonical = draft.canonical_key or CanonicalContextKey(
                subtype=draft.subtype,
                summary=draft.summary,
                structured_slots=draft.structured_slots,
            )
            signature = json.dumps(canonical.to_dict(), ensure_ascii=False, sort_keys=True)
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(draft)
        return deduped

    def _event_payload(self, event: Optional[Event]) -> dict[str, Any]:
        if event is None:
            return {}
        return {
            "id": event.id,
            "summary": event.summary,
            "action": event.action,
            "causality": event.causality,
            "participants": event.participants,
            "payload": event.payload,
            "timestamp": event.timestamp,
            "valid_from": event.valid_from,
        }

    def _record_text(self, record: Any, event: Optional[Event]) -> str:
        if isinstance(record, str):
            return record.strip()
        if isinstance(record, Event):
            payload = record.payload if isinstance(record.payload, dict) else {}
            return str(payload.get("episode_text", "") or record.summary or record.action or "").strip()
        if isinstance(record, dict):
            for key in ("text", "content", "record", "raw_input", "input"):
                value = record.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            episode = record.get("episode")
            if isinstance(episode, dict):
                for key in ("content", "text"):
                    value = episode.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
        if event is not None:
            payload = event.payload if isinstance(event.payload, dict) else {}
            return str(payload.get("episode_text", "") or event.summary or event.action or "").strip()
        return ""

    def _event_timestamp(self, event: Optional[Event]) -> int:
        if event is None:
            return 0
        return int(event.valid_from or event.timestamp or event.last_active or 0)

    def _split_text_clauses(self, text: str) -> list[tuple[str, int, int]]:
        clauses: list[tuple[str, int, int]] = []
        for match in re.finditer(r"[^。！？!?；;]+", text):
            chunk = match.group(0).strip(" ，,\n\t")
            if chunk:
                clauses.append((chunk, match.start(), match.end()))
        return clauses

    def _evidence_matches_text(self, evidence_span: str, record_text: str) -> bool:
        if not evidence_span:
            return False
        if not record_text:
            return True
        evidence_norm = self._normalize_text(evidence_span)
        record_norm = self._normalize_text(record_text)
        if evidence_norm and evidence_norm in record_norm:
            return True
        evidence_tokens = self._tokenize_text(evidence_span)
        record_tokens = self._tokenize_text(record_text)
        if not evidence_tokens:
            return False
        overlap = len(evidence_tokens & record_tokens)
        return overlap / max(1, len(evidence_tokens)) >= 0.55

    def _evidence_overlap_ratio(self, evidence_span: str, record_text: str) -> float:
        if not evidence_span:
            return 0.0
        if not record_text:
            return 1.0
        evidence_norm = self._normalize_text(evidence_span)
        record_norm = self._normalize_text(record_text)
        if evidence_norm and evidence_norm in record_norm:
            return 1.0
        evidence_tokens = self._tokenize_text(evidence_span)
        record_tokens = self._tokenize_text(record_text)
        if not evidence_tokens:
            return 0.0
        overlap = len(evidence_tokens & record_tokens)
        return overlap / max(1, len(evidence_tokens))

    def _looks_like_event_or_result(self, text: str, event: Optional[Event]) -> bool:
        normalized = self._normalize_free_text(text)
        if not normalized:
            return True
        has_strong_context_signal = any(
            marker in normalized for marker in self._strong_context_signal_markers
        )
        event_marker_count = sum(1 for marker in self._event_like_markers if marker in normalized)
        if event_marker_count > 0:
            if not has_strong_context_signal:
                return True
        if event is not None:
            event_text = " ".join(part for part in [event.summary, event.action, event.causality] if part)
            lexical_sim = self._lexical_similarity(normalized, event_text)
            similarity_gate = 0.80 if event_marker_count > 0 else 0.88
            if lexical_sim >= similarity_gate:
                return True
            if normalized == self._normalize_free_text(event.summary) or normalized == self._normalize_free_text(event.action):
                return True
        return False

    def _looks_low_reusability_context(self, summary: str, evidence_span: str, strict: bool = True) -> bool:
        text = self._normalize_free_text(summary or evidence_span)
        if not text:
            return True
        text_lower = text.lower()
        event_marker_count = sum(1 for marker in self._event_like_markers if marker in text)

        if not strict:
            # LLM path is primary; keep only very coarse guardrails.
            if len(text) >= 48 and event_marker_count >= 2:
                return True
            return False

        for pattern in _LOW_REUSE_TIME_PATTERNS:
            if re.search(pattern, text):
                if event_marker_count >= 1:
                    return True
        for pattern in self._domain_literal_patterns:
            if re.search(pattern, text):
                return True
        # Overly specific one-shot narration is usually not reusable as Context.
        if len(text) >= 30 and event_marker_count >= 1:
            return True
        if "播放" in text_lower and ("qq音乐" in text_lower or "网易云" in text_lower):
            return True
        return False

    def _normalize_context_surface(self, text: str) -> str:
        normalized = self._normalize_free_text(text)
        if not normalized:
            return normalized
        # Strip leading timeline fragments such as "2026-03-12 下午5点半左右,"
        normalized = re.sub(
            r"^\s*(\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?\s*)?"
            r"((上午|下午|晚上|凌晨|中午)?\d{1,2}点(半|\d{1,2}分)?\s*(左右)?)?\s*[，,]?",
            "",
            normalized,
        ).strip(" ，,")
        return self._normalize_free_text(normalized)

    def _looks_like_habit_not_context(self, text: str) -> bool:
        normalized = str(text or "").strip()
        return any(marker in normalized for marker in self._habit_like_markers)

    def _context_quality_score(
        self,
        draft: ContextDraft,
        evidence_span: str,
        record_text: str,
        event: Optional[Event],
        from_llm: bool,
        evidence_overlap: float,
    ) -> float:
        summary = draft.summary or ""
        event_like = self._looks_like_event_or_result(summary, event)
        low_reuse = self._looks_low_reusability_context(
            summary,
            evidence_span,
            strict=not from_llm,
        )
        habit_like = self._looks_like_habit_not_context(evidence_span)
        has_slots = bool(self._clean_structured_slots(draft.structured_slots))
        context_signal = any(marker in summary for marker in self._strong_context_signal_markers)
        abstraction_score = self._abstraction_quality_score(summary, evidence_span)

        score = 0.25
        score += 0.35 * max(0.0, min(1.0, evidence_overlap))
        score += 0.20 * max(0.0, min(1.0, draft.confidence))
        score += 0.15 * abstraction_score
        if has_slots:
            score += 0.12
        if context_signal:
            score += 0.08
        if event_like:
            score -= 0.30 if from_llm else 0.40
        if low_reuse:
            score -= 0.18 if from_llm else 0.25
        if habit_like:
            score -= 0.25
        return max(0.0, min(1.0, score))

    def _abstraction_quality_score(self, summary: str, evidence_span: str) -> float:
        text = self._normalize_free_text(summary)
        if not text:
            return 0.0
        lowered = text.lower()
        score = 0.55
        # Reward concise reusable labels.
        if len(text) <= 14:
            score += 0.20
        elif len(text) <= 24:
            score += 0.10
        # Penalize literal one-shot details leaking into summary.
        for pattern in self._domain_literal_patterns:
            if re.search(pattern, text):
                score -= 0.30
        if re.search(r"\d{1,2}(:|点)\d{0,2}", text):
            score -= 0.20
        if any(marker in lowered for marker in self._low_value_literal_markers):
            score -= 0.18
        if any(token in text for token in ("播放", "打开", "搜索", "开始", "前往")) and len(text) >= 16:
            score -= 0.15
        # If summary is near-copy of evidence, abstraction is weak.
        sim = self._lexical_similarity(text, evidence_span or "")
        if sim >= 0.75 and len(text) >= 14:
            score -= 0.18
        return max(0.0, min(1.0, score))

    def _candidate_span_limit(self, text: str, event: Optional[Event]) -> int:
        text_len = len(self._normalize_free_text(text))
        payload_bonus = 0
        if event is not None and isinstance(event.payload, dict):
            payload = event.payload
            if isinstance(payload.get("context"), dict):
                payload_bonus += min(4, len(payload.get("context", {})))
            payload_bonus += 1 if payload.get("context_note") else 0
            payload_bonus += 1 if payload.get("constraint") else 0
            payload_bonus += 1 if payload.get("goal") else 0
        base = 10
        length_bonus = min(8, text_len // 80)
        return max(8, min(24, base + length_bonus + payload_bonus))

    def _target_context_count(self, record_text: str) -> int:
        text_len = len(self._normalize_free_text(record_text))
        if text_len <= 36:
            return 2
        if text_len <= 120:
            return 3
        if text_len <= 220:
            return 4
        return 5

    def _rerank_context_drafts(
        self,
        drafts: list[ContextDraft],
        record_text: str,
        event: Optional[Event],
    ) -> list[ContextDraft]:
        if not drafts:
            return []
        scored: list[tuple[float, ContextDraft]] = []
        for draft in drafts:
            evidence_span = draft.evidence_span or draft.summary
            overlap = self._evidence_overlap_ratio(evidence_span, record_text)
            source = ""
            if draft.source_refs and isinstance(draft.source_refs[0], dict):
                source = str(draft.source_refs[0].get("source", "") or "")
            from_llm = "llm_context_extraction" in source
            score = self._context_quality_score(
                draft=draft,
                evidence_span=evidence_span,
                record_text=record_text,
                event=event,
                from_llm=from_llm,
                evidence_overlap=overlap,
            )
            scored.append((score, draft))
        scored.sort(key=lambda item: (item[0], item[1].confidence), reverse=True)

        target = self._target_context_count(record_text)
        max_keep = max(target + 1, min(8, target * 2))
        selected: list[ContextDraft] = []
        used_subtypes: set[str] = set()
        for score, draft in scored:
            if len(selected) >= max_keep:
                break
            # Prefer subtype diversity in the first pass, then backfill by score.
            if len(selected) < target and draft.subtype in used_subtypes:
                continue
            selected.append(draft)
            used_subtypes.add(draft.subtype)
        if len(selected) < min(max_keep, len(scored)):
            for _score, draft in scored:
                if draft in selected:
                    continue
                selected.append(draft)
                if len(selected) >= max_keep:
                    break
        return selected

    def _infer_minimum_context(self, event: Optional[Event], record_text: str) -> Optional[ContextDraft]:
        if event is None:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        candidates = [
            str(payload.get("context_note", "") or "").strip(),
            str(payload.get("state", "") or "").strip(),
            str(payload.get("constraint", "") or "").strip(),
            str(payload.get("goal", "") or "").strip(),
            str((payload.get("context", {}) or {}).get("scene", "") if isinstance(payload.get("context", {}), dict) else "").strip(),
            str(event.summary or "").strip(),
            str(event.action or "").strip(),
        ]
        text = next((item for item in candidates if item), "")
        if not text:
            text = self._normalize_free_text(record_text)
        if not text:
            return None

        subtype = "situation"
        summary = self._normalize_context_surface(text)
        lowered = summary.lower()
        for markers, configured_subtype in self._minimum_context_subtype_rules:
            if any(marker.lower() in lowered for marker in markers):
                subtype = configured_subtype
                break
        for markers, configured_subtype, configured_summary in self._minimum_context_phrase_rules:
            if any(marker.lower() in lowered for marker in markers):
                subtype = configured_subtype
                summary = configured_summary
                break

        if self._looks_like_event_or_result(summary, event):
            matched_rule = False
            for markers, configured_subtype, configured_summary in self._event_like_minimum_context_rules:
                if any(marker.lower() in lowered for marker in markers):
                    summary = configured_summary
                    subtype = configured_subtype
                    matched_rule = True
                    break
            if not matched_rule:
                summary = self._normalize_context_surface(record_text) or "当前场景"
        summary = self._abstract_context_phrase(
            summary,
            evidence_span=text,
            subtype=normalize_context_subtype(subtype),
        )

        slot_key = _FALLBACK_SLOT_KEYS.get(subtype, "condition")
        evidence = self._normalize_free_text(text) or summary
        draft = ContextDraft(
            subtype=normalize_context_subtype(subtype),
            summary=self._normalize_free_text(summary),
            structured_slots={slot_key: self._normalize_free_text(summary)},
            confidence=0.45,
            evidence_span=evidence,
            source_refs=self._make_source_refs(
                event=event,
                evidence_span=evidence,
                signal="event_minimum_context",
                source="event_minimum_context_inference",
            ),
            valid_from=self._event_timestamp(event),
        )
        return draft if self.is_valid_context_draft(draft) else None

    def _abstract_context_phrase(self, text: str, evidence_span: str = "", subtype: str = "situation") -> str:
        normalized = self._normalize_context_surface(text)
        if not normalized:
            return normalized
        source = f"{normalized} {evidence_span}".lower()

        for markers, required_subtype, configured_summary in self._abstract_context_rules:
            if required_subtype is not None and required_subtype != subtype:
                continue
            if any(marker.lower() in source for marker in markers):
                return configured_summary

        abstracted = normalized
        for pattern in self._domain_literal_patterns:
            abstracted = re.sub(pattern, "", abstracted).strip(" ，,。；;：:")
        abstracted = re.sub(
            r"(在)?(qq音乐|网易云|酷狗|酷我|喜马拉雅)(上)?",
            "",
            abstracted,
            flags=re.IGNORECASE,
        ).strip(" ，,。；;：:")
        abstracted = re.sub(r"(打开|播放|搜索|开始|继续|切换|前往|去往).{0,16}$", "", abstracted).strip(" ，,。；;：:")
        if not abstracted:
            return self._abstract_context_fallback_by_subtype.get(subtype, "当前场景")
        return self._normalize_free_text(abstracted)

    def _make_source_refs(
        self,
        event: Optional[Event],
        evidence_span: str,
        signal: str,
        source: str,
    ) -> list[dict[str, Any]]:
        ref: dict[str, Any] = {
            "source": source,
            "evidence_span": evidence_span,
            "signal": signal,
        }
        if event is not None:
            ref["event_id"] = event.id
            ref["timestamp"] = self._event_timestamp(event)
        return [ref]

    def _match_signal(self, evidence_span: str, candidate_spans: list[ContextSpan]) -> str:
        for span in candidate_spans:
            if span.text == evidence_span:
                return span.signal
            if evidence_span and evidence_span in span.text:
                return span.signal
        return "llm_context"

    def _normalize_free_text(self, text: str) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip(" ，,。；;：:")
        return normalized[:64]

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"[\s，,。；;：:、/\\-]+", "", str(text or "").lower())

    def _tokenize_text(self, text: str) -> set[str]:
        raw = str(text or "").lower()
        tokens = set(re.findall(r"[\u4e00-\u9fff]{1,4}|[a-z0-9_]+", raw))
        compact = self._normalize_text(raw)
        if len(compact) >= 2:
            tokens.update(compact[idx: idx + 2] for idx in range(len(compact) - 1))
        elif compact:
            tokens.add(compact)
        return {token for token in tokens if token}

    def _lexical_similarity(self, left: str, right: str) -> float:
        left_tokens = self._tokenize_text(left)
        right_tokens = self._tokenize_text(right)
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def _strip_discourse_prefix(self, text: str) -> str:
        result = self._normalize_free_text(text)
        changed = True
        while changed and result:
            changed = False
            for prefix in _SUMMARY_PREFIXES:
                if result.startswith(prefix):
                    result = result[len(prefix):].strip(" ：:，,")
                    changed = True
        result = re.sub(r"^(在.+情况下)", "", result).strip(" ：:，,")
        return self._normalize_free_text(result)

    def _llm_available(self) -> bool:
        return bool(
            not self.offline_mode
            and self.llm_client.has_generation_api()
            and self.llm_client.has_valid_api_key()
            and self._system_prompt
            and self._user_prompt
        )
