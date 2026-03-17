# -*- coding: utf-8 -*-
"""Context node for dynamic evolution memory graph."""

from dataclasses import dataclass, field
from typing import Any, Optional
import json

from ..utils import safe_json_loads


@dataclass
class Context:
    """Context node that evolves over time.

    Context stores stable situation slots. It is not action-led and should not
    compete with Entity for semantic ownership.
    """

    id: str
    context_type: str = "situation"
    subtype: str = "generic"
    summary: str = ""
    # `structured_slots` keeps scene/environment/stage/constraint style slots.
    structured_slots: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.6
    support_count: int = 1
    created_at: int = 0
    updated_at: int = 0
    valid_from: int = 0
    valid_to: Optional[int] = None
    last_seen_at: int = 0
    status: str = "active"
    embedding: Optional[list[float]] = None

    @classmethod
    def from_db_row(cls, row: list[Any], columns: list[str]) -> "Context":
        data = dict(zip(columns, row))
        return cls(
            id=data.get("id", ""),
            context_type=data.get("context_type", "situation") or "situation",
            subtype=data.get("subtype", "generic") or "generic",
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
            embedding=list(data.get("embedding")) if data.get("embedding") else None,
        )

    def to_db_fields(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "context_type": self.context_type,
            "subtype": self.subtype,
            "summary": self.summary,
            "structured_slots": json.dumps(self.structured_slots, ensure_ascii=False),
            "confidence": float(self.confidence),
            "support_count": int(self.support_count),
            "created_at": int(self.created_at),
            "updated_at": int(self.updated_at),
            "valid_from": int(self.valid_from),
            "valid_to": self.valid_to,
            "last_seen_at": int(self.last_seen_at),
            "status": self.status,
            "embedding": self.embedding,
        }
