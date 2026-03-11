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
from .builder.extractor import TwoStageExtractor
from .builder.consolidator import Consolidator
from .retriever.memory_searcher import MemorySearcher, SearcherConfig
from .retriever.entity_matcher import EntityMatcher
from .retriever.ranker import MemoryRanker, RankerConfig
from .config import (
    DB_PATH,
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    GENERATION_MODEL,
    EMBEDDING_MODEL,
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

    # 1. 创建嵌入客户端
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
    )

    builder = MemoryBuilder(
        extractor=extractor,
        consolidator=consolidator,
        store=store,
        config=builder_config,
        api_key=api_key,
        base_url=base_url,
        embedding_model=config.get("embedding_model", EMBEDDING_MODEL),
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
    )

    # 7. 组装系统
    return LTMemoryImpl(
        store=store,
        builder=builder,
        searcher=searcher,
        episode_ttl=config.get("episode_ttl", EPISODE_TTL),
        decay_rate=config.get("decay_rate", DECAY_RATE),
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
