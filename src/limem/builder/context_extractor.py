# -*- coding: utf-8 -*-
"""LLM-first context extraction pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import json
import logging
import re

from ..config import (
    CONTEXT_EXTRACTION_BATCH_SIZE,
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    GENERATION_MODEL,
    normalize_dashscope_base_url,
)
from ..core.context import (
    ALLOWED_CONTEXT_SUBTYPES,
    CanonicalContextKey,
    ContextDraft,
    ContextSpan,
    normalize_context_subtype,
)
from ..core.event import Event
from ..llm import DashScopeClient
from ..utils import load_prompt, robust_json_loads, safe_json_dumps

logger = logging.getLogger(__name__)


@dataclass
class _PreparedContextRequest:
    record_text: str
    event: Optional[Event]
    candidate_spans: list[ContextSpan]
    existing_contexts: Optional[list[dict[str, Any]]] = None


@dataclass
class ContextExtractionPipeline:
    """Extract reusable ContextDraft objects from record/Event input."""

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    generation_model: Optional[str] = None
    llm_client: Optional[DashScopeClient] = None

    def __post_init__(self) -> None:
        self.generation_model = self.generation_model or GENERATION_MODEL
        if self.llm_client is None:
            self.api_key = self.api_key or DASHSCOPE_API_KEY
            self.base_url = normalize_dashscope_base_url(
                self.base_url or DASHSCOPE_BASE_URL
            )
            self.llm_client = DashScopeClient(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        self._system_prompt = load_prompt("extract_context_system.txt")
        self._user_prompt = load_prompt("extract_context_user.txt")
        self._batch_user_prompt = load_prompt("extract_context_batch_user.txt")

    def extract(
        self,
        record: Any,
        event: Optional[Event] = None,
        existing_contexts: Optional[list[dict[str, Any]]] = None,
    ) -> list[ContextDraft]:
        prepared = self._prepare_context_request(
            record=record,
            event=event,
            existing_contexts=existing_contexts,
        )
        llm_drafts = self.llm_extract_contexts(
            prepared.record_text,
            prepared.event,
            prepared.candidate_spans,
            existing_contexts=prepared.existing_contexts,
        )
        return self._finalize_context_extraction(prepared=prepared, llm_drafts=llm_drafts)

    def extract_batch(
        self,
        records: list[Any],
        events: list[Optional[Event]],
        existing_contexts_by_index: Optional[dict[int, list[dict[str, Any]]]] = None,
    ) -> list[list[ContextDraft]]:
        if not records or not events or len(records) != len(events):
            return []
        if len(events) == 1:
            return [
                self.extract(
                    records[0],
                    event=events[0],
                    existing_contexts=(existing_contexts_by_index or {}).get(0),
                )
            ]

        prepared_requests = [
            self._prepare_context_request(
                record=record,
                event=event,
                existing_contexts=(existing_contexts_by_index or {}).get(idx),
            )
            for idx, (record, event) in enumerate(zip(records, events))
        ]

        batch_used, llm_drafts_by_index = self.llm_extract_contexts_batch(prepared_requests)
        if not batch_used:
            return [
                self.extract(
                    record,
                    event=event,
                    existing_contexts=prepared.existing_contexts,
                )
                for record, event, prepared in zip(records, events, prepared_requests)
            ]

        return [
            (
                self._finalize_context_extraction(
                    prepared=prepared,
                    llm_drafts=llm_drafts_by_index[idx],
                )
                if idx in llm_drafts_by_index
                else self.extract(
                    records[idx],
                    event=events[idx],
                    existing_contexts=prepared.existing_contexts,
                )
            )
            for idx, prepared in enumerate(prepared_requests)
        ]

    def _prepare_context_request(
        self,
        record: Any,
        event: Optional[Event],
        existing_contexts: Optional[list[dict[str, Any]]] = None,
    ) -> _PreparedContextRequest:
        record_text = self._record_text(record, event)
        candidate_spans = self.detect_context_candidates(record, event)
        return _PreparedContextRequest(
            record_text=record_text,
            event=event,
            candidate_spans=candidate_spans,
            existing_contexts=(
                [dict(item) for item in existing_contexts if isinstance(item, dict)]
                if existing_contexts
                else None
            ),
        )

    def _finalize_context_extraction(
        self,
        prepared: _PreparedContextRequest,
        llm_drafts: Optional[list[ContextDraft]] = None,
    ) -> list[ContextDraft]:
        validated = self.validate_context_drafts(
            llm_drafts or [],
            prepared.record_text,
            prepared.event,
        )
        canonicalized = [
            draft
            for draft in (self.canonicalize_context(item) for item in validated)
            if self.is_valid_context_draft(draft)
        ]
        return self._dedupe_drafts(canonicalized)

    def detect_context_candidates(
        self,
        record: Any,
        event: Optional[Event] = None,
    ) -> list[ContextSpan]:
        text = self._record_text(record, event)
        spans: list[ContextSpan] = []
        seen: set[tuple[str, str]] = set()

        def add_span(text_value: Any, signal: str, subtype_hint: str, source: str) -> None:
            candidate = self._normalize_free_text(text_value)
            if not candidate:
                return
            key = (candidate, signal)
            if key in seen:
                return
            seen.add(key)
            spans.append(
                ContextSpan(
                    text=candidate,
                    signal=signal,
                    subtype_hint=subtype_hint,
                    source=source,
                )
            )

        if text:
            add_span(text, "record", "situation", "record")

        if event is not None and isinstance(event.payload, dict):
            payload = event.payload
            for key, subtype in (
                ("context_note", "situation"),
                ("state", "state"),
                ("constraint", "constraint"),
                ("goal", "goal"),
                ("phase", "phase"),
                ("environment", "environment"),
            ):
                add_span(payload.get(key), f"event_payload:{key}", subtype, "event")

            context_payload = payload.get("context", {})
            if isinstance(context_payload, dict):
                for key, subtype in (
                    ("scene", "situation"),
                    ("state", "state"),
                    ("constraint", "constraint"),
                    ("goal", "goal"),
                    ("phase", "phase"),
                    ("environment", "environment"),
                    ("geo_context", "environment"),
                    ("digital_context", "environment"),
                ):
                    add_span(
                        context_payload.get(key),
                        f"event_payload:context.{key}",
                        subtype,
                        "event",
                    )

        return spans[: max(1, CONTEXT_EXTRACTION_BATCH_SIZE)]

    def llm_extract_contexts(
        self,
        record_text: str,
        event: Optional[Event],
        candidate_spans: list[ContextSpan],
        existing_contexts: Optional[list[dict[str, Any]]] = None,
    ) -> list[ContextDraft]:
        parsed = self._call_context_llm_json(
            self._build_context_user_message(
                record_text=record_text,
                event=event,
                candidate_spans=candidate_spans,
                existing_contexts=existing_contexts,
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
                    "context batch extraction returned %s items for %s requests",
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
                    logger.warning("invalid context batch item index: %s", exc)
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
        existing_contexts: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        return self._user_prompt.format(
            record_text=record_text or "",
            event_json=safe_json_dumps(self._event_payload(event)),
            candidate_spans_json=safe_json_dumps(
                self._candidate_spans_payload(candidate_spans)
            ),
            existing_contexts_section=self._existing_contexts_section(existing_contexts),
        )

    def _build_context_batch_user_message(
        self,
        prepared_requests: list[_PreparedContextRequest],
    ) -> str:
        items = []
        has_existing_contexts = False
        for idx, prepared in enumerate(prepared_requests):
            item = {
                "item_index": idx,
                "record_text": prepared.record_text or "",
                "event": self._event_payload(prepared.event),
                "candidate_spans": self._candidate_spans_payload(prepared.candidate_spans),
            }
            if prepared.existing_contexts:
                item["existing_contexts"] = prepared.existing_contexts
                has_existing_contexts = True
            items.append(item)
        return self._batch_user_prompt.format(
            item_total=len(prepared_requests),
            items_json=safe_json_dumps(items),
            existing_contexts_note=self._existing_contexts_note(has_existing_contexts),
        )

    def _existing_contexts_section(
        self,
        existing_contexts: Optional[list[dict[str, Any]]],
    ) -> str:
        if not existing_contexts:
            return ""
        return (
            "\n\n可复用已有 Context（仅在真正语义等价时，复用完全相同的 summary；"
            "否则正常抽取新的抽象 summary）：\n"
            + safe_json_dumps(existing_contexts)
            + "\n\n补充规则：\n"
            "- 如果某个已有 Context 与当前输入等价，优先直接使用该 summary，保持字面完全一致。\n"
            "- 如果没有等价项，正常返回新的抽象 summary。\n"
            "- 不要因为主题相近或处于同一大类场景就强行复用。"
        )

    def _existing_contexts_note(self, has_existing_contexts: bool) -> str:
        if not has_existing_contexts:
            return ""
        return (
            "补充字段说明：item 中可能包含 existing_contexts。\n"
            "只有在与其中某一项真正语义等价时，才复用该项的 summary，并保持字面完全一致；\n"
            "否则正常产出新的抽象 summary。不要因为主题相关就复用。"
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
            )
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
            if not isinstance(draft.structured_slots, dict):
                continue
            if not self.is_valid_context_draft(draft):
                continue

            cleaned_slots = self._clean_structured_slots(draft.structured_slots)
            if not cleaned_slots:
                slot_key = self._default_slot_key(draft.subtype)
                slot_value = self._normalize_free_text(draft.summary or draft.evidence_span)
                if slot_value:
                    cleaned_slots = {slot_key: slot_value}

            evidence_span = self._normalize_free_text(draft.evidence_span or draft.summary)
            if evidence_span and record_text and not self._evidence_matches_text(evidence_span, record_text):
                continue

            validated.append(
                ContextDraft(
                    subtype=normalize_context_subtype(draft.subtype),
                    summary=self._normalize_free_text(draft.summary),
                    structured_slots=cleaned_slots,
                    confidence=max(0.0, min(1.0, float(draft.confidence or 0.0))),
                    evidence_span=evidence_span or self._normalize_free_text(draft.summary),
                    source_refs=draft.source_refs or self._make_source_refs(
                        event=event,
                        evidence_span=evidence_span or self._normalize_free_text(draft.summary),
                        signal="validated_context",
                        source="context_validation",
                    ),
                    valid_from=draft.valid_from or self._event_timestamp(event),
                    valid_to=draft.valid_to,
                )
            )
        return validated

    def is_valid_context_draft(self, draft: ContextDraft) -> bool:
        if not isinstance(draft, ContextDraft):
            return False
        summary = str(draft.summary or "").strip()
        if not summary:
            return False
        if len(summary) > 64:
            return False
        if any(ch in summary for ch in ('{', '}', '"')):
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
        return {key: cleaned[key] for key in sorted(cleaned.keys())}

    def _normalize_slot_key(self, value: Any) -> str:
        text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        return re.sub(r"[^a-z0-9_\u4e00-\u9fff]", "", text)

    def _normalize_slot_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            nested = {
                self._normalize_slot_key(key): self._normalize_slot_value(sub_value)
                for key, sub_value in value.items()
            }
            return {
                key: nested[key]
                for key in sorted(nested.keys())
                if key and nested[key] not in (None, "", [], {})
            }
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
        return self._normalize_free_text(summary or evidence_span)

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
        return overlap / max(1, len(evidence_tokens)) >= 0.5

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

    def _default_slot_key(self, subtype: str) -> str:
        return {
            "situation": "situation",
            "state": "state",
            "constraint": "constraint",
            "goal": "goal",
            "environment": "environment",
            "phase": "phase",
        }.get(normalize_context_subtype(subtype), "condition")

    def _normalize_free_text(self, text: Any) -> str:
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
