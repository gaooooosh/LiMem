# -*- coding: utf-8 -*-
"""Context - Agent 从感知流中观察并归纳出的背景框架。

Context 来自 Agent 对任意来源感知流的观察，不等同于事件动作或用户意图。
它描述明确主体在某段时间内可复用的发生条件、环境实况和背景边界。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import json
import re

from ..utils import safe_json_loads

ALLOWED_CONTEXT_SUBTYPES = {
    "situation",
    "constraint",
    "environment",
}
ALLOWED_CONTEXT_STATUSES = {"active", "weakened", "deprecated", "merged"}

_SUBTYPE_ALIASES = {
    "situational": "situation",
    "scene": "situation",
    "scenario": "situation",
    "context": "situation",
    "state": "situation",
    "status": "situation",
    "condition": "situation",
    "phase": "situation",
    "stage": "situation",
    "limit": "constraint",
    "restriction": "constraint",
    "resource": "constraint",
    "external_environment": "environment",
    "env": "environment",
    "goal": "situation",
    "objective": "situation",
    "intent": "situation",
    "target": "situation",
    "preference": "situation",
    "like": "situation",
    "dislike": "situation",
    "relationship": "situation",
    "social": "situation",
    "role": "situation",
    "emotion": "situation",
    "feeling": "situation",
    "mood": "situation",
    "affective": "situation",
    "capability": "situation",
    "ability": "situation",
    "profile": "situation",
    "profile_context": "situation",
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


def _normalize_context_description(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ，,。；;：:")
    return text[:512]


def _normalize_context_facts(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, str):
        value = safe_json_loads(value, {})
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for key, val in value.items():
        text_key = str(key or "").strip()
        if not text_key:
            continue
        if val in (None, "", [], {}):
            continue
        result[text_key[:64]] = val
    return result


def _normalize_context_short_text(value: Any, limit: int = 128) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ，,。；;：:")
    return text[:limit]


def render_context_description(
    condition: str = "",
    facts: Optional[dict[str, Any]] = None,
    applies_when: str = "",
    fallback: str = "",
) -> str:
    parts: list[str] = []
    condition = _normalize_context_short_text(condition, 192)
    applies_when = _normalize_context_short_text(applies_when, 192)
    facts = _normalize_context_facts(facts)
    if condition:
        parts.append(f"背景条件：{condition}")
    if facts:
        facts_text = "；".join(f"{key}：{value}" for key, value in facts.items())
        parts.append(f"实况：{facts_text}")
    if applies_when:
        parts.append(f"适用：{applies_when}")
    fallback = _normalize_context_description(fallback)
    if fallback and fallback not in " ".join(parts):
        parts.append(fallback)
    return _normalize_context_description("。".join(parts))


@dataclass
class ContextSpan:
    """Agent 从观测中圈出的、可能描述情境条件的候选片段。"""

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
    """Agent 为情境条件生成的稳定匹配键。"""

    context_type: str = "context"
    subtype: str = "situation"
    summary: str = ""

    def __post_init__(self) -> None:
        self.context_type = normalize_context_type(self.context_type)
        self.subtype = normalize_context_subtype(self.subtype)
        self.summary = str(self.summary or "").strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_type": self.context_type,
            "subtype": self.subtype,
            "summary": self.summary,
        }


@dataclass
class ContextDraft:
    """Agent 在图解析前形成的情境条件草稿。"""

    subtype: str = "situation"
    summary: str = ""
    description: str = ""
    subject: str = ""
    condition: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    applies_when: str = ""
    confidence: float = 0.6
    evidence_span: str = ""
    context_type: str = "context"
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    valid_from: int = 0
    valid_to: Optional[int] = None
    canonical_key: Optional[CanonicalContextKey] = None
    _raw_summary_length: int = field(init=False, repr=False, default=0)
    _raw_condition_length: int = field(init=False, repr=False, default=0)
    _raw_description_length: int = field(init=False, repr=False, default=0)

    def __post_init__(self) -> None:
        raw_summary = str(self.summary or "")
        raw_condition = str(self.condition or "")
        raw_description = str(self.description or "")
        self._raw_summary_length = len(raw_summary)
        self._raw_condition_length = len(raw_condition)
        self._raw_description_length = len(raw_description)
        self.context_type = normalize_context_type(self.context_type)
        self.subtype = normalize_context_subtype(self.subtype)
        self.subject = _normalize_context_short_text(self.subject)
        self.condition = _normalize_context_short_text(self.condition or self.summary)
        self.facts = _normalize_context_facts(self.facts)
        self.applies_when = _normalize_context_short_text(self.applies_when, 192)
        self.summary = _normalize_context_short_text(self.summary or self.condition)
        self.description = render_context_description(
            condition=self.condition,
            facts=self.facts,
            applies_when=self.applies_when,
            fallback=self.description,
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
            description=self.description,
            subject=self.subject,
            condition=self.condition,
            facts=dict(self.facts),
            applies_when=self.applies_when,
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
    """Agent 记忆图中的 Context 节点，存储可复用的情境条件卡片。"""

    id: str
    context_type: str = "context"
    subtype: str = "situation"
    summary: str = ""
    description: str = ""
    subject: str = ""
    condition: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    applies_when: str = ""
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
        self.subject = _normalize_context_short_text(self.subject)
        self.condition = _normalize_context_short_text(self.condition or self.summary)
        self.facts = _normalize_context_facts(self.facts)
        self.applies_when = _normalize_context_short_text(self.applies_when, 192)
        self.summary = _normalize_context_short_text(self.summary or self.condition)
        self.description = render_context_description(
            condition=self.condition,
            facts=self.facts,
            applies_when=self.applies_when,
            fallback=self.description,
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
            description=data.get("description", "") or "",
            subject=data.get("subject", "") or "",
            condition=data.get("condition", "") or "",
            facts=safe_json_loads(data.get("facts"), {}),
            applies_when=data.get("applies_when", "") or "",
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
            "description": self.description,
            "subject": self.subject,
            "condition": self.condition,
            "facts": json.dumps(self.facts, ensure_ascii=False),
            "applies_when": self.applies_when,
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
