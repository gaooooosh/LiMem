# -*- coding: utf-8 -*-
"""Event - Agent 对观测的结构化解读。

Agent 从原始观测中识别出的、值得纳入长期记忆的结构化认知单元。
当前主路径为 append-first 写入，事件归并由离线 consolidation 处理。
"""

from dataclasses import dataclass, field
from typing import Optional, Any

from ..utils import hash_summary


@dataclass
class Event:
    """Event - Agent 对单段观测形成的结构化记忆单元。

    Attributes:
        summary: Agent 对这段观测的概括性理解
        action: Agent 识别出的核心动作、变化或决策
        causality: Agent 对原因、结果或影响的因果判断
        participants: Agent 识别出的参与者列表
        evidence: 支撑该解读的观测证据
    """

    # 核心语义字段
    summary: str  # Agent 对这段观测的概括性理解
    id: str = ""
    action: str = ""  # Agent 识别出的核心动作、变化或决策
    causality: str = ""  # Agent 对原因、结果或影响的因果判断

    # 时间信息
    time_range: dict[str, Any] = field(default_factory=dict)
    timestamp: int = 0
    last_active: int = 0
    created_at: int = 0
    updated_at: int = 0
    valid_from: int = 0
    valid_to: Optional[int] = None

    # 参与者与扩展负载
    participants: list[dict[str, Any]] = field(default_factory=list)  # Agent 识别出的参与者
    payload: dict[str, Any] = field(default_factory=dict)

    # 证据与状态
    evidence: list[dict[str, Any]] = field(default_factory=list)  # 支撑该解读的观测证据
    status: str = "active"
    support_count: int = 1

    # 向量嵌入
    embedding: Optional[list[float]] = None

    def __post_init__(self):
        if not self.id and self.summary:
            self.id = hash_summary(self.summary)

        if self.timestamp <= 0:
            self.timestamp = self.last_active
        if self.created_at <= 0:
            self.created_at = self.timestamp
        if self.updated_at <= 0:
            self.updated_at = self.last_active or self.timestamp
        if self.valid_from <= 0:
            self.valid_from = self.timestamp

    @classmethod
    def from_extraction(cls, data: dict[str, Any], current_time: int) -> "Event":
        summary = data.get("summary", "")
        return cls(
            id=hash_summary(summary) if summary else "",
            summary=summary,
            action=data.get("action", ""),
            causality=data.get("causality", ""),
            time_range=data.get("time_range", {}),
            timestamp=current_time,
            last_active=current_time,
            created_at=current_time,
            updated_at=current_time,
            valid_from=current_time,
            participants=data.get("participants", []),
            payload=data,
            evidence=data.get("evidence", []),
            status=str(data.get("status", "active")),
        )

    @classmethod
    def from_db_row(cls, row: list[Any], columns: list[str]) -> "Event":
        from ..utils import safe_json_loads

        data = dict(zip(columns, row))
        return cls(
            id=data.get("id", ""),
            summary=data.get("summary", ""),
            action=data.get("action", ""),
            causality=data.get("causality", ""),
            time_range=safe_json_loads(data.get("time_range"), {}),
            timestamp=data.get("timestamp", 0),
            last_active=data.get("last_active", 0),
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
            valid_from=data.get("valid_from", 0),
            valid_to=data.get("valid_to"),
            participants=safe_json_loads(data.get("participants"), []),
            payload=safe_json_loads(data.get("payload"), {}),
            evidence=safe_json_loads(data.get("evidence"), []),
            status=data.get("status", "active"),
            support_count=int(data.get("support_count", 1) or 1),
            embedding=list(data.get("embedding", [])) if data.get("embedding") else None,
        )

    def merge_with(self, other: "Event", current_time: int) -> "Event":
        return Event(
            id=self.id,
            summary=self.summary,
            action=self.action,
            causality=self.causality,
            time_range=self.time_range,
            timestamp=self.timestamp,
            last_active=current_time,
            created_at=self.created_at,
            updated_at=current_time,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
            participants=self.participants + other.participants,
            payload=self.payload or other.payload,
            evidence=self.evidence + other.evidence,
            status=self.status,
            support_count=self.support_count + other.support_count,
            embedding=self.embedding,
        )

    def to_db_fields(self) -> dict[str, Any]:
        import json

        return {
            "id": self.id,
            "summary": self.summary,
            "action": self.action,
            "causality": self.causality,
            "time_range": json.dumps(self.time_range, ensure_ascii=False),
            "timestamp": self.timestamp,
            "last_active": self.last_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "participants": json.dumps(self.participants, ensure_ascii=False),
            "payload": json.dumps(self.payload, ensure_ascii=False),
            "evidence": json.dumps(self.evidence, ensure_ascii=False),
            "status": self.status,
            "support_count": int(self.support_count),
            "embedding": self.embedding,
        }

    def __repr__(self) -> str:
        return f"Event(id={self.id[:8]}..., summary={self.summary[:30]}...)"


@dataclass
class EventRelation:
    event_id: str
    entity_id: str
    t_created: int
    t_expired: Optional[int] = None
    t_valid: int = 0
    t_invalid: Optional[int] = None
    c_valid: int = 1
