# -*- coding: utf-8 -*-
"""Consolidator - 记忆合并器

决定新事件是创建还是合并到现有事件。
算法：多维度相似度计算（语义 + 实体 + 时间 + 动作）。
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple
import math

from ..config import (
    SIMILARITY_THRESHOLD,
    MERGE_WEIGHT_SEMANTIC,
    MERGE_WEIGHT_ENTITY,
    MERGE_WEIGHT_TIME,
    MERGE_WEIGHT_ACTION,
    MERGE_TIME_WINDOW,
)


@dataclass
class ConsolidationResult:
    """合并结果

    Attributes:
        should_merge: 是否应该合并
        target_event_id: 合并目标事件ID（如果合并）
        similarity_score: 相似度分数
        debug_info: 调试信息
    """

    should_merge: bool
    target_event_id: Optional[str] = None
    similarity_score: float = 0.0
    debug_info: dict[str, Any] = field(default_factory=dict)


class Consolidator:
    """记忆合并器

    职责：决定新事件是创建还是合并到现有事件。
    算法：多维度相似度计算。

    四个维度：
    1. 语义相似度（Semantic）: 向量余弦相似度
    2. 实体重叠（Entity）: Jaccard 相似度
    3. 时间邻近（Time）: 指数衰减
    4. 动作匹配（Action）: 精确匹配
    """

    def __init__(
        self,
        store,  # GraphStore
        similarity_threshold: float = SIMILARITY_THRESHOLD,
        weights: Optional[dict[str, float]] = None,
        time_window: int = MERGE_TIME_WINDOW,
    ):
        """初始化合并器

        Args:
            store: 图存储接口
            similarity_threshold: 合并相似度阈值
            weights: 各维度权重（semantic, entity, time, action）
            time_window: 时间窗口（秒）
        """
        self.store = store
        self.similarity_threshold = similarity_threshold
        self.time_window = time_window

        # 默认权重配置
        self.weights = weights or {
            "semantic": MERGE_WEIGHT_SEMANTIC,
            "entity": MERGE_WEIGHT_ENTITY,
            "time": MERGE_WEIGHT_TIME,
            "action": MERGE_WEIGHT_ACTION,
        }

        # 验证权重总和为 1.0
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Weights must sum to 1.0, got {total}")

    def find_similar_event(
        self,
        embedding: list[float],
        entities: list[str],
        action: str,
        current_time: int,
    ) -> ConsolidationResult:
        """查找最相似的现有事件

        Args:
            embedding: 新事件的嵌入向量
            entities: 新事件涉及的实体列表
            action: 新事件的动作类型
            current_time: 当前时间戳

        Returns:
            ConsolidationResult 包含合并决策和调试信息
        """
        # 获取所有候选事件
        candidates = self._get_candidate_events()

        if not candidates:
            return ConsolidationResult(should_merge=False)

        best_id = None
        best_summary = None
        best_score = 0.0
        best_debug = {}

        # 标准化实体名称
        entity_set = set(self._normalize_entity_name(e) for e in entities)

        for candidate in candidates:
            # 计算四维相似度
            scores = self._calculate_multi_dim_similarity(
                embedding=embedding,
                entity_set=entity_set,
                action=action,
                current_time=current_time,
                candidate=candidate,
            )

            # 加权组合
            combined_score = (
                self.weights["semantic"] * scores["semantic"] +
                self.weights["entity"] * scores["entity"] +
                self.weights["time"] * scores["time"] +
                self.weights["action"] * scores["action"]
            )

            if combined_score > best_score:
                best_score = combined_score
                best_id = candidate["id"]
                best_summary = candidate["summary"]
                best_debug = scores

        # 决策
        should_merge = best_score > self.similarity_threshold

        return ConsolidationResult(
            should_merge=should_merge,
            target_event_id=best_id if should_merge else None,
            similarity_score=best_score,
            debug_info={
                **best_debug,
                "combined": best_score,
                "threshold": self.similarity_threshold,
                "best_summary": best_summary,
            },
        )

    def _get_candidate_events(self) -> list[dict[str, Any]]:
        """获取候选事件列表

        Returns:
            候选事件字典列表
        """
        return self.store.get_all_events_with_entities()

    def _calculate_multi_dim_similarity(
        self,
        embedding: list[float],
        entity_set: set[str],
        action: str,
        current_time: int,
        candidate: dict[str, Any],
    ) -> dict[str, float]:
        """计算多维度相似度

        Args:
            embedding: 新事件嵌入
            entity_set: 新事件实体集合
            action: 新事件动作
            current_time: 当前时间
            candidate: 候选事件

        Returns:
            各维度相似度字典
        """
        # 1. 语义相似度（余弦）
        semantic_sim = self._cosine_similarity(
            embedding,
            candidate.get("embedding"),
        )

        # 2. 实体重叠（Jaccard）
        entity_sim = 0.0
        stored_entities = candidate.get("entities", [])
        if entity_set and stored_entities:
            stored_set = set(stored_entities)
            if entity_set or stored_set:
                intersection = len(entity_set & stored_set)
                union = len(entity_set | stored_set)
                entity_sim = intersection / union if union > 0 else 0.0

        # 3. 时间邻近（指数衰减）
        time_sim = 0.0
        last_active = candidate.get("last_active")
        if current_time is not None and last_active is not None:
            time_diff = abs(current_time - last_active)
            if time_diff <= self.time_window:
                # 指数衰减: t=0 时为 1.0, t=time_window 时约为 0.05
                time_sim = math.exp(-3.0 * time_diff / self.time_window)
            else:
                time_sim = 0.0
        elif current_time is None:
            time_sim = 0.5  # 无时间信息时不惩罚

        # 4. 动作匹配（精确）
        action_sim = 0.0
        stored_action = candidate.get("action", "")
        if action and stored_action:
            action_sim = 1.0 if action == stored_action else 0.0

        return {
            "semantic": semantic_sim,
            "entity": entity_sim,
            "time": time_sim,
            "action": action_sim,
        }

    def _cosine_similarity(
        self,
        vec_a: Optional[list[float]],
        vec_b: Optional[list[float]],
    ) -> float:
        """计算余弦相似度

        Args:
            vec_a: 向量A
            vec_b: 向量B

        Returns:
            余弦相似度（-1 到 1）
        """
        if not vec_a or not vec_b:
            return -1.0

        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0

        for a, b in zip(vec_a, vec_b):
            dot += a * b
            norm_a += a * a
            norm_b += b * b

        if norm_a == 0.0 or norm_b == 0.0:
            return -1.0

        return dot / math.sqrt(norm_a * norm_b)

    def _normalize_entity_name(self, entity: Any) -> str:
        """标准化实体名称

        Args:
            entity: 实体（字符串或字典）

        Returns:
            标准化后的实体名称
        """
        if isinstance(entity, dict):
            return entity.get("name", str(entity))
        return str(entity)
