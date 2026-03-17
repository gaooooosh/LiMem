# -*- coding: utf-8 -*-
"""GraphStore - 图存储抽象接口

定义图数据库操作的标准契约。
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from ..core.episode import Episode
from ..core.event import Event, EventRelation
from ..core.entity import Entity


class GraphStore(ABC):
    """图存储抽象接口

    职责：定义图数据库操作的标准契约。

    操作类别：
    - Episode 操作：保存、获取、删除
    - Event 操作：保存、获取、更新、查询
    - Entity 操作：确保存在、获取、嵌入
    - Relation 操作：INVOLVES、EXTRACTED_FROM、PERMANENT_TRAIT
    """

    # ==================== Episode 操作 ====================

    @abstractmethod
    def save_episode(self, episode: Episode) -> None:
        """保存Episode

        Args:
            episode: Episode实例
        """
        pass

    @abstractmethod
    def get_episode(self, episode_id: str) -> Optional[Episode]:
        """获取Episode

        Args:
            episode_id: Episode ID

        Returns:
            Episode实例，如果不存在则返回None
        """
        pass

    @abstractmethod
    def delete_expired_episodes(self, current_time: int, ttl: int) -> int:
        """删除过期Episode

        Args:
            current_time: 当前时间戳
            ttl: 生存时间（秒）

        Returns:
            删除的数量
        """
        pass

    # ==================== Event 操作 ====================

    @abstractmethod
    def save_event(self, event: Event) -> None:
        """保存Event

        Args:
            event: Event实例
        """
        pass

    @abstractmethod
    def get_event(self, event_id: str) -> Optional[Event]:
        """获取Event

        Args:
            event_id: Event ID

        Returns:
            Event实例，如果不存在则返回None
        """
        pass

    @abstractmethod
    def update_event(self, event: Event) -> None:
        """更新Event

        Args:
            event: Event实例
        """
        pass

    @abstractmethod
    def get_events_by_entities(self, entities: list[str]) -> list[Event]:
        """根据实体获取关联的事件

        Args:
            entities: 实体ID列表

        Returns:
            Event实例列表
        """
        pass

    @abstractmethod
    def get_all_events_with_entities(self) -> list[dict[str, Any]]:
        """获取所有事件及其关联实体

        用于相似度搜索的候选事件获取。

        Returns:
            事件字典列表，包含 id, summary, embedding, action, last_active, entities
        """
        pass

    # ==================== Entity 操作 ====================

    @abstractmethod
    def ensure_entity(self, entity_name: str, entity_type: str = "UNKNOWN") -> bool:
        """确保实体存在

        如果实体不存在则创建，返回是否为新创建。

        Args:
            entity_name: 实体名称
            entity_type: 实体类型

        Returns:
            是否为新创建
        """
        pass

    @abstractmethod
    def get_all_entities(self) -> list[str]:
        """获取所有实体名称

        Returns:
            实体名称列表
        """
        pass

    @abstractmethod
    def get_entity_embeddings(self, entities: list[str]) -> dict[str, list[float]]:
        """获取实体嵌入向量

        Args:
            entities: 实体名称列表

        Returns:
            实体名称到嵌入向量的映射
        """
        pass

    # ==================== Relation 操作 ====================

    @abstractmethod
    def get_involves_relation(
        self, event_id: str, entity_id: str
    ) -> Optional[EventRelation]:
        """获取INVOLVES关系

        Args:
            event_id: 事件ID
            entity_id: 实体ID

        Returns:
            EventRelation实例，如果不存在则返回None
        """
        pass

    @abstractmethod
    def create_involves_relation(
        self,
        event_id: str,
        entity_id: str,
        t_created: int,
        t_valid: int,
        c_valid: int = 1,
        t_expired: Optional[int] = None,
        t_invalid: Optional[int] = None,
    ) -> None:
        """创建INVOLVES关系

        Args:
            event_id: 事件ID
            entity_id: 实体ID
            t_created: 创建时间
            t_valid: 验证时间
            c_valid: 验证次数
            t_expired: 过期时间
            t_invalid: 失效时间
        """
        pass

    @abstractmethod
    def update_involves_relation(self, relation: EventRelation) -> None:
        """更新INVOLVES关系

        Args:
            relation: EventRelation实例
        """
        pass

    @abstractmethod
    def get_event_entities(self, event_id: str) -> list[str]:
        """获取事件关联的实体

        Args:
            event_id: 事件ID

        Returns:
            实体ID列表
        """
        pass

    @abstractmethod
    def get_event_relations(self, event_id: str) -> list[EventRelation]:
        """获取事件的所有INVOLVES关系

        Args:
            event_id: 事件ID

        Returns:
            EventRelation列表
        """
        pass

    @abstractmethod
    def link_event_to_episode(self, event_id: str, episode_id: str) -> None:
        """创建EXTRACTED_FROM关系

        Args:
            event_id: 事件ID
            episode_id: Episode ID
        """
        pass

    @abstractmethod
    def promote_permanent_trait(
        self, user_id: str, event_id: str, t_created: int
    ) -> None:
        """提升为永久特征

        创建PERMANENT_TRAIT关系。

        Args:
            user_id: 用户ID
            event_id: 事件ID
            t_created: 创建时间
        """
        pass

    # ==================== Dynamic Evolution 扩展 ====================
    # 默认提供可选扩展接口，子类按需实现。

    def save_context(self, context: Any) -> None:
        raise NotImplementedError("save_context is not implemented")

    def get_context(self, context_id: str) -> Optional[Any]:
        raise NotImplementedError("get_context is not implemented")

    def update_context(self, context: Any) -> None:
        raise NotImplementedError("update_context is not implemented")

    def find_context_candidates(
        self,
        context_type: str,
        subtype: str = "",
        limit: int = 20,
        only_active: bool = True,
    ) -> list[Any]:
        raise NotImplementedError("find_context_candidates is not implemented")

    def save_pattern(self, pattern: Any) -> None:
        raise NotImplementedError("save_pattern is not implemented")

    def get_pattern(self, pattern_id: str) -> Optional[Any]:
        raise NotImplementedError("get_pattern is not implemented")

    def update_pattern(self, pattern: Any) -> None:
        raise NotImplementedError("update_pattern is not implemented")

    def find_pattern_candidates(
        self,
        pattern_type: str,
        limit: int = 20,
        only_active: bool = True,
    ) -> list[Any]:
        raise NotImplementedError("find_pattern_candidates is not implemented")

    def link_event_to_context(
        self,
        event_id: str,
        context_id: str,
        confidence: float,
        weight: float,
        original_type: str,
        timestamp: int,
    ) -> None:
        raise NotImplementedError("link_event_to_context is not implemented")

    def link_next(
        self,
        from_event_id: str,
        to_event_id: str,
        confidence: float,
        score: float,
        relation_hint: str,
        timestamp: int,
    ) -> None:
        raise NotImplementedError("link_next is not implemented")

    def link_event_relation(
        self,
        from_event_id: str,
        to_event_id: str,
        relation_type: str,
        confidence: float,
        reason: str,
        source: str,
        timestamp: int,
    ) -> None:
        raise NotImplementedError("link_event_relation is not implemented")

    def link_event_to_pattern(
        self,
        event_id: str,
        pattern_id: str,
        confidence: float,
        contribution_weight: float,
        timestamp: int,
    ) -> None:
        raise NotImplementedError("link_event_to_pattern is not implemented")

    def get_recent_events(
        self,
        current_time: int,
        window_seconds: int,
        limit: int = 100,
    ) -> list[Event]:
        raise NotImplementedError("get_recent_events is not implemented")

    def get_event_contexts(self, event_id: str) -> list[Any]:
        raise NotImplementedError("get_event_contexts is not implemented")

    def get_event_patterns(self, event_id: str) -> list[Any]:
        raise NotImplementedError("get_event_patterns is not implemented")

    def retrieve_candidate_contexts_for_query(
        self,
        query: str,
        query_entities: list[str],
        limit: int = 20,
    ) -> list[Any]:
        raise NotImplementedError("retrieve_candidate_contexts_for_query is not implemented")

    def retrieve_candidate_patterns_for_query(
        self,
        query: str,
        query_entities: list[str],
        limit: int = 20,
    ) -> list[Any]:
        raise NotImplementedError("retrieve_candidate_patterns_for_query is not implemented")

    def retrieve_events_by_contexts(
        self,
        context_ids: list[str],
        limit: int = 50,
    ) -> list[Event]:
        raise NotImplementedError("retrieve_events_by_contexts is not implemented")

    def retrieve_events_by_patterns(
        self,
        pattern_ids: list[str],
        limit: int = 50,
    ) -> list[Event]:
        raise NotImplementedError("retrieve_events_by_patterns is not implemented")

    def save_event_merge_trace(
        self,
        source_event_id: str,
        target_event_id: str,
        merge_reason: str,
        similarity_score: float,
        merged_at: int,
        strategy_version: str,
    ) -> None:
        raise NotImplementedError("save_event_merge_trace is not implemented")

    def list_event_merge_traces(self, event_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError("list_event_merge_traces is not implemented")

    def prune_weak_next_edges(self, min_score: float, stale_before: int) -> int:
        raise NotImplementedError("prune_weak_next_edges is not implemented")

    def prune_weak_event_relation_edges(self, min_confidence: float, stale_before: int) -> int:
        raise NotImplementedError("prune_weak_event_relation_edges is not implemented")

    def archive_event(self, event_id: str, archived_at: int) -> None:
        raise NotImplementedError("archive_event is not implemented")

    def archive_context(self, context_id: str, archived_at: int) -> None:
        raise NotImplementedError("archive_context is not implemented")

    def delete_event(self, event_id: str) -> None:
        raise NotImplementedError("delete_event is not implemented")

    def delete_context(self, context_id: str) -> None:
        raise NotImplementedError("delete_context is not implemented")

    def relink_event_references(
        self,
        source_event_id: str,
        target_event_id: str,
        timestamp: int,
    ) -> dict[str, int]:
        raise NotImplementedError("relink_event_references is not implemented")

    def relink_context_edges(
        self,
        source_context_id: str,
        target_context_id: str,
        timestamp: int,
    ) -> int:
        raise NotImplementedError("relink_context_edges is not implemented")

    def relink_pattern_edges(
        self,
        source_pattern_id: str,
        target_pattern_id: str,
        timestamp: int,
    ) -> int:
        raise NotImplementedError("relink_pattern_edges is not implemented")

    def list_events(
        self,
        limit: int = 50,
        query: str = "",
        statuses: Optional[list[str]] = None,
    ) -> list[Event]:
        raise NotImplementedError("list_events is not implemented")

    def list_contexts(
        self,
        limit: int = 50,
        query: str = "",
        statuses: Optional[list[str]] = None,
    ) -> list[Any]:
        raise NotImplementedError("list_contexts is not implemented")

    def list_patterns(
        self,
        limit: int = 50,
        query: str = "",
        statuses: Optional[list[str]] = None,
    ) -> list[Any]:
        raise NotImplementedError("list_patterns is not implemented")

    def list_event_context_edges(
        self,
        limit: int = 200,
        event_statuses: Optional[list[str]] = None,
        context_statuses: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("list_event_context_edges is not implemented")

    def list_event_pattern_edges(
        self,
        limit: int = 200,
        event_statuses: Optional[list[str]] = None,
        pattern_statuses: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("list_event_pattern_edges is not implemented")

    def list_next_edges(
        self,
        limit: int = 200,
        event_statuses: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("list_next_edges is not implemented")

    def list_event_relation_edges(
        self,
        limit: int = 200,
        event_statuses: Optional[list[str]] = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("list_event_relation_edges is not implemented")

    # ==================== 统计 ====================

    @abstractmethod
    def get_stats(self) -> dict[str, Any]:
        """获取统计信息

        Returns:
            包含事件数、实体数等统计信息的字典
        """
        pass
