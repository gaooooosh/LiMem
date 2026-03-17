# -*- coding: utf-8 -*-
"""LLM-first context extraction pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import json
import re

try:
    import dashscope
    from dashscope import Generation
except Exception:  # pragma: no cover - optional dependency for offline mode
    dashscope = None
    Generation = None

from ..config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    GENERATION_MODEL,
)
from ..core.context import (
    ALLOWED_CONTEXT_SUBTYPES,
    CanonicalContextKey,
    ContextDraft,
    ContextSpan,
    normalize_context_subtype,
)
from ..core.event import Event
from ..utils import load_prompt, robust_json_loads, safe_json_dumps

_HABIT_LIKE_MARKERS = ("通常", "经常", "总是", "一向", "偏好", "习惯")
_EVENT_LIKE_MARKERS = (
    "打开", "搜索", "开始", "完成", "成功", "失败", "找到", "支付", "导航", "播放", "推荐", "决定",
)
_CONTEXT_SIGNAL_MARKERS = (
    "由于", "因为", "当前", "目前", "此时", "只剩", "仅剩", "不足", "需要", "希望", "目标", "阶段",
    "环境", "状态", "网络", "电量", "限制", "条件", "情况下", "场景",
)
_GENERIC_CLAUSE_PATTERNS: list[tuple[str, str, str]] = [
    (r"由于[^，。；,;]+", "constraint", "causal_clause"),
    (r"因为[^，。；,;]+", "constraint", "causal_clause"),
    (r"在[^，。；,;]{1,40}情况下", "situation", "situation_clause"),
    (r"(当前|目前|此时)[^，。；,;]+", "state", "current_clause"),
    (r"(只剩|仅剩|不足)[^，。；,;]+", "constraint", "remaining_clause"),
    (r"(需要|希望|想要|目标是)[^，。；,;]+", "goal", "goal_clause"),
    (r"(正在|处于)[^，。；,;]{0,24}(阶段|中)", "phase", "phase_clause"),
    (r"(执行中|准备中|进行中|等待中|收尾中)", "phase", "phase_clause"),
]
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


@dataclass
class ContextExtractionPipeline:
    """Extract reusable ContextDraft objects from record/Event input."""

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    generation_model: Optional[str] = None
    offline_mode: bool = False

    def __post_init__(self) -> None:
        self.api_key = self.api_key or DASHSCOPE_API_KEY
        self.base_url = self.base_url or DASHSCOPE_BASE_URL
        self.generation_model = self.generation_model or GENERATION_MODEL
        self._system_prompt = load_prompt("extract_context_system.txt")
        self._user_prompt = load_prompt("extract_context_user.txt")

    def extract(self, record: Any, event: Optional[Event] = None) -> list[ContextDraft]:
        record_text = self._record_text(record, event)
        candidate_spans = self.detect_context_candidates(record, event)
        drafts = self.llm_extract_contexts(record_text, event, candidate_spans)
        if not drafts:
            drafts = self._fallback_extract_contexts(record_text, event, candidate_spans)
        validated = self.validate_context_drafts(drafts, record_text, event)
        canonicalized = [self.canonicalize_context(draft) for draft in validated]
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
            for pattern, subtype, signal in _GENERIC_CLAUSE_PATTERNS:
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
        return spans[:8]

    def llm_extract_contexts(
        self,
        record_text: str,
        event: Optional[Event],
        candidate_spans: list[ContextSpan],
    ) -> list[ContextDraft]:
        if not self._llm_available() or not candidate_spans:
            return []

        user_msg = self._user_prompt.format(
            record_text=record_text or "",
            event_json=safe_json_dumps(self._event_payload(event)),
            candidate_spans_json=safe_json_dumps(
                [
                    {
                        "text": span.text,
                        "signal": span.signal,
                        "subtype_hint": span.subtype_hint,
                        "source": span.source,
                    }
                    for span in candidate_spans
                ]
            ),
        )
        try:
            dashscope.base_http_api_url = self.base_url
            dashscope.api_key = self.api_key
            resp = Generation.call(
                api_key=self.api_key,
                model=self.generation_model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                result_format="message",
                enable_thinking=False,
            )
            if getattr(resp, "status_code", None) != 200:
                return []
            parsed = robust_json_loads(resp.output.choices[0].message.content, {})
        except Exception:
            return []

        raw_contexts = parsed.get("contexts", []) if isinstance(parsed, dict) else []
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

            evidence_span = draft.evidence_span or draft.summary
            if not self._evidence_matches_text(evidence_span, record_text):
                continue
            if self._looks_like_event_or_result(draft.summary, event):
                continue
            if self._looks_like_habit_not_context(evidence_span):
                continue

            cleaned_slots = self._clean_structured_slots(draft.structured_slots)
            if not cleaned_slots:
                slot_key = _FALLBACK_SLOT_KEYS.get(draft.subtype, "condition")
                cleaned_slots = {slot_key: self._normalize_free_text(draft.summary)}

            validated.append(
                ContextDraft(
                    subtype=draft.subtype,
                    summary=self._normalize_free_text(draft.summary),
                    structured_slots=cleaned_slots,
                    confidence=max(0.1, min(1.0, draft.confidence)),
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
            )
        return validated

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
            drafts.append(
                ContextDraft(
                    subtype=normalize_context_subtype(span.subtype_hint),
                    summary=normalized_text,
                    structured_slots={
                        _FALLBACK_SLOT_KEYS.get(
                            normalize_context_subtype(span.subtype_hint),
                            "condition",
                        ): normalized_text
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
            )

        if drafts or not record_text:
            return drafts

        for clause, start, end in self._split_text_clauses(record_text):
            if len(clause) < 4:
                continue
            if self._looks_like_event_or_result(clause, event):
                continue
            drafts.append(
                ContextDraft(
                    subtype="situation",
                    summary=self._normalize_free_text(clause),
                    structured_slots={"situation": self._normalize_free_text(clause)},
                    confidence=0.4,
                    evidence_span=clause,
                    source_refs=self._make_source_refs(
                        event=event,
                        evidence_span=clause,
                        signal="fallback_clause",
                        source="fallback_context_extraction",
                    ),
                    valid_from=self._event_timestamp(event),
                )
            )
            if len(drafts) >= 2:
                break
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

    def _looks_like_event_or_result(self, text: str, event: Optional[Event]) -> bool:
        normalized = self._normalize_free_text(text)
        if not normalized:
            return True
        has_context_signal = any(marker in normalized for marker in _CONTEXT_SIGNAL_MARKERS)
        if any(marker in normalized for marker in _EVENT_LIKE_MARKERS):
            if not has_context_signal:
                return True
        if event is not None:
            event_text = " ".join(part for part in [event.summary, event.action, event.causality] if part)
            lexical_sim = self._lexical_similarity(normalized, event_text)
            if lexical_sim >= (0.90 if has_context_signal else 0.84):
                return True
            if normalized == self._normalize_free_text(event.summary) or normalized == self._normalize_free_text(event.action):
                return True
        return False

    def _looks_like_habit_not_context(self, text: str) -> bool:
        normalized = str(text or "").strip()
        return any(marker in normalized for marker in _HABIT_LIKE_MARKERS)

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
        api_key = str(self.api_key or "").strip()
        return bool(
            not self.offline_mode
            and dashscope is not None
            and Generation is not None
            and api_key
            and api_key not in {"YOUR_API_KEY", "sk-xxx"}
            and self._system_prompt
            and self._user_prompt
        )
