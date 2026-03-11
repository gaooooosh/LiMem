# -*- coding: utf-8 -*-
"""Entity - 实体模型

Entity 是记忆图中的符号节点，表示人、地点、概念等。
"""

from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class Entity:
    """实体 - 记忆图中的符号节点

    职责：表示对话中涉及的人、地点、概念等实体。
    实体通过INVOLVES关系与Event连接。

    Attributes:
        id: 实体ID（通常是实体名称）
        type: 实体类型（如 person, location, concept, object 等）
        embedding: 向量嵌入（用于语义匹配）
        metadata: 扩展元数据
    """

    id: str
    type: str = "unknown"
    embedding: Optional[list[float]] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entity":
        """从字典创建Entity"""
        return cls(
            id=data.get("id", data.get("name", "")),
            type=data.get("type", "unknown"),
            embedding=data.get("embedding"),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_db_row(cls, row: list[Any], columns: list[str]) -> "Entity":
        """从数据库行创建Entity"""
        data = dict(zip(columns, row))
        embedding = data.get("embedding")
        # Kuzu 返回的 embedding 可能是列表或其他格式
        if embedding and not isinstance(embedding, list):
            embedding = list(embedding) if hasattr(embedding, "__iter__") else None

        return cls(
            id=data.get("id", ""),
            type=data.get("type", "unknown"),
            embedding=embedding,
        )

    def to_db_fields(self) -> dict[str, Any]:
        """转换为数据库存储格式"""
        return {
            "id": self.id,
            "type": self.type,
            "embedding": self.embedding,
        }

    def __repr__(self) -> str:
        return f"Entity(id={self.id}, type={self.type})"

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Entity):
            return self.id == other.id
        if isinstance(other, str):
            return self.id == other
        return False
