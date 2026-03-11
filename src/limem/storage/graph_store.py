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

    # ==================== 统计 ====================

    @abstractmethod
    def get_stats(self) -> dict[str, Any]:
        """获取统计信息

        Returns:
            包含事件数、实体数等统计信息的字典
        """
        pass
