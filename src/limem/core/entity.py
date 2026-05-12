# -*- coding: utf-8 -*-
"""Entity - 实体模型

Entity 是记忆图中的符号节点，表示人、地点、概念等。

注册实体（registered=True）由调用方显式登记，
携带自然语言 description 与基于该描述的向量，
用于后续抽取阶段的链接 / 合并决策。
"""

from dataclasses import dataclass, field
from typing import Optional, Any

from ..utils import safe_json_dumps, safe_json_loads


@dataclass
class Entity:
    """实体 - 记忆图中的符号节点

    职责：表示对话中涉及的人、地点、概念等实体。
    实体通过INVOLVES关系与Event连接。

    Attributes:
        id: 实体ID（通常是实体名称）
        type: 实体类型（如 person, location, concept, object 等）
        embedding: 基于裸名的向量嵌入（用于语义匹配，保留既有行为）
        description: 注册实体的自然语言描述
        description_embedding: description + id + type 的语义嵌入
        aliases: 已知别名列表（注册实体上累积抽取阶段命中的 surface form）
        registered: 是否为注册（重要）实体
        status: active / merged
        canonical_id: 若本节点已被合并到注册实体，指向 canonical id
        merged_from: 注册节点上累积被吸收的旧节点 id
        created_at / updated_at: 时间戳
        metadata: 扩展元数据
    """

    id: str
    type: str = "unknown"
    embedding: Optional[list[float]] = None
    description: str = ""
    description_embedding: Optional[list[float]] = None
    aliases: list[str] = field(default_factory=list)
    registered: bool = False
    status: str = "active"
    canonical_id: Optional[str] = None
    merged_from: list[str] = field(default_factory=list)
    created_at: int = 0
    updated_at: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Entity":
        """从字典创建Entity"""
        return cls(
            id=data.get("id", data.get("name", "")),
            type=data.get("type", "unknown"),
            embedding=data.get("embedding"),
            description=data.get("description", "") or "",
            description_embedding=data.get("description_embedding"),
            aliases=list(data.get("aliases", []) or []),
            registered=bool(data.get("registered", False)),
            status=data.get("status", "active") or "active",
            canonical_id=data.get("canonical_id"),
            merged_from=list(data.get("merged_from", []) or []),
            created_at=int(data.get("created_at", 0) or 0),
            updated_at=int(data.get("updated_at", 0) or 0),
            metadata=data.get("metadata", {}) or {},
        )

    @classmethod
    def from_db_row(cls, row: list[Any], columns: list[str]) -> "Entity":
        """从数据库行创建Entity

        columns 列出 SELECT 返回字段的顺序，本方法按 columns 名称取值。
        aliases / merged_from 在库中以 JSON 字符串存储，此处反序列化为 list。
        """
        data = dict(zip(columns, row))

        embedding = data.get("embedding")
        if embedding and not isinstance(embedding, list):
            embedding = list(embedding) if hasattr(embedding, "__iter__") else None

        description_embedding = data.get("description_embedding")
        if description_embedding and not isinstance(description_embedding, list):
            description_embedding = (
                list(description_embedding)
                if hasattr(description_embedding, "__iter__")
                else None
            )

        aliases = data.get("aliases")
        if isinstance(aliases, str):
            aliases = safe_json_loads(aliases, [])
        aliases = list(aliases or [])

        merged_from = data.get("merged_from")
        if isinstance(merged_from, str):
            merged_from = safe_json_loads(merged_from, [])
        merged_from = list(merged_from or [])

        metadata = data.get("metadata")
        if isinstance(metadata, str):
            metadata = safe_json_loads(metadata, {})
        metadata = metadata or {}

        return cls(
            id=data.get("id", ""),
            type=data.get("type", "unknown") or "unknown",
            embedding=embedding,
            description=data.get("description", "") or "",
            description_embedding=description_embedding,
            aliases=aliases,
            registered=bool(data.get("registered", False)),
            status=data.get("status", "active") or "active",
            canonical_id=data.get("canonical_id"),
            merged_from=merged_from,
            created_at=int(data.get("created_at", 0) or 0),
            updated_at=int(data.get("updated_at", 0) or 0),
            metadata=metadata,
        )

    def to_db_fields(self) -> dict[str, Any]:
        """转换为数据库存储格式（aliases / merged_from 序列化为 JSON 串）"""
        return {
            "id": self.id,
            "type": self.type,
            "embedding": self.embedding,
            "description": self.description or "",
            "description_embedding": self.description_embedding,
            "aliases": safe_json_dumps(self.aliases or []),
            "registered": bool(self.registered),
            "status": self.status or "active",
            "canonical_id": self.canonical_id,
            "merged_from": safe_json_dumps(self.merged_from or []),
            "created_at": int(self.created_at or 0),
            "updated_at": int(self.updated_at or 0),
            "metadata": safe_json_dumps(self.metadata or {}),
        }

    def to_serializable(self) -> dict[str, Any]:
        """对外可序列化的字段（不含 embedding 大数组）"""
        return {
            "id": self.id,
            "type": self.type,
            "description": self.description,
            "aliases": list(self.aliases or []),
            "registered": bool(self.registered),
            "status": self.status,
            "canonical_id": self.canonical_id,
            "merged_from": list(self.merged_from or []),
            "created_at": int(self.created_at or 0),
            "updated_at": int(self.updated_at or 0),
            "metadata": dict(self.metadata or {}),
        }

    def __repr__(self) -> str:
        flag = " *" if self.registered else ""
        return f"Entity(id={self.id}, type={self.type}{flag})"

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Entity):
            return self.id == other.id
        if isinstance(other, str):
            return self.id == other
        return False
