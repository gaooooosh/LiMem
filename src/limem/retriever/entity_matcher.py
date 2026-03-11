# -*- coding: utf-8 -*-
"""EntityMatcher - 实体匹配器

实现三级实体匹配策略：精确匹配、包含匹配、向量模糊匹配。
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple
import math
import time

from ..config import (
    SEARCH_ENABLE_VECTOR_MATCH,
    SEARCH_VECTOR_THRESHOLD,
    SEARCH_VECTOR_TOP_K,
)


@dataclass
class MatchResult:
    """单个查询实体的匹配结果

    Attributes:
        query_entity: 查询实体名称
        matched_entities: 匹配到的数据库实体列表
        match_type: 匹配类型（exact, containment, fuzzy, exact+fuzzy）
        weights: 实体ID到匹配权重的映射
    """

    query_entity: str
    matched_entities: list[str] = field(default_factory=list)
    match_type: str = ""
    weights: dict[str, float] = field(default_factory=dict)


class EntityMatcher:
    """实体匹配器

    职责：实现三级匹配策略，将查询实体匹配到数据库实体。

    匹配策略：
    1. 精确匹配（exact）: query == db_entity, weight = 1.0
    2. 包含匹配（containment）: query in db_entity, weight = 0.9
    3. 向量模糊匹配（fuzzy）: 语义相似度, weight = similarity²
    """

    def __init__(
        self,
        store,  # GraphStore
        embedding_client=None,
        enable_vector: bool = SEARCH_ENABLE_VECTOR_MATCH,
        vector_threshold: float = SEARCH_VECTOR_THRESHOLD,
        vector_top_k: int = SEARCH_VECTOR_TOP_K,
    ):
        """初始化实体匹配器

        Args:
            store: 图存储接口
            embedding_client: 嵌入向量客户端
            enable_vector: 是否启用向量匹配
            vector_threshold: 向量相似度阈值
            vector_top_k: 向量匹配Top-K数量
        """
        self.store = store
        self.embedding_client = embedding_client
        self.enable_vector = enable_vector
        self.vector_threshold = vector_threshold
        self.vector_top_k = vector_top_k

        # 缓存
        self._entity_embedding_cache: dict[str, list[float]] = {}
        self._db_entity_embeddings_cache: Optional[dict[str, list[float]]] = None
        self._db_entity_cache_time: float = 0
        self._db_entity_cache_ttl: float = 60.0  # 缓存TTL（秒）

    def match(self, query_entities: list[str]) -> dict[str, MatchResult]:
        """执行实体匹配

        Args:
            query_entities: 查询实体列表

        Returns:
            查询实体到匹配结果的映射
        """
        if not query_entities:
            return {}

        results: dict[str, MatchResult] = {}

        # 获取所有数据库实体
        db_entities = set(self.store.get_all_entities())

        for query_entity in query_entities:
            matched = []
            weights = {}
            match_types = set()

            # Level 1: 精确匹配
            if query_entity in db_entities:
                matched.append(query_entity)
                weights[query_entity] = 1.0
                match_types.add("exact")

            # Level 2: 包含匹配
            containment_matches = self._containment_match(query_entity, db_entities)
            for entity in containment_matches:
                if entity not in weights:
                    matched.append(entity)
                    weights[entity] = 0.9
                match_types.add("containment")

            # Level 3: 向量模糊匹配
            if self.enable_vector:
                fuzzy_matches = self._vector_match(query_entity, db_entities)
                for entity, sim in fuzzy_matches:
                    if entity not in weights:
                        matched.append(entity)
                        weights[entity] = sim ** 2  # 平方放大差异
                    match_types.add("fuzzy")

            # 确定匹配类型
            if match_types:
                match_type = "+".join(sorted(match_types))
                results[query_entity] = MatchResult(
                    query_entity=query_entity,
                    matched_entities=matched,
                    match_type=match_type,
                    weights=weights,
                )

        # 打印匹配摘要
        if results:
            total_matched = sum(len(r.matched_entities) for r in results.values())
            print(f"🎯 Matched {total_matched} entities for {len(results)} query entities")

        return results

    def get_matched_db_entities(self, results: dict[str, MatchResult]) -> dict[str, float]:
        """获取所有匹配的数据库实体及其最高权重

        Args:
            results: 匹配结果

        Returns:
            数据库实体ID到最高权重的映射
        """
        merged = {}
        for result in results.values():
            for entity_id, weight in result.weights.items():
                if entity_id not in merged or weight > merged[entity_id]:
                    merged[entity_id] = weight
        return merged

    def _containment_match(
        self, query: str, db_entities: set[str]
    ) -> list[str]:
        """包含匹配

        Args:
            query: 查询实体
            db_entities: 数据库实体集合

        Returns:
            匹配到的实体列表
        """
        if len(query) < 2:  # 跳过单字符
            return []

        matches = []
        for entity in db_entities:
            if query in entity and query != entity:
                matches.append(entity)
        return matches

    def _vector_match(
        self, query: str, db_entities: set[str]
    ) -> list[Tuple[str, float]]:
        """向量模糊匹配

        Args:
            query: 查询实体
            db_entities: 数据库实体集合

        Returns:
            (实体ID, 相似度) 列表
        """
        if not self.embedding_client:
            return []

        # 获取查询实体嵌入
        query_embedding = self._get_entity_embedding(query)
        if not query_embedding:
            return []

        # 获取数据库实体嵌入（使用缓存）
        db_embeddings = self._get_db_entity_embeddings()
        if not db_embeddings:
            return []

        # 计算相似度
        similarities = []
        for entity_id, db_emb in db_embeddings.items():
            if entity_id not in db_entities:
                continue
            sim = self._cosine_similarity(query_embedding, db_emb)
            if sim >= self.vector_threshold:
                similarities.append((entity_id, sim))

        # 返回Top-K
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[: self.vector_top_k]

    def _get_entity_embedding(self, entity: str) -> Optional[list[float]]:
        """获取实体嵌入（带缓存）"""
        if entity in self._entity_embedding_cache:
            return self._entity_embedding_cache[entity]

        if not self.embedding_client:
            return None

        try:
            embedding = self.embedding_client.get_embedding(entity)
            self._entity_embedding_cache[entity] = embedding
            return embedding
        except Exception as ex:
            print(f"⚠️ Failed to generate embedding for '{entity}': {ex}")
            return None

    def _get_db_entity_embeddings(self) -> dict[str, list[float]]:
        """获取数据库实体嵌入（带缓存）"""
        current_time = time.time()

        # 使用缓存
        if (
            self._db_entity_embeddings_cache is not None
            and current_time - self._db_entity_cache_time < self._db_entity_cache_ttl
        ):
            return self._db_entity_embeddings_cache

        # 从数据库获取
        try:
            embeddings = self.store.get_entity_embeddings(
                self.store.get_all_entities()
            )
            self._db_entity_embeddings_cache = embeddings
            self._db_entity_cache_time = current_time
            return embeddings
        except Exception as ex:
            print(f"⚠️ Failed to fetch entity embeddings: {ex}")
            return {}

    def _cosine_similarity(
        self, vec_a: list[float], vec_b: list[float]
    ) -> float:
        """计算余弦相似度"""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
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
