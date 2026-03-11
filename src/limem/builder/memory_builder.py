# -*- coding: utf-8 -*-
"""MemoryBuilder - 记忆构建器

编排整个构建管道：
1. LLM提取（事件 + 实体）
2. 相似度搜索
3. 合并/创建决策
4. 存储（Event + INVOLVES关系）
5. 修剪/提升
"""

from dataclasses import dataclass, field
from typing import Any, Optional
import uuid

from dashscope import TextEmbedding

from ..core.episode import Episode
from ..core.event import Event, EventRelation, Consistency
from ..core.memory import IngestResult
from ..config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    EMBEDDING_MODEL,
    DEFAULT_USER_ID,
    PRUNE_C_VALID_THRESHOLD,
    PRUNE_EVIDENCE_TOP_K,
)
from ..utils import (
    hash_summary,
    safe_json_dumps,
    safe_json_loads,
    time_bucket_from_ts,
)
from .extractor import LLMExtractor, ExtractionResult
from .consolidator import Consolidator, ConsolidationResult


@dataclass
class BuilderConfig:
    """构建器配置

    Attributes:
        prune_threshold: 修剪阈值（c_valid）
        prune_top_k: 证据修剪数量
        default_user_id: 默认用户ID
    """

    prune_threshold: int = PRUNE_C_VALID_THRESHOLD
    prune_top_k: int = PRUNE_EVIDENCE_TOP_K
    default_user_id: str = DEFAULT_USER_ID


