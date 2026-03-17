# -*- coding: utf-8 -*-
"""Context models for the dynamic long-term memory graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import json

from ..utils import safe_json_loads

ALLOWED_CONTEXT_SUBTYPES = {
    "situation",
    "state",
    "constraint",
    "goal",
    "environment",
    "phase",
}
ALLOWED_CONTEXT_STATUSES = {"active", "weakened", "deprecated", "merged"}

_SUBTYPE_ALIASES = {
    "situational": "situation",
    "scene": "situation",
    "scenario": "situation",
    "context": "situation",
    "status": "state",
    "condition": "state",
    "limit": "constraint",
    "restriction": "constraint",
    "objective": "goal",
    "intent": "goal",
    "target": "goal",
    "external_environment": "environment",
    "env": "environment",
    "stage": "phase",
}
_STATUS_ALIASES = {
    "inactive": "deprecated",
    "archived": "deprecated",
    "removed": "deprecated",
}


def normalize_context_type(_: Any) -> str:
    """Context now has a single high-level type."""
    return "context"


def normalize_context_subtype(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if text in ALLOWED_CONTEXT_SUBTYPES:
        return text
    text = _SUBTYPE_ALIASES.get(text, text)
    if text in ALLOWED_CONTEXT_SUBTYPES:
        return text
    return "situation"


def normalize_context_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = _STATUS_ALIASES.get(text, text)
    if text in ALLOWED_CONTEXT_STATUSES:
        return text
    return "active"


def _normalize_source_refs(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, str):
        value = safe_json_loads(value, [])
    if not isinstance(value, list):
        value = [value]
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            normalized = {str(key): val for key, val in item.items() if key not in (None, "")}
            if normalized:
                result.append(normalized)
        else:
            text = str(item).strip()
            if text:
                result.append({"source": text})
    return result


def _normalize_merged_from(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        parsed = safe_json_loads(value, None)
        if isinstance(parsed, list):
            value = parsed
        elif value.strip():
            value = [value]
        else:
            value = []
    if not isinstance(value, list):
        value = [value]
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _sorted_slots(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sorted_slots(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, list):
        return [_sorted_slots(item) for item in value]
    return value


@dataclass
class ContextSpan:
    """Candidate text span that may describe an event condition."""

    text: str
    signal: str = ""
    subtype_hint: str = ""
    source: str = "record"
    start: int = -1
    end: int = -1

    def __post_init__(self) -> None:
        self.text = str(self.text or "").strip()
        self.signal = str(self.signal or "").strip()
        self.subtype_hint = normalize_context_subtype(self.subtype_hint)
        self.source = str(self.source or "record").strip() or "record"


@dataclass
class CanonicalContextKey:
    """Stable matching key produced from a ContextDraft."""

    context_type: str = "context"
    subtype: str = "situation"
    summary: str = ""
    structured_slots: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.context_type = normalize_context_type(self.context_type)
        self.subtype = normalize_context_subtype(self.subtype)
        self.summary = str(self.summary or "").strip()
        self.structured_slots = _sorted_slots(
            dict(self.structured_slots) if isinstance(self.structured_slots, dict) else {}
        )

    @property
    def slot_signature(self) -> str:
        return json.dumps(self.structured_slots, ensure_ascii=False, sort_keys=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_type": self.context_type,
            "subtype": self.subtype,
            "summary": self.summary,
            "structured_slots": self.structured_slots,
            "slot_signature": self.slot_signature,
        }


@dataclass
class ContextDraft:
    """Draft extracted from raw text or an Event before graph resolution."""

    subtype: str = "situation"
    summary: str = ""
    structured_slots: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.6
    evidence_span: str = ""
    context_type: str = "context"
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    valid_from: int = 0
    valid_to: Optional[int] = None
    canonical_key: Optional[CanonicalContextKey] = None

    def __post_init__(self) -> None:
        self.context_type = normalize_context_type(self.context_type)
        self.subtype = normalize_context_subtype(self.subtype)
        self.summary = str(self.summary or "").strip()
        self.structured_slots = _sorted_slots(
            dict(self.structured_slots) if isinstance(self.structured_slots, dict) else {}
        )
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))
        self.evidence_span = str(self.evidence_span or "").strip()
        self.source_refs = _normalize_source_refs(self.source_refs)
        if self.canonical_key and not isinstance(self.canonical_key, CanonicalContextKey):
            self.canonical_key = CanonicalContextKey(**dict(self.canonical_key))

    def to_node(self, context_id: str, timestamp: int, embedding: Optional[list[float]] = None) -> "Context":
        return Context(
            id=context_id,
            context_type=self.context_type,
            subtype=self.subtype,
            summary=self.summary,
            structured_slots=self.structured_slots,
            confidence=self.confidence,
            support_count=1,
            created_at=timestamp,
            updated_at=timestamp,
            valid_from=self.valid_from or timestamp,
            valid_to=self.valid_to,
            last_seen_at=timestamp,
            status="active",
            source_refs=list(self.source_refs),
            embedding=embedding,
        )


@dataclass
class Context:
    """Context node that stores reusable conditions for events."""

    id: str
    context_type: str = "context"
    subtype: str = "situation"
    summary: str = ""
    structured_slots: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.6
    support_count: int = 1
    created_at: int = 0
    updated_at: int = 0
    valid_from: int = 0
    valid_to: Optional[int] = None
    last_seen_at: int = 0
    status: str = "active"
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    merged_from: list[str] = field(default_factory=list)
    embedding: Optional[list[float]] = None

    def __post_init__(self) -> None:
        self.context_type = normalize_context_type(self.context_type)
        self.subtype = normalize_context_subtype(self.subtype)
        self.summary = str(self.summary or "").strip()
        self.structured_slots = _sorted_slots(
            dict(self.structured_slots) if isinstance(self.structured_slots, dict) else {}
        )
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))
        self.support_count = max(1, int(self.support_count or 1))
        self.created_at = int(self.created_at or 0)
        self.updated_at = int(self.updated_at or self.created_at or 0)
        self.valid_from = int(self.valid_from or self.created_at or 0)
        self.last_seen_at = int(self.last_seen_at or self.updated_at or self.valid_from or 0)
        self.status = normalize_context_status(self.status)
        self.source_refs = _normalize_source_refs(self.source_refs)
        self.merged_from = _normalize_merged_from(self.merged_from)
        if self.embedding is not None:
            self.embedding = list(self.embedding)

    @classmethod
    def from_db_row(cls, row: list[Any], columns: list[str]) -> "Context":
        data = dict(zip(columns, row))
        return cls(
            id=data.get("id", ""),
            context_type=data.get("context_type", "context"),
            subtype=data.get("subtype", "situation"),
            summary=data.get("summary", "") or "",
            structured_slots=safe_json_loads(data.get("structured_slots"), {}),
            confidence=float(data.get("confidence", 0.6) or 0.6),
            support_count=int(data.get("support_count", 1) or 1),
            created_at=int(data.get("created_at", 0) or 0),
            updated_at=int(data.get("updated_at", 0) or 0),
            valid_from=int(data.get("valid_from", 0) or 0),
            valid_to=data.get("valid_to"),
            last_seen_at=int(data.get("last_seen_at", 0) or 0),
            status=data.get("status", "active") or "active",
            source_refs=safe_json_loads(data.get("source_refs"), []),
            merged_from=safe_json_loads(data.get("merged_from"), []),
            embedding=list(data.get("embedding")) if data.get("embedding") else None,
        )

    def to_db_fields(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "context_type": self.context_type,
            "subtype": self.subtype,
            "summary": self.summary,
            "structured_slots": json.dumps(self.structured_slots, ensure_ascii=False, sort_keys=True),
            "confidence": float(self.confidence),
            "support_count": int(self.support_count),
            "created_at": int(self.created_at),
            "updated_at": int(self.updated_at),
            "valid_from": int(self.valid_from),
            "valid_to": self.valid_to,
            "last_seen_at": int(self.last_seen_at),
            "status": self.status,
            "source_refs": json.dumps(self.source_refs, ensure_ascii=False),
            "merged_from": json.dumps(self.merged_from, ensure_ascii=False),
            "embedding": self.embedding,
        }


ContextNode = Context
