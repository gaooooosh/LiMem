# -*- coding: utf-8 -*-
"""LTMemory - 长时记忆系统抽象接口

定义记忆系统的核心操作契约，是整个系统的顶层抽象。
"""

from abc import ABC, abstractmethod
from typing import Any, Optional
from dataclasses import dataclass, field

from .episode import Episode
from .event import Event, RankedEvent


@dataclass
class SearchResult:
    """搜索结果

    Attributes:
        query: 原始查询
        entities: 提取的实体列表
        ranked_events: 所有排序后的事件
        top_k_events: Top-K事件
        answer: LLM生成的回答（可选）
    """

    query: str
    entities: list[str] = field(default_factory=list)
    ranked_events: list[RankedEvent] = field(default_factory=list)
    top_k_events: list[RankedEvent] = field(default_factory=list)
    answer: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        return {
            "query": self.query,
            "entities": self.entities,
            "ranked_events": [e.to_dict() for e in self.ranked_events],
            "top_k_events": [e.to_dict() for e in self.top_k_events],
            "answer": self.answer,
        }


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
    - search: 搜索记忆，返回相关事件
    - get_event: 获取单个事件
    - cleanup: 清理过期数据
    """

    @abstractmethod
    def ingest(self, episode: Episode) -> IngestResult:
        """摄入一个Episode，返回提取的Event

        这是记忆构建的核心入口，执行以下流程：
        1. LLM提取事件和实体
        2. 相似度搜索
        3. 合并或创建事件
        4. 更新实体关系

        Args:
            episode: 原始对话片段

        Returns:
            IngestResult 包含事件和构建信息
        """
        pass

    @abstractmethod
    def search(
        self,
        query: str,
        top_k: int = 5,
        generate_answer: bool = True,
    ) -> SearchResult:
        """搜索记忆

        执行四阶段检索管道：
        1. 实体提取
        2. 图路径搜索
        3. 加权重排序
        4. LLM总结（可选）

        Args:
            query: 用户查询
            top_k: 返回的事件数量
            generate_answer: 是否生成LLM回答

        Returns:
            SearchResult 包含排序事件和可选回答
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
    def decay_weights(self, current_time: int) -> dict[str, float]:
        """计算所有事件的衰减权重

        用于观察和调试记忆衰减状态。

        Args:
            current_time: 当前时间戳

        Returns:
            事件ID到权重的映射
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
