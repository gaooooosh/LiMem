# -*- coding: utf-8 -*-
"""Episode - 原始对话片段模型

Episode 是记忆系统的输入单元，表示原始的对话片段。
生命周期：临时存在，TTL后自动清理。
"""

from dataclasses import dataclass, field
from typing import Optional, Any
import uuid


@dataclass
class Episode:
    """原始对话片段 - 记忆系统的输入单元

    职责：封装原始对话数据，提供统一的事件来源抽象。
    生命周期：临时存在，TTL后自动清理。

    Attributes:
        id: 唯一标识符（默认自动生成UUID）
        content: 原始对话文本
        timestamp: Unix时间戳
        metadata: 扩展元数据（如说话人、上下文等）
    """

    content: str
    timestamp: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    metadata: dict[str, Any] = field(default_factory=dict)

    # 可选字段
    speaker: Optional[str] = None  # 说话人
    context: Optional[dict[str, Any]] = None  # 上下文信息（位置、设备等）

    def __post_init__(self):
        """初始化后处理"""
        # 确保 timestamp 是整数
        if isinstance(self.timestamp, str):
            self.timestamp = int(self.timestamp)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Episode":
        """从字典创建Episode

        Args:
            data: 包含Episode数据的字典

        Returns:
            Episode实例
        """
        return cls(
            id=data.get("id", uuid.uuid4().hex),
            content=data.get("content", ""),
            timestamp=data.get("timestamp", 0),
            metadata=data.get("metadata", {}),
            speaker=data.get("speaker"),
            context=data.get("context"),
        )

    def to_db_fields(self) -> dict[str, Any]:
        """转换为数据库存储格式

        Returns:
            适合Kuzu数据库插入的字段字典
        """
        return {
            "id": self.id,
            "content": self.content,
            "timestamp": self.timestamp,
        }

    def is_expired(self, current_time: int, ttl: int) -> bool:
        """检查是否过期

        Args:
            current_time: 当前Unix时间戳
            ttl: 生存时间（秒）

        Returns:
            是否已过期
        """
        return (current_time - self.timestamp) > ttl

    def __repr__(self) -> str:
        return f"Episode(id={self.id[:8]}..., timestamp={self.timestamp}, content={self.content[:50]}...)"