class MemoryBuilder:
    """记忆构建器 - 编排完整的构建管道

    职责：协调提取、合并、存储的完整流程。

    管道流程：
    1. 保存原始Episode
    2. LLM提取（事件 + 实体）
    3. 生成嵌入向量
    4. 相似度搜索
    5. 合并/创建决策
    6. 存储Event
    7. 创建Event → Episode链接
    8. 更新INVOLVES关系
    9. 修剪/提升（可选）
    """

    def __init__(
        self,
        extractor: LLMExtractor,
        consolidator: Consolidator,
        store,  # GraphStore
        config: Optional[BuilderConfig] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ):
        """初始化记忆构建器

        Args:
            extractor: LLM提取器
            consolidator: 记忆合并器
            store: 图存储接口
            config: 构建器配置
            api_key: DashScope API Key
            base_url: DashScope API URL
            embedding_model: 嵌入模型名称
        """
        self.extractor = extractor
        self.consolidator = consolidator
        self.store = store
        self.config = config or BuilderConfig()

        # 配置嵌入服务
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.embedding_model = embedding_model or EMBEDDING_MODEL

    def build(self, episode: Episode) -> IngestResult:
        """从Episode构建记忆

        这是最核心的方法，编排整个构建流程。

        Args:
            episode: 原始对话片段

        Returns:
            IngestResult 包含事件和构建信息
        """
        current_time = episode.timestamp

        # Step 1: 保存原始Episode
        self.store.save_episode(episode)
        print(f"📝 Saved Episode: {episode.id} at t={current_time}")

        # Step 2: LLM提取
        extraction = self.extractor.extract(episode.content)

        # 构建事件帧
        event = self._build_event_frame(extraction, episode.content, current_time)
        print(f"🧠 Extracted Event: {event.summary}")

        # 获取实体
        entities = extraction.entities
        print(f"🧩 Entities: {entities}")

        # Step 3: 生成嵌入向量
        embedding = self._get_embedding(event.summary)

        # Step 4: 相似度搜索
        consolidation = self.consolidator.find_similar_event(
            embedding=embedding,
            entities=entities,
            action=event.action,
            current_time=current_time,
        )

        if consolidation.similarity_score > 0:
            print(f"🔎 Top match (combined={consolidation.similarity_score:.4f})")
            if consolidation.debug_info:
                debug = consolidation.debug_info
                print(
                    f"   └─ semantic={debug.get('semantic', 0):.3f}, "
                    f"entity={debug.get('entity', 0):.3f}, "
                    f"time={debug.get('time', 0):.3f}, "
                    f"action={debug.get('action', 0):.3f}"
                )

        # Step 5: 合并或创建
        if consolidation.should_merge:
            # 合并到现有事件
            event = self._merge_event(
                event_id=consolidation.target_event_id,
                incoming_event=event,
                embedding=embedding,
                current_time=current_time,
            )
            is_new = False
            print(f"🔍 Merged into existing memory: {consolidation.target_event_id[:8]}...")
        else:
            # 创建新事件
            event.id = hash_summary(event.summary)
            event.embedding = embedding
            self.store.save_event(event)
            is_new = True
            print(f"🆕 Created new memory: {event.id[:8]}...")

        # Step 6: 创建Event → Episode链接
        self.store.link_event_to_episode(event.id, episode.id)

        # Step 7: 更新INVOLVES关系
        entities_created = self._update_entity_relations(
            event_id=event.id,
            entities=entities,
            current_time=current_time,
        )

        return IngestResult(
            event=event,
            is_new=is_new,
            merged_with=consolidation.target_event_id if not is_new else None,
            entities_created=entities_created,
        )

    def _build_event_frame(
        self,
        extraction: ExtractionResult,
        episode_content: str,
        current_time: int,
    ) -> Event:
        """构建事件帧

        Args:
            extraction: 提取结果
            episode_content: 原始Episode内容
            current_time: 当前时间戳

        Returns:
            Event实例
        """
        data = extraction.event_data

        # 使用摘要或截取Episode内容作为摘要
        summary = data.get("summary", "")
        if not summary:
            summary = episode_content[:120]

        # 构建时间范围
        time_range = data.get("time_range", {})
        if time_range.get("start", 0) == 0:
            time_range["start"] = current_time
        if time_range.get("end", 0) == 0:
            time_range["end"] = current_time
        if not time_range.get("display_time_bucket", ""):
            time_range["display_time_bucket"] = time_bucket_from_ts(current_time)

        # 处理 consistency 字段（可能是字符串或浮点数）
        consistency_value = data.get("consistency", "uncertain")
        if isinstance(consistency_value, (int, float)):
            # 如果是浮点数，转换为对应的字符串
            if consistency_value >= 0.8:
                consistency_str = "consistent"
            elif consistency_value <= 0.2:
                consistency_str = "inconsistent"
            else:
                consistency_str = "uncertain"
        else:
            consistency_str = str(consistency_value)

        return Event(
            id=hash_summary(summary) if summary else "",
            summary=summary,
            action=data.get("action", ""),
            causality=data.get("causality", ""),
            time_range=time_range,
            last_active=current_time,
            participants=data.get("participants", []),
            location=data.get("location", {}),
            evidence=data.get("evidence", []),
            consistency=Consistency(consistency_str),
        )

    def _merge_event(
        self,
        event_id: str,
        incoming_event: Event,
        embedding: list[float],
        current_time: int,
    ) -> Event:
        """合并到现有事件

        Args:
            event_id: 目标事件ID
            incoming_event: 新事件
            embedding: 嵌入向量
            current_time: 当前时间戳

        Returns:
            合并后的事件
        """
        # 获取现有事件
        existing = self.store.get_event(event_id)
        if not existing:
            raise ValueError(f"Event not found: {event_id}")

        # 合并证据
        merged_evidence = existing.evidence + incoming_event.evidence

        # 更新事件
        existing.last_active = current_time
        existing.evidence = merged_evidence
        existing.embedding = embedding

        # 保存更新
        self.store.update_event(existing)

        return existing

    def _update_entity_relations(
        self,
        event_id: str,
        entities: list[str],
        current_time: int,
    ) -> int:
        """更新实体关系

        Args:
            event_id: 事件ID
            entities: 实体列表
            current_time: 当前时间戳

        Returns:
            新创建的实体数量
        """
        count = 0

        for entity in entities:
            # 获取实体名称
            entity_name = self._get_entity_name(entity)
            if not entity_name:
                continue

            # 确保实体存在
            is_new = self.store.ensure_entity(
                entity_name,
                entity.get("type", "UNKNOWN") if isinstance(entity, dict) else "UNKNOWN",
            )
            if is_new:
                count += 1

            # 更新或创建INVOLVES关系
            relation = self.store.get_involves_relation(event_id, entity_name)

            if relation:
                # 更新现有关系
                relation.c_valid += 1
                relation.t_valid = current_time
                self.store.update_involves_relation(relation)
                c_valid_new = relation.c_valid
            else:
                # 创建新关系
                self.store.create_involves_relation(
                    event_id=event_id,
                    entity_id=entity_name,
                    t_created=current_time,
                    t_valid=current_time,
                    c_valid=1,
                )
                c_valid_new = 1

            # 检查是否需要修剪/提升
            if c_valid_new > self.config.prune_threshold:
                self._prune_and_promote(event_id, current_time)

        return count

    def _prune_and_promote(self, event_id: str, current_time: int) -> None:
        """修剪证据并提升为永久特征

        Args:
            event_id: 事件ID
            current_time: 当前时间戳
        """
        # 修剪证据
        self._prune_event_evidence(event_id)

        # 提升为永久特征
        self.store.promote_permanent_trait(
            user_id=self.config.default_user_id,
            event_id=event_id,
            t_created=current_time,
        )

    def _prune_event_evidence(self, event_id: str) -> None:
        """修剪事件证据

        保留置信度最高的 Top-K 证据。

        Args:
            event_id: 事件ID
        """
        event = self.store.get_event(event_id)
        if not event or not event.evidence:
            return

        # 按置信度排序
        sorted_evidence = sorted(
            event.evidence,
            key=lambda item: float(item.get("confidence", 0.0)),
            reverse=True,
        )

        # 保留 Top-K
        event.evidence = sorted_evidence[: self.config.prune_top_k]
        self.store.update_event(event)

    def _get_embedding(self, text: str) -> list[float]:
        """获取文本嵌入向量

        Args:
            text: 输入文本

        Returns:
            嵌入向量
        """
        import dashscope
        dashscope.base_http_api_url = self.base_url
        dashscope.api_key = self.api_key

        resp = TextEmbedding.call(model=self.embedding_model, input=text)
        output = resp.output

        if isinstance(output, dict):
            return output["embeddings"][0]["embedding"]
        return output.embeddings[0].embedding

    def _get_entity_name(self, entity: Any) -> Optional[str]:
        """获取实体名称

        Args:
            entity: 实体（字符串或字典）

        Returns:
            实体名称
        """
        if isinstance(entity, dict):
            return entity.get("name")
        return str(entity) if entity else None
