# -*- coding: utf-8 -*-
"""Event - 结构化事件模型

Event 是从 Episode 提取的语义记忆单元，表示经过LLM处理的、结构化的语义事件。
特点：可合并、可衰减、可验证。
"""

from dataclasses import dataclass, field
from typing import Optional, Any
from enum import Enum
import math

from ..utils import hash_summary


class Consistency(str, Enum):
    """事件一致性状态"""

    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    UNCERTAIN = "uncertain"


@dataclass
class Event:
    """结构化事件 - 从Episode提取的语义记忆单元

    职责：表示经过LLM处理的、结构化的语义事件。
    特点：
    - 可合并（相似事件可融合）
    - 可衰减（时间权重递减）
    - 可验证（c_valid计数）

    Attributes:
        id: 事件ID（summary的SHA256哈希）
        summary: 事件摘要
        action: 动作类型
        causality: 因果关系
        time_range: 时间范围信息
        last_active: 最后激活时间（Unix时间戳）
        participants: 参与者列表
        location: 位置信息
        evidence: 证据列表
        consistency: 一致性状态
        embedding: 向量嵌入（用于语义相似度计算）
    """

    # 核心语义字段
    summary: str
    id: str = ""  # 默认为空，__post_init__ 中根据 summary 生成
    action: str = ""
    causality: str = ""

    # 时间信息
    time_range: dict[str, Any] = field(default_factory=dict)
    last_active: int = 0

    # 参与者与位置
    participants: list[dict[str, Any]] = field(default_factory=list)
    location: dict[str, Any] = field(default_factory=dict)

    # 证据与一致性
    evidence: list[dict[str, Any]] = field(default_factory=list)
    consistency: Consistency = Consistency.UNCERTAIN

    # 向量嵌入
    embedding: Optional[list[float]] = None

    def __post_init__(self):
        """初始化后处理：生成事件ID"""
        if not self.id and self.summary:
            self.id = hash_summary(self.summary)

        # 确保 consistency 是枚举类型
        if isinstance(self.consistency, str):
            self.consistency = Consistency(self.consistency)

    @classmethod
    def from_extraction(cls, data: dict[str, Any], current_time: int) -> "Event":
        """从LLM提取结果创建Event

        Args:
            data: LLM提取的事件数据
            current_time: 当前时间戳

        Returns:
            Event实例
        """
        summary = data.get("summary", "")
        return cls(
            id=hash_summary(summary) if summary else "",
            summary=summary,
            action=data.get("action", ""),
            causality=data.get("causality", ""),
            time_range=data.get("time_range", {}),
            last_active=current_time,
            participants=data.get("participants", []),
            location=data.get("location", {}),
            evidence=data.get("evidence", []),
            consistency=Consistency(data.get("consistency", "uncertain")),
        )

    @classmethod
    def from_db_row(cls, row: list[Any], columns: list[str]) -> "Event":
        """从数据库行创建Event

        Args:
            row: 数据库返回的行数据
            columns: 列名列表

        Returns:
            Event实例
        """
        import json
        from ..utils import safe_json_loads

        data = dict(zip(columns, row))
        return cls(
            id=data.get("id", ""),
            summary=data.get("summary", ""),
            action=data.get("action", ""),
            causality=data.get("causality", ""),
            time_range=safe_json_loads(data.get("time_range"), {}),
            last_active=data.get("last_active", 0),
            participants=safe_json_loads(data.get("participants"), []),
            location=safe_json_loads(data.get("location"), {}),
            evidence=safe_json_loads(data.get("evidence"), []),
            consistency=Consistency(data.get("consistency", "uncertain")),
            embedding=list(data.get("embedding", [])) if data.get("embedding") else None,
        )

    def merge_with(self, other: "Event", current_time: int) -> "Event":
        """合并两个相似事件

        Args:
            other: 要合并的另一个事件
            current_time: 当前时间戳

        Returns:
            合并后的新Event实例
        """
        # 合并证据（去重）
        merged_evidence = self.evidence + other.evidence

        # 合并参与者（去重）
        merged_participants = self.participants + other.participants

        return Event(
            id=self.id,
            summary=self.summary,  # 保留原始摘要
            action=self.action,
            causality=self.causality,
            time_range=self.time_range,
            last_active=current_time,  # 刷新激活时间
            participants=merged_participants,
            location=self.location,
            evidence=merged_evidence,
            consistency=self.consistency,
            embedding=self.embedding,  # 保留原嵌入
        )

    def to_db_fields(self) -> dict[str, Any]:
        """转换为数据库存储格式

        Returns:
            适合Kuzu数据库插入的字段字典
        """
        import json

        return {
            "id": self.id,
            "summary": self.summary,
            "action": self.action,
            "causality": self.causality,
            "time_range": json.dumps(self.time_range, ensure_ascii=False),
            "last_active": self.last_active,
            "participants": json.dumps(self.participants, ensure_ascii=False),
            "location": json.dumps(self.location, ensure_ascii=False),
            "evidence": json.dumps(self.evidence, ensure_ascii=False),
            "consistency": self.consistency.value,
            "embedding": self.embedding,
        }

    def __repr__(self) -> str:
        return f"Event(id={self.id[:8]}..., summary={self.summary[:30]}...)"


