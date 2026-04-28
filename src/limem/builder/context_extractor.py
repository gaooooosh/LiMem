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
        return _PreparedContextRequest(
            record_text=record_text,
            event=event,
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

    # detect_context_candidates() removed — the LLM works directly from
    # observation text + event without a rule-based candidate pre-filter.

    def llm_extract_contexts(
        self,
        record_text: str,
        event: Optional[Event],
        existing_contexts: Optional[list[dict[str, Any]]] = None,
    ) -> list[ContextDraft]:
        parsed = self._call_context_llm_json(
            self._build_context_user_message(
                record_text=record_text,
                event=event,
                existing_contexts=existing_contexts,
            )
        )
        raw_contexts = parsed.get("contexts", []) if isinstance(parsed, dict) else []
        return self._build_context_drafts_from_raw(
            raw_contexts=raw_contexts,
            event=event,
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
                )

        return True, drafts_by_index

    def _build_context_user_message(
        self,
        record_text: str,
        event: Optional[Event],
        existing_contexts: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        return self._user_prompt.format(
            record_text=record_text or "",
            event_json=safe_json_dumps(self._event_payload(event)),
            existing_contexts_section=self._existing_contexts_section(existing_contexts),
        )

    def _build_context_batch_user_message(
        self,
        prepared_requests: list[_PreparedContextRequest],
    ) -> str:
        items = []
        has_existing_contexts = False
        for idx, prepared in enumerate(prepared_requests):
            item: dict[str, Any] = {
                "item_index": idx,
                "record_text": prepared.record_text or "",
                "event": self._event_payload(prepared.event),
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
            "\n\n你记忆中的已有情境条件（仅在真正语义等价时，复用完全相同的 summary；"
            "否则正常抽取新的抽象 summary）：\n"
            + safe_json_dumps(existing_contexts)
            + "\n\n补充规则：\n"
            "- 如果你记忆中的某个已有 Context 与当前输入等价，优先直接使用该 summary，保持字面完全一致。\n"
            "- 如果没有等价项，正常返回新的抽象 summary。\n"
            "- 不要因为主题相近或处于同一大类场景就强行复用。"
        )

    def _existing_contexts_note(self, has_existing_contexts: bool) -> str:
        if not has_existing_contexts:
            return ""
        return (
            "补充字段说明：item 中可能包含你记忆里的 existing_contexts。\n"
            "只有在与其中某一项真正语义等价时，才复用该项的 summary，并保持字面完全一致；\n"
            "否则正常产出新的抽象 summary。不要因为主题相关就复用。"
        )

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
    ) -> list[ContextDraft]:
        if not isinstance(raw_contexts, list):
            return []

        drafts: list[ContextDraft] = []
        for item in raw_contexts:
            if not isinstance(item, dict) or item.get("not_context") is True:
                continue
            subtype = normalize_context_subtype(item.get("subtype", "situation"))
            summary = str(item.get("summary", "") or "").strip()
            description = str(item.get("description", "") or "").strip()
            evidence_span = str(item.get("evidence_span", "") or "").strip()
            drafts.append(
                ContextDraft(
                    subtype=subtype,
                    summary=summary,
                    description=description,
                    confidence=float(item.get("confidence", 0.6) or 0.6),
                    evidence_span=evidence_span,
                    source_refs=self._make_source_refs(
                        event=event,
                        evidence_span=evidence_span,
                        signal="llm_context",
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
            if not self.is_valid_context_draft(draft):
                continue
            if self._looks_like_current_intent(draft):
                continue

            # Use full evidence text for matching — don't truncate before validation.
            raw_evidence = self._normalize_whitespace(draft.evidence_span or draft.summary)
            if raw_evidence and record_text and not self._evidence_matches_text(raw_evidence, record_text):
                continue

            evidence_span = self._normalize_description(raw_evidence) if raw_evidence else self._normalize_free_text(draft.summary)
            validated.append(
                ContextDraft(
                    subtype=normalize_context_subtype(draft.subtype),
                    summary=self._normalize_free_text(draft.summary),
                    description=self._normalize_description(
                        draft.description or draft.evidence_span or draft.summary
                    ),
                    confidence=max(0.0, min(1.0, float(draft.confidence or 0.0))),
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

    def is_valid_context_draft(self, draft: ContextDraft) -> bool:
        if not isinstance(draft, ContextDraft):
            return False
        summary = str(draft.summary or "").strip()
        if not summary:
            return False
        if len(summary) > 128:
            return False
        if any(ch in summary for ch in ('{', '}', '"')):
            return False
        if len(str(draft.description or "").strip()) > 512:
            return False
        return True

    def _looks_like_current_intent(self, draft: ContextDraft) -> bool:
        text = " ".join(
            str(part or "")
            for part in (draft.summary, draft.evidence_span)
        ).lower()
        intent_markers = (
            "想", "希望", "打算", "计划", "准备", "请求", "要求", "需要",
            "我要", "我想", "帮我", "请", "为了", "目标", "意图",
            "want", "wants", "hope", "hopes", "plan", "plans", "intend",
            "intends", "request", "requests", "need", "needs", "goal",
        )
        stable_markers = (
            "长期", "稳定", "习惯", "偏好", "经常", "通常", "常常", "画像",
            "身份", "角色", "能力", "关系", "long-term", "stable", "habit",
            "usually", "preference", "profile", "role", "capability",
        )
        return any(marker in text for marker in intent_markers) and not any(
            marker in text for marker in stable_markers
        )

    def canonicalize_context(self, context_draft: ContextDraft) -> ContextDraft:
        subtype = normalize_context_subtype(context_draft.subtype)
        summary = self._canonicalize_summary(context_draft.summary, context_draft.evidence_span)
        description = self._normalize_description(
            context_draft.description or context_draft.evidence_span or summary
        )
        canonical_key = CanonicalContextKey(
            context_type="context",
            subtype=subtype,
            summary=summary,
        )
        return ContextDraft(
            subtype=subtype,
            summary=summary,
            description=description,
            confidence=context_draft.confidence,
            evidence_span=context_draft.evidence_span or context_draft.summary,
            source_refs=context_draft.source_refs,
            valid_from=context_draft.valid_from,
            valid_to=context_draft.valid_to,
            canonical_key=canonical_key,
        )

    def _canonicalize_summary(self, summary: str, evidence_span: str) -> str:
        return self._normalize_free_text(summary or evidence_span)

    def _dedupe_drafts(self, drafts: list[ContextDraft]) -> list[ContextDraft]:
        deduped: list[ContextDraft] = []
        seen: set[str] = set()
        for draft in drafts:
            canonical = draft.canonical_key or CanonicalContextKey(
                subtype=draft.subtype,
                summary=draft.summary,
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

    def _normalize_whitespace(self, text: Any) -> str:
        """Normalize whitespace and trailing punctuation without truncation."""
        return re.sub(r"\s+", " ", str(text or "")).strip(" ，,。；;：:")

    def _normalize_free_text(self, text: Any) -> str:
        return self._normalize_whitespace(text)[:128]

    def _normalize_description(self, text: Any) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip(" ，,。；;：:")
        return normalized[:512]

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
