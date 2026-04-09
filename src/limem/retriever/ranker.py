# -*- coding: utf-8 -*-
"""MemoryRanker - 记忆排序器

基于时间衰减和实体匹配计算权重，对事件进行排序。
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple
import math
import time

from ..core.event import RankedEvent, EventRelation
from ..config import DECAY_RATE


@dataclass
class RankerConfig:
    """排序器配置

    Attributes:
        decay_rate: 时间衰减率
        decay_rate: 时间衰减率
    """

    decay_rate: float = DECAY_RATE


@dataclass
class WeightDebugInfo:
    """权重计算调试信息"""

    event_id: str
    summary: str
    weight: float
    c_valid: int
    t_valid: int
    t_expired: Optional[int]
    t_invalid: Optional[int]
    entity_match_weights: dict[str, float]
    match_type: str
    t_now: int
    time_diff: int
    base_weight: float
    temporal_factor: float
    entity_factor: float
    evolution_factor: float = 1.0


class MemoryRanker:
    """记忆排序器

    职责：基于时间衰减和实体匹配计算权重。

    权重公式：
    w = log(1 + c_valid) * exp(-decay_rate * time_diff) * entity_factor

    硬过滤条件：
    - t_expired is not None → weight = 0
    - t_invalid is not None and t_now >= t_invalid → weight = 0
    - time_diff < 0 → weight = 0
    """

    def __init__(self, config: Optional[RankerConfig] = None):
        """初始化排序器

        Args:
            config: 排序器配置
        """
        self.config = config or RankerConfig()

    def rank(
        self,
        raw_events: list[dict[str, Any]],
        current_time: Optional[int] = None,
        top_k: Optional[int] = None,
    ) -> Tuple[list[RankedEvent], list[WeightDebugInfo]]:
        """对事件进行排序

        Args:
            raw_events: 原始事件列表（包含entity_match_weights）
            current_time: 当前时间戳（默认使用当前时间）
            top_k: 返回的Top-K数量（None表示返回全部）

        Returns:
            (排序后的RankedEvent列表, 调试信息列表)
        """
        if not raw_events:
            return [], []

        t_now = current_time or int(time.time())

        ranked = []
        debug_list = []

        for event in raw_events:
            weight, debug = self._calculate_weight_with_debug(event, t_now)

            if weight > 0:
                ranked_event = RankedEvent(
                    event_id=event.get("event_id", event.get("id", "")),
                    summary=event.get("summary", ""),
                    weight=weight,
                    c_valid=event.get("c_valid", 0) or 0,
                    t_valid=event.get("t_valid", 0) or 0,
                    t_expired=event.get("t_expired"),
                    t_invalid=event.get("t_invalid"),
                    action=event.get("action", ""),
                    causality=event.get("causality", ""),
                    participants=str(event.get("participants", "")),
                    time_range=str(event.get("time_range", "")),
                    match_type=event.get("match_type", ""),
                    entity_match_weights=event.get("entity_match_weights"),
                )
                ranked.append(ranked_event)
                debug_list.append(debug)

        # 按权重降序排序
        ranked.sort(key=lambda e: e.weight, reverse=True)
        debug_list.sort(key=lambda d: d.weight, reverse=True)

        # 返回Top-K
        if top_k is not None:
            return ranked[:top_k], debug_list[:top_k]

        return ranked, debug_list

    def _calculate_weight_with_debug(
        self, event: dict[str, Any], t_now: int
    ) -> Tuple[float, WeightDebugInfo]:
        """计算权重并返回调试信息

        Args:
            event: 事件字典
            t_now: 当前时间戳

        Returns:
            (权重, 调试信息)
        """
        event_id = event.get("event_id", event.get("id", ""))
        summary = event.get("summary", "")

        # 硬过滤
        t_expired = event.get("t_expired")
        if t_expired is not None:
            debug = WeightDebugInfo(
                event_id=event_id,
                summary=summary,
                weight=0.0,
                c_valid=event.get("c_valid", 0) or 0,
                t_valid=event.get("t_valid", 0) or 0,
                t_expired=t_expired,
                t_invalid=event.get("t_invalid"),
                entity_match_weights=event.get("entity_match_weights", {}),
                match_type=event.get("match_type", ""),
                t_now=t_now,
                time_diff=0,
                base_weight=0.0,
                temporal_factor=0.0,
                entity_factor=0.0,
            )
            return 0.0, debug

        t_invalid = event.get("t_invalid")
        if t_invalid is not None and t_now >= t_invalid:
            debug = WeightDebugInfo(
                event_id=event_id,
                summary=summary,
                weight=0.0,
                c_valid=event.get("c_valid", 0) or 0,
                t_valid=event.get("t_valid", 0) or 0,
                t_expired=t_expired,
                t_invalid=t_invalid,
                entity_match_weights=event.get("entity_match_weights", {}),
                match_type=event.get("match_type", ""),
                t_now=t_now,
                time_diff=0,
                base_weight=0.0,
                temporal_factor=0.0,
                entity_factor=0.0,
            )
            return 0.0, debug

        # 计算时间差
        c_valid = event.get("c_valid", 0) or 0
        t_valid = event.get("t_valid", 0) or 0
        time_diff = t_now - t_valid

        if time_diff < 0:
            debug = WeightDebugInfo(
                event_id=event_id,
                summary=summary,
                weight=0.0,
                c_valid=c_valid,
                t_valid=t_valid,
                t_expired=t_expired,
                t_invalid=t_invalid,
                entity_match_weights=event.get("entity_match_weights", {}),
                match_type=event.get("match_type", ""),
                t_now=t_now,
                time_diff=time_diff,
                base_weight=0.0,
                temporal_factor=0.0,
                entity_factor=0.0,
            )
            return 0.0, debug

        # 计算基础权重
        base_weight = math.log(1 + c_valid)

        # 计算时间衰减
        temporal_factor = math.exp(-self.config.decay_rate * time_diff)

        # 计算实体匹配因子
        entity_match_weights = event.get("entity_match_weights", {})
        entity_factor = self._calculate_entity_factor(entity_match_weights)

        # 演化感知项（context/validity/drift/decay）
        evolution_score = float(event.get("evolution_score", 0.0) or 0.0)
        context_match = float(event.get("context_match", 0.0) or 0.0)
        validity = float(event.get("validity", 1.0) or 1.0)
        support_norm = float(event.get("support_norm", 0.0) or 0.0)
        drift_penalty = float(event.get("drift_penalty", 0.0) or 0.0)
        decay_penalty = float(event.get("decay_penalty", 0.0) or 0.0)
        evolution_factor = max(0.1, 1.0 + 0.3 * evolution_score)

        weight = base_weight * temporal_factor * entity_factor * evolution_factor

        debug = WeightDebugInfo(
            event_id=event_id,
            summary=summary,
            weight=weight,
            c_valid=c_valid,
            t_valid=t_valid,
            t_expired=t_expired,
            t_invalid=t_invalid,
            entity_match_weights=entity_match_weights,
            match_type=event.get("match_type", ""),
            t_now=t_now,
            time_diff=time_diff,
            base_weight=base_weight,
            temporal_factor=temporal_factor,
            entity_factor=entity_factor,
            evolution_factor=evolution_factor,
        )

        return weight, debug

    def calculate_weight(self, event: dict[str, Any], t_now: int) -> float:
        """计算权重（简化版，只返回权重值）

        Args:
            event: 事件字典
            t_now: 当前时间戳

        Returns:
            权重值
        """
        weight, _ = self._calculate_weight_with_debug(event, t_now)
        return weight

    def _calculate_entity_factor(self, entity_match_weights: dict[str, float]) -> float:
        """计算实体匹配因子

        Args:
            entity_match_weights: 实体ID到匹配权重的映射

        Returns:
            实体匹配因子
        """
        if not entity_match_weights:
            return 1.0
        return max(float(weight or 0.0) for weight in entity_match_weights.values())
