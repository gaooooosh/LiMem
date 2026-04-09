# -*- coding: utf-8 -*-
"""LTMemory - 长时记忆系统抽象接口

定义记忆系统的核心操作契约，是整个系统的顶层抽象。
"""

from abc import ABC, abstractmethod
from typing import Any, Optional
from dataclasses import dataclass, field

from .episode import Episode
from .event import Event


@dataclass
class IngestResult:
    """摄入结果

    Attributes:
        event: 创建或合并后的事件
        is_new: 是否是新创建的事件
        merged_with: 如果是合并，目标事件ID
        entities_created: 新创建的实体数量
    """

    event: Event
    is_new: bool
    merged_with: Optional[str] = None
    entities_created: int = 0
    events: list[Event] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        all_events = self.events or [self.event]
        return {
            "event_id": self.event.id,
            "is_new": self.is_new,
            "merged_with": self.merged_with,
            "entities_created": self.entities_created,
            "event_ids": [event.id for event in all_events],
            "event_count": len(all_events),
            "metrics": dict(self.metrics or {}),
        }


class LTMemory(ABC):
    """长时记忆系统 - 顶层抽象接口

    职责：定义记忆系统的核心操作契约。

    核心操作：
    - ingest: 摄入Episode，提取并存储Event
    - get_event: 获取单个事件
    - cleanup: 清理过期数据
    """

    @abstractmethod
    def ingest(self, episode: Episode) -> IngestResult:
        """摄入一个Episode，返回提取的Event

        这是记忆构建的核心入口，执行以下流程：
        1. LLM提取事件和实体
        2. 规范化并持久化事件
        3. 更新实体关系和相关图结构

        Args:
            episode: 原始对话片段

        Returns:
            IngestResult 包含事件和构建信息
        """
        pass

    @abstractmethod
    def get_event(self, event_id: str) -> Optional[Event]:
        """获取单个事件

        Args:
            event_id: 事件ID

        Returns:
            Event实例，如果不存在则返回None
        """
        pass

    @abstractmethod
    def get_related_entities(self, event_id: str) -> list[str]:
        """获取事件关联的实体

        Args:
            event_id: 事件ID

        Returns:
            实体ID列表
        """
        pass

    @abstractmethod
    def cleanup(self, current_time: int) -> int:
        """清理过期的临时数据

        Args:
            current_time: 当前时间戳

        Returns:
            清理的数据数量
        """
        pass

    @abstractmethod
    def get_stats(self) -> dict[str, Any]:
        """获取系统统计信息

        Returns:
            包含事件数、实体数等统计信息的字典
        """
        pass
