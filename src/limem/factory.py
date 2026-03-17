# -*- coding: utf-8 -*-
"""Factory - 工厂方法

提供创建完整LTM系统的便捷方法。
"""

from typing import Any, Optional

from .core.memory import LTMemory
from .ltmemory_impl import LTMemoryImpl
from .storage.kuzu_store import KuzuStore
from .storage.graph_store import GraphStore
from .builder.memory_builder import MemoryBuilder, BuilderConfig
from .builder.extractor import TwoStageExtractor, HeuristicExtractor
from .builder.consolidator import Consolidator
from .retriever.memory_searcher import MemorySearcher, SearcherConfig
from .retriever.entity_matcher import EntityMatcher
from .retriever.ranker import MemoryRanker, RankerConfig
from .evolution import DynamicEvolutionEngine, DynamicEvolutionConfig
from .config import (
    APPEND_FIRST_MODE,
    DB_PATH,
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    ENABLE_DYNAMIC_EVOLUTION,
    ENABLE_EVENT_RELATIONS,
    GENERATION_MODEL,
    EMBEDDING_MODEL,
    OFFLINE_MODE,
    SIMILARITY_THRESHOLD,
    MERGE_WEIGHT_SEMANTIC,
    MERGE_WEIGHT_ENTITY,
    MERGE_WEIGHT_TIME,
    MERGE_WEIGHT_ACTION,
    MERGE_TIME_WINDOW,
    DECAY_RATE,
    PRUNE_C_VALID_THRESHOLD,
    PRUNE_EVIDENCE_TOP_K,
    DEFAULT_USER_ID,
    EVENT_RELATION_WINDOW_SECONDS,
    EVENT_RELATION_CANDIDATE_LIMIT,
    EVENT_RELATION_MAX_LINKS_PER_EVENT,
    EVENT_RELATION_CONFIDENCE_THRESHOLD,
    EPISODE_TTL,
    SEARCH_TOP_K,
    SEARCH_MAX_TOKENS,
    SEARCH_TEMPERATURE,
    SEARCH_ENABLE_VECTOR_MATCH,
    SEARCH_VECTOR_THRESHOLD,
    SEARCH_VECTOR_TOP_K,
)
from .utils import load_prompt


