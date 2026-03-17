# -*- coding: utf-8 -*-
"""Pattern node for dynamic evolution memory graph."""

from dataclasses import dataclass, field
from typing import Any, Optional
import json

from ..utils import safe_json_loads


@dataclass
class Pattern:
    """Pattern node induced from multiple events."""

    id: str
    pattern_type: str = "experience"
    summary: str = ""
    prototype_features: dict[str, Any] = field(default_factory=dict)
    support_count: int = 1
    confidence: float = 0.6
    stability_score: float = 0.5
    drift_score: float = 0.0
    created_at: int = 0
    updated_at: int = 0
    valid_from: int = 0
    valid_to: Optional[int] = None
    last_seen_at: int = 0
    status: str = "active"
    embedding: Optional[list[float]] = None

    @classmethod
    def from_db_row(cls, row: list[Any], columns: list[str]) -> "Pattern":
        data = dict(zip(columns, row))
        return cls(
            id=data.get("id", ""),
            pattern_type=data.get("pattern_type", "experience") or "experience",
            summary=data.get("summary", "") or "",
            prototype_features=safe_json_loads(data.get("prototype_features"), {}),
            support_count=int(data.get("support_count", 1) or 1),
            confidence=float(data.get("confidence", 0.6) or 0.6),
            stability_score=float(data.get("stability_score", 0.5) or 0.5),
            drift_score=float(data.get("drift_score", 0.0) or 0.0),
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
            "pattern_type": self.pattern_type,
            "summary": self.summary,
            "prototype_features": json.dumps(self.prototype_features, ensure_ascii=False),
            "support_count": int(self.support_count),
            "confidence": float(self.confidence),
            "stability_score": float(self.stability_score),
            "drift_score": float(self.drift_score),
            "created_at": int(self.created_at),
            "updated_at": int(self.updated_at),
            "valid_from": int(self.valid_from),
            "valid_to": self.valid_to,
            "last_seen_at": int(self.last_seen_at),
            "status": self.status,
            "embedding": self.embedding,
        }