@dataclass
class EventRelation:
    """Event与Entity的关系（INVOLVES）

    Attributes:
        event_id: 事件ID
        entity_id: 实体ID
        t_created: 创建时间
        t_valid: 最后验证时间
        c_valid: 验证次数（参与度）
        t_expired: 过期时间（可选）
        t_invalid: 失效时间（可选）
    """

    event_id: str
    entity_id: str
    t_created: int
    t_valid: int
    c_valid: int = 1
    t_expired: Optional[int] = None
    t_invalid: Optional[int] = None

    def calculate_weight(self, current_time: int, decay_rate: float) -> float:
        """计算衰减权重

        公式: weight = log(1 + c_valid) * exp(-decay_rate * time_diff)

        Args:
            current_time: 当前时间戳
            decay_rate: 衰减率

        Returns:
            计算得到的权重值
        """
        # 硬过滤：已过期
        if self.t_expired is not None:
            return 0.0

        # 硬过滤：已失效
        if self.t_invalid is not None and current_time >= self.t_invalid:
            return 0.0

        time_diff = current_time - self.t_valid

        # 硬过滤：未来事件
        if time_diff < 0:
            return 0.0

        # 权重公式
        return math.log(1 + self.c_valid) * math.exp(-decay_rate * time_diff)

    @classmethod
    def from_db_row(cls, row: list[Any], columns: list[str]) -> "EventRelation":
        """从数据库行创建EventRelation"""
        data = dict(zip(columns, row))
        return cls(
            event_id=data.get("event_id", ""),
            entity_id=data.get("entity_id", ""),
            t_created=data.get("t_created", 0),
            t_valid=data.get("t_valid", 0),
            c_valid=data.get("c_valid", 1),
            t_expired=data.get("t_expired"),
            t_invalid=data.get("t_invalid"),
        )

    def to_db_fields(self) -> dict[str, Any]:
        """转换为数据库存储格式"""
        return {
            "event_id": self.event_id,
            "entity_id": self.entity_id,
            "t_created": self.t_created,
            "t_valid": self.t_valid,
            "c_valid": self.c_valid,
            "t_expired": self.t_expired,
            "t_invalid": self.t_invalid,
        }


@dataclass
class RankedEvent:
    """排序后的事件 - 用于检索结果

    继承Event的基本信息，添加检索相关的权重和匹配信息。
    """

    event_id: str
    summary: str
    weight: float
    c_valid: int
    t_valid: int
    t_expired: Optional[int] = None
    t_invalid: Optional[int] = None
    action: str = ""
    causality: str = ""
    participants: str = ""
    location: str = ""
    time_range: str = ""
    match_type: str = ""  # "exact", "containment", "fuzzy", "exact+fuzzy"
    entity_match_weights: Optional[dict[str, float]] = None

    def __lt__(self, other: "RankedEvent") -> bool:
        """支持按权重降序排序"""
        return self.weight > other.weight

    @classmethod
    def from_event(
        cls,
        event: Event,
        relation: EventRelation,
        weight: float,
        match_type: str = "",
        entity_match_weights: Optional[dict[str, float]] = None,
    ) -> "RankedEvent":
        """从Event和EventRelation创建RankedEvent"""
        import json

        return cls(
            event_id=event.id,
            summary=event.summary,
            weight=weight,
            c_valid=relation.c_valid,
            t_valid=relation.t_valid,
            t_expired=relation.t_expired,
            t_invalid=relation.t_invalid,
            action=event.action,
            causality=event.causality,
            participants=json.dumps(event.participants, ensure_ascii=False),
            location=json.dumps(event.location, ensure_ascii=False),
            time_range=json.dumps(event.time_range, ensure_ascii=False),
            match_type=match_type,
            entity_match_weights=entity_match_weights,
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式（用于API响应）"""
        from dataclasses import asdict

        return asdict(self)