class EmbeddingClient:
    """嵌入向量客户端包装

    提供统一的嵌入向量生成接口。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ):
        from dashscope import TextEmbedding
        import dashscope

        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.embedding_model = embedding_model or EMBEDDING_MODEL

        dashscope.base_http_api_url = self.base_url
        dashscope.api_key = self.api_key

    def get_embedding(self, text: str) -> list[float]:
        """获取文本嵌入向量"""
        from dashscope import TextEmbedding

        resp = TextEmbedding.call(model=self.embedding_model, input=text)
        output = resp.output

        if isinstance(output, dict):
            return output["embeddings"][0]["embedding"]
        return output.embeddings[0].embedding


class HashEmbeddingClient:
    """Deterministic local embedding fallback for offline mode."""

    def __init__(self, dim: int = 1536):
        self.dim = dim

    def get_embedding(self, text: str) -> list[float]:
        import hashlib
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = [0.0] * self.dim
        for i, b in enumerate(digest):
            idx = i % self.dim
            vec[idx] += (b / 255.0) * 2.0 - 1.0
        norm = sum(x * x for x in vec) ** 0.5
        if norm == 0:
            return vec
        return [x / norm for x in vec]


def create_ltm_system(
    db_path: str = DB_PATH,
    config: Optional[dict[str, Any]] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> LTMemoryImpl:
    """工厂方法：创建完整的LTM系统

    这是创建LTM系统的推荐方式，自动配置所有组件。

    Args:
        db_path: 数据库路径
        config: 配置字典（覆盖默认配置）
        api_key: DashScope API密钥
        base_url: DashScope API URL

    Returns:
        配置好的LTMemoryImpl实例

    Example:
        >>> from limem import create_ltm_system
        >>> ltm = create_ltm_system(db_path="./my_memory.kz")
        >>> result = ltm.ingest_text("用户喜欢听周杰伦的歌")
        >>> search_result = ltm.search("用户喜欢什么音乐？")
    """
    config = config or {}
    api_key = api_key or DASHSCOPE_API_KEY
    base_url = base_url or DASHSCOPE_BASE_URL
    offline_mode = bool(config.get("offline_mode", OFFLINE_MODE))

    # 1. 创建嵌入客户端
    if offline_mode:
        embedding_client = HashEmbeddingClient()
    else:
        embedding_client = EmbeddingClient(
            api_key=api_key,
            base_url=base_url,
            embedding_model=config.get("embedding_model", EMBEDDING_MODEL),
        )

    # 2. 创建存储层
    store = KuzuStore(
        db_path=config.get("db_path", db_path),
        embedding_client=embedding_client,
    )

    # 3. 创建提取器
    if offline_mode:
        extractor = HeuristicExtractor()
    else:
        extractor = TwoStageExtractor(
            api_key=api_key,
            base_url=base_url,
            generation_model=config.get("generation_model", GENERATION_MODEL),
            enable_thinking=config.get("enable_thinking", False),
        )

    # 4. 创建合并器
    consolidator = Consolidator(
        store=store,
        similarity_threshold=config.get("similarity_threshold", SIMILARITY_THRESHOLD),
        weights=config.get("merge_weights", {
            "semantic": MERGE_WEIGHT_SEMANTIC,
            "entity": MERGE_WEIGHT_ENTITY,
            "time": MERGE_WEIGHT_TIME,
            "action": MERGE_WEIGHT_ACTION,
        }),
        time_window=config.get("merge_time_window", MERGE_TIME_WINDOW),
    )

    # 5. 创建构建器
    builder_config = BuilderConfig(
        prune_threshold=config.get("prune_threshold", PRUNE_C_VALID_THRESHOLD),
        prune_top_k=config.get("prune_top_k", PRUNE_EVIDENCE_TOP_K),
        default_user_id=config.get("default_user_id", DEFAULT_USER_ID),
        append_first_mode=config.get("append_first_mode", APPEND_FIRST_MODE),
    )

    dynamic_engine = None
    if config.get("enable_dynamic_evolution", ENABLE_DYNAMIC_EVOLUTION):
        dynamic_engine = DynamicEvolutionEngine(
            store=store,
            config=DynamicEvolutionConfig(
                append_first_mode=config.get("append_first_mode", APPEND_FIRST_MODE),
                offline_mode=offline_mode,
                merge_decision_strategy=config.get("merge_decision_strategy", "auto"),
                llm_api_key=api_key,
                llm_base_url=base_url,
                llm_model=config.get("generation_model", GENERATION_MODEL),
                enable_auto_consolidation=config.get("enable_auto_consolidation", True),
                enable_event_relations=config.get("enable_event_relations", ENABLE_EVENT_RELATIONS),
                event_relation_window_seconds=config.get("event_relation_window_seconds", EVENT_RELATION_WINDOW_SECONDS),
                event_relation_candidate_limit=config.get("event_relation_candidate_limit", EVENT_RELATION_CANDIDATE_LIMIT),
                event_relation_max_links_per_event=config.get("event_relation_max_links_per_event", EVENT_RELATION_MAX_LINKS_PER_EVENT),
                event_relation_confidence_threshold=config.get("event_relation_confidence_threshold", EVENT_RELATION_CONFIDENCE_THRESHOLD),
            ),
        )

    builder = MemoryBuilder(
        extractor=extractor,
        consolidator=consolidator,
        store=store,
        config=builder_config,
        api_key=api_key,
        base_url=base_url,
        embedding_model=config.get("embedding_model", EMBEDDING_MODEL),
        dynamic_engine=dynamic_engine,
    )

    # 6. 创建检索器组件
    entity_matcher = EntityMatcher(
        store=store,
        embedding_client=embedding_client,
        enable_vector=config.get("enable_vector_match", SEARCH_ENABLE_VECTOR_MATCH),
        vector_threshold=config.get("vector_threshold", SEARCH_VECTOR_THRESHOLD),
        vector_top_k=config.get("vector_top_k", SEARCH_VECTOR_TOP_K),
    )

    ranker_config = RankerConfig(
        decay_rate=config.get("decay_rate", DECAY_RATE),
        precise_boost=config.get("precise_boost", 0.5),
        fuzzy_discount=config.get("fuzzy_discount", 0.5),
    )

    ranker = MemoryRanker(config=ranker_config)

    searcher_config = SearcherConfig(
        default_top_k=config.get("search_top_k", SEARCH_TOP_K),
        max_tokens=config.get("search_max_tokens", SEARCH_MAX_TOKENS),
        temperature=config.get("search_temperature", SEARCH_TEMPERATURE),
        generate_answer=config.get("generate_answer", True),
    )

    searcher = MemorySearcher(
        store=store,
        entity_matcher=entity_matcher,
        ranker=ranker,
        config=searcher_config,
        api_key=api_key,
        base_url=base_url,
        generation_model=config.get("generation_model", GENERATION_MODEL),
        dynamic_engine=dynamic_engine,
        offline_mode=offline_mode,
    )

    # 7. 组装系统
    return LTMemoryImpl(
        store=store,
        builder=builder,
        searcher=searcher,
        episode_ttl=config.get("episode_ttl", EPISODE_TTL),
        decay_rate=config.get("decay_rate", DECAY_RATE),
        dynamic_engine=dynamic_engine,
    )


def create_ltm_from_env() -> LTMemoryImpl:
    """从环境变量创建LTM系统

    使用 .env 文件中的配置创建系统。

    Returns:
        配置好的LTMemoryImpl实例
    """
    return create_ltm_system()


# 别名
create_ltm = create_ltm_system
