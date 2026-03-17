# -*- coding: utf-8 -*-
"""LTMemoryImpl - 长时记忆系统实现

整合 Builder + Searcher + Store 的完整实现。
"""

from typing import Any, Optional
import time

from .core.episode import Episode
from .core.event import Event, RankedEvent, EventRelation
from .core.memory import LTMemory, SearchResult, IngestResult
from .builder.memory_builder import MemoryBuilder
from .retriever.memory_searcher import MemorySearcher
from .storage.graph_store import GraphStore
from .config import EPISODE_TTL, DECAY_RATE


class LTMemoryImpl(LTMemory):
    """长时记忆系统实现

    职责：整合 Builder + Searcher + Store，提供统一的记忆系统接口。

    核心功能：
    - ingest: 记忆构建（Episode → Event）
    - search: 记忆检索（Query → Events）
    - cleanup: 过期数据清理
    """

    def __init__(
        self,
        store: GraphStore,
        builder: MemoryBuilder,
        searcher: MemorySearcher,
        episode_ttl: int = EPISODE_TTL,
        decay_rate: float = DECAY_RATE,
        dynamic_engine=None,
    ):
        """初始化长时记忆系统

        Args:
            store: 图存储接口
            builder: 记忆构建器
            searcher: 记忆搜索器
            episode_ttl: Episode生存时间（秒）
            decay_rate: 权重衰减率
        """
        self.store = store
        self.builder = builder
        self.searcher = searcher
        self.episode_ttl = episode_ttl
        self.decay_rate = decay_rate
        self.dynamic_engine = dynamic_engine

    def ingest(self, episode: Episode) -> IngestResult:
        """摄入Episode

        这是记忆构建的核心入口。

        Args:
            episode: 原始对话片段

        Returns:
            IngestResult 包含事件和构建信息
        """
        return self.builder.build(episode)

    def search(
        self,
        query: str,
        top_k: int = 5,
        generate_answer: bool = True,
    ) -> SearchResult:
        """搜索记忆

        执行四阶段检索管道。

        Args:
            query: 用户查询
            top_k: 返回的事件数量
            generate_answer: 是否生成LLM回答

        Returns:
            SearchResult 包含排序事件和可选回答
        """
        return self.searcher.search(query, top_k, generate_answer)

    def get_event(self, event_id: str) -> Optional[Event]:
        """获取单个事件

        Args:
            event_id: 事件ID

        Returns:
            Event实例，如果不存在则返回None
        """
        return self.store.get_event(event_id)

    def get_related_entities(self, event_id: str) -> list[str]:
        """获取事件关联的实体

        Args:
            event_id: 事件ID

        Returns:
            实体ID列表
        """
        return self.store.get_event_entities(event_id)

    def decay_weights(self, current_time: int) -> dict[str, float]:
        """计算所有事件的衰减权重

        用于观察和调试记忆衰减状态。

        Args:
            current_time: 当前时间戳

        Returns:
            事件ID到权重的映射
        """
        weights = {}

        # 获取所有事件及其关系
        all_events = self.store.get_all_events_with_entities()

        for event_data in all_events:
            event_id = event_data["id"]
            relations = self.store.get_event_relations(event_id)

            for relation in relations:
                w = relation.calculate_weight(current_time, self.decay_rate)
                key = f"{event_id}:{relation.entity_id}"
                weights[key] = w

        return weights

    def cleanup(self, current_time: int) -> int:
        """清理过期的临时数据

        Args:
            current_time: 当前时间戳

        Returns:
            清理的数据数量
        """
        count = self.store.delete_expired_episodes(current_time, self.episode_ttl)
        if count > 0:
            print(f"🗑️ Cleaned up {count} old episodes")
        return count

    def get_stats(self) -> dict[str, Any]:
        """获取系统统计信息

        Returns:
            包含事件数、实体数等统计信息的字典
        """
        return self.store.get_stats()

    def retrieve_memories(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Evolution-aware retrieval output for edge-side small models."""
        if not self.dynamic_engine:
            search_result = self.search(query=query, top_k=top_k, generate_answer=False)
            return [
                {
                    "event_id": item.event_id,
                    "summary": item.summary,
                    "weight": item.weight,
                }
                for item in search_result.top_k_events
            ]
        return self.dynamic_engine.retrieve_memories(query=query, top_k=top_k)

    def run_consolidation(self, dry_run: bool = False) -> dict[str, int]:
        if not self.dynamic_engine:
            return {}
        return self.dynamic_engine.run_consolidation(dry_run=dry_run)

    # ==================== 便捷方法 ====================

    def ingest_text(self, text: str, timestamp: Optional[int] = None) -> IngestResult:
        """从文本创建并摄入Episode

        便捷方法，自动创建Episode对象。

        Args:
            text: 对话文本
            timestamp: 时间戳（默认使用当前时间）

        Returns:
            IngestResult
        """
        ts = timestamp or int(time.time())
        episode = Episode(content=text, timestamp=ts)
        return self.ingest(episode)

    def search_debug(self, query: str, top_k: int = 5) -> dict[str, Any]:
        """调试搜索

        返回详细的调试信息。

        Args:
            query: 用户查询
            top_k: 返回的事件数量

        Returns:
            包含调试信息的字典
        """
        return self.searcher.search_debug(query, top_k)

    def peek_decayed_weights(self, event_id: str, current_time: int) -> None:
        """观察单个事件的衰减权重（调试用）

        Args:
            event_id: 事件ID
            current_time: 当前时间戳
        """
        event = self.store.get_event(event_id)
        if not event:
            print(f"Event not found: {event_id}")
            return

        relations = self.store.get_event_relations(event_id)

        for relation in relations:
            w = relation.calculate_weight(current_time, self.decay_rate)
            print(
                f"📉 Decayed weight @t={current_time} | {event.summary} -> {relation.entity_id} | "
                f"decayed={w:.4f}"
            )
