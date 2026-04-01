# -*- coding: utf-8 -*-
"""Factory - 工厂方法

提供创建完整LTM系统的便捷方法。
"""

from dataclasses import fields
from typing import Any, Optional

from .core.memory import LTMemory
from .ltmemory_impl import LTMemoryImpl
from .storage.kuzu_store import KuzuStore
from .storage.graph_store import GraphStore
from .builder.memory_builder import MemoryBuilder, BuilderConfig
from .builder.extractor import AdaptiveExtractor, TwoStageExtractor
from .builder.consolidator import Consolidator
from .retriever.memory_searcher import MemorySearcher, SearcherConfig
from .retriever.entity_matcher import EntityMatcher
from .retriever.ranker import MemoryRanker, RankerConfig
from .evolution import DynamicEvolutionEngine, DynamicEvolutionConfig
from .config import (
    APPEND_FIRST_MODE,
    BULK_INGEST_MODE,
    CONTEXT_EXTRACTION_BATCH_SIZE,
    DB_PATH,
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    DEFERRED_EVOLUTION,
    ENABLE_DYNAMIC_EVOLUTION,
    ENABLE_EVENT_RELATIONS,
    EXTRACTOR_TYPE,
    GENERATION_MODEL,
    EMBEDDING_MODEL,
    LLM_CONCURRENCY,
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
    EPISODE_TTL,
    SEARCH_TOP_K,
    SEARCH_MAX_TOKENS,
    SEARCH_TEMPERATURE,
    SEARCH_ENABLE_VECTOR_MATCH,
    SEARCH_VECTOR_THRESHOLD,
    SEARCH_VECTOR_TOP_K,
    normalize_dashscope_base_url,
)
from .llm import DashScopeClient
from .utils import load_prompt


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
    base_url = normalize_dashscope_base_url(base_url or DASHSCOPE_BASE_URL)
    generation_model = config.get("generation_model", GENERATION_MODEL)
    embedding_model = config.get("embedding_model", EMBEDDING_MODEL)

    # 1. 创建统一的 LLM 客户端
    llm_client = DashScopeClient(
        api_key=api_key,
        base_url=base_url,
        generation_model=generation_model,
        embedding_model=embedding_model,
    )

    # 2. 创建存储层
    store = KuzuStore(
        db_path=config.get("db_path", db_path),
        embedding_client=llm_client,
    )

    # 3. 创建提取器
    extractor_type = str(config.get("extractor_type", EXTRACTOR_TYPE) or "adaptive").strip().lower()
    if extractor_type == "two_stage":
        extractor = TwoStageExtractor(
            generation_model=generation_model,
            enable_thinking=config.get("enable_thinking", False),
            llm_concurrency=config.get("llm_concurrency", LLM_CONCURRENCY),
            llm_client=llm_client,
        )
    else:
        extractor = AdaptiveExtractor(
            generation_model=generation_model,
            enable_thinking=config.get("enable_thinking", False),
            field_config=config.get("field_config"),
            plugins=config.get("extractor_plugins"),
            llm_client=llm_client,
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
        deferred_evolution=config.get("deferred_evolution", DEFERRED_EVOLUTION),
        llm_concurrency=config.get("llm_concurrency", LLM_CONCURRENCY),
    )

    dynamic_engine = None
    if config.get("enable_dynamic_evolution", ENABLE_DYNAMIC_EVOLUTION):
        dynamic_config = DynamicEvolutionConfig(
            append_first_mode=config.get("append_first_mode", APPEND_FIRST_MODE),
            llm_concurrency=config.get("llm_concurrency", LLM_CONCURRENCY),
            bulk_ingest_mode=config.get("bulk_ingest_mode", BULK_INGEST_MODE),
            merge_decision_strategy=config.get("merge_decision_strategy", "auto"),
            llm_api_key=api_key,
            llm_base_url=base_url,
            llm_model=generation_model,
            enable_auto_consolidation=config.get("enable_auto_consolidation", True),
            context_extraction_batch_size=config.get(
                "context_extraction_batch_size",
                CONTEXT_EXTRACTION_BATCH_SIZE,
            ),
            event_consolidation_candidate_limit=config.get("event_consolidation_candidate_limit", 12),
            event_consolidation_embedding_candidate_threshold=config.get(
                "event_consolidation_embedding_candidate_threshold",
                0.80,
            ),
            event_consolidation_embedding_top_k=config.get(
                "event_consolidation_embedding_top_k",
                8,
            ),
            enable_event_relations=config.get("enable_event_relations", ENABLE_EVENT_RELATIONS),
        )
        for field in fields(DynamicEvolutionConfig):
            if field.name in config:
                setattr(dynamic_config, field.name, config[field.name])
        dynamic_config.__post_init__()
        dynamic_engine = DynamicEvolutionEngine(
            store=store,
            config=dynamic_config,
            llm_client=llm_client,
        )

    builder = MemoryBuilder(
        extractor=extractor,
        consolidator=consolidator,
        store=store,
        config=builder_config,
        embedding_model=embedding_model,
        dynamic_engine=dynamic_engine,
        llm_client=llm_client,
    )

    # 6. 创建检索器组件
    entity_matcher = EntityMatcher(
        store=store,
        embedding_client=llm_client,
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
        generation_model=generation_model,
        dynamic_engine=dynamic_engine,
        offline_mode=False,
        llm_client=llm_client,
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
