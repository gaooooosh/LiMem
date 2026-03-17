# -*- coding: utf-8 -*-
"""LiMem - 长时记忆系统

推荐使用新版抽象 API：

```python
from limem import create_ltm, Episode, Event, LTMemory
ltm = create_ltm(db_path="./memory.kz")
result = ltm.ingest_text("用户喜欢听周杰伦的歌")
search_result = ltm.search("用户喜欢什么音乐？")
```
"""

# ==================== 新版核心抽象 ====================
from .core import (
    Episode,
    Event,
    EventRelation,
    RankedEvent,
    Consistency,
    Context,
    Pattern,
    Entity,
    LTMemory,
    SearchResult,
    IngestResult,
)

# ==================== 工厂方法 ====================
from .factory import create_ltm, create_ltm_from_env, EmbeddingClient

# ==================== 存储层 ====================
from .storage import GraphStore, KuzuStore

# ==================== 构建层 ====================
from .builder import (
    LLMExtractor,
    TwoStageExtractor,
    HeuristicExtractor,
    ExtractionResult,
    Consolidator,
    ConsolidationResult,
    MemoryBuilder,
    BuilderConfig,
)

# ==================== 检索层 ====================
from .retriever import (
    EntityMatcher,
    MatchResult,
    MemoryRanker,
    RankerConfig,
    WeightDebugInfo,
    MemorySearcher,
    SearcherConfig,
)

# ==================== 系统实现 ====================
from .ltmemory_impl import LTMemoryImpl
from .ops import MemoryGraphOps
from .trips_debugger import TripsDebuggerConfig, TripsDebuggerSession, create_trips_debugger_app
from .evolution import DynamicEvolutionEngine, DynamicEvolutionConfig
from .migration import migrate_to_dynamic_graph, MigrationReport, LegacyEdgeAdapter

# ==================== 数据库和工具 ====================
from .db import open_connection, init_db

# ==================== 配置（从 config.py 重新导出）====================
from .config import (
    # 实验参数
    SIMILARITY_THRESHOLD,
    MERGE_WEIGHT_SEMANTIC,
    MERGE_WEIGHT_ENTITY,
    MERGE_WEIGHT_TIME,
    MERGE_WEIGHT_ACTION,
    MERGE_TIME_WINDOW,
    DECAY_RATE,
    EPISODE_TTL,
    PRUNE_C_VALID_THRESHOLD,
    PRUNE_EVIDENCE_TOP_K,
    DEFAULT_USER_ID,
    # 搜索参数
    SEARCH_TOP_K,
    SEARCH_LAMBDA,
    SEARCH_MAX_ENTITIES,
    SEARCH_MAX_TOKENS,
    SEARCH_TEMPERATURE,
    SEARCH_ENABLE_VECTOR_MATCH,
    SEARCH_VECTOR_THRESHOLD,
    SEARCH_VECTOR_TOP_K,
    # 模型设置
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    GENERATION_MODEL,
    EMBEDDING_MODEL,
    ENABLE_THINKING,
    # 数据集参数
    TEST_DATA_PATH,
    TEST_DATASET_KEY,
    MAX_EPISODES,
    # 数据库路径
    DB_PATH,
    ENABLE_DYNAMIC_EVOLUTION,
    APPEND_FIRST_MODE,
    CONTEXT_REUSE_THRESHOLD,
    CONTEXT_CONFLICT_THRESHOLD,
    CONTEXT_CANDIDATE_LIMIT,
    ENABLE_EVENT_RELATIONS,
    EVENT_RELATION_WINDOW_SECONDS,
    EVENT_RELATION_CANDIDATE_LIMIT,
    EVENT_RELATION_MAX_LINKS_PER_EVENT,
    EVENT_RELATION_CONFIDENCE_THRESHOLD,
    NEXT_RECENT_WINDOW_SECONDS,
    NEXT_MAX_PREDECESSORS,
    NEXT_MIN_SCORE,
    PATTERN_ASSIGN_THRESHOLD,
    PATTERN_DRIFT_THRESHOLD,
    PATTERN_SPLIT_DRIFT_THRESHOLD,
    PATTERN_MERGE_THRESHOLD,
    PATTERN_CANDIDATE_LIMIT,
    REINFORCEMENT_STEP,
    DECAY_STEP,
    STALE_SECONDS,
    ARCHIVE_EVENT_SECONDS,
    RETRIEVAL_WEIGHT_EVENT_SIM,
    RETRIEVAL_WEIGHT_CONTEXT,
    RETRIEVAL_WEIGHT_PATTERN,
    RETRIEVAL_WEIGHT_RECENCY,
    RETRIEVAL_WEIGHT_VALIDITY,
    RETRIEVAL_WEIGHT_SUPPORT,
    ENABLE_AUTO_CONSOLIDATION,
    CONSOLIDATION_MIN_INTERVAL_SECONDS,
    WEAK_EDGE_PRUNE_THRESHOLD,
    CONSOLIDATION_LOG_PATH,
    OFFLINE_MODE,
)

__all__ = [
    # ===== 新版核心抽象 =====
    "Episode",
    "Event",
    "EventRelation",
    "RankedEvent",
    "Consistency",
    "Context",
    "Pattern",
    "Entity",
    "LTMemory",
    "SearchResult",
    "IngestResult",
    # ===== 工厂方法 =====
    "create_ltm",
    "create_ltm_from_env",
    "EmbeddingClient",
    # ===== 存储层 =====
    "GraphStore",
    "KuzuStore",
    # ===== 构建层 =====
    "LLMExtractor",
    "TwoStageExtractor",
    "HeuristicExtractor",
    "ExtractionResult",
    "Consolidator",
    "ConsolidationResult",
    "MemoryBuilder",
    "BuilderConfig",
    # ===== 检索层 =====
    "EntityMatcher",
    "MatchResult",
    "MemoryRanker",
    "RankerConfig",
    "WeightDebugInfo",
    "MemorySearcher",
    "SearcherConfig",
    # ===== 系统实现 =====
    "LTMemoryImpl",
    "MemoryGraphOps",
    "TripsDebuggerConfig",
    "TripsDebuggerSession",
    "create_trips_debugger_app",
    "DynamicEvolutionEngine",
    "DynamicEvolutionConfig",
    "migrate_to_dynamic_graph",
    "MigrationReport",
    "LegacyEdgeAdapter",
    # ===== 数据库和工具 =====
    "open_connection",
    "init_db",
    # ===== 配置 =====
    "SIMILARITY_THRESHOLD",
    "MERGE_WEIGHT_SEMANTIC",
    "MERGE_WEIGHT_ENTITY",
    "MERGE_WEIGHT_TIME",
    "MERGE_WEIGHT_ACTION",
    "MERGE_TIME_WINDOW",
    "DECAY_RATE",
    "EPISODE_TTL",
    "PRUNE_C_VALID_THRESHOLD",
    "PRUNE_EVIDENCE_TOP_K",
    "DEFAULT_USER_ID",
    "SEARCH_TOP_K",
    "SEARCH_LAMBDA",
    "SEARCH_MAX_ENTITIES",
    "SEARCH_MAX_TOKENS",
    "SEARCH_TEMPERATURE",
    "SEARCH_ENABLE_VECTOR_MATCH",
    "SEARCH_VECTOR_THRESHOLD",
    "SEARCH_VECTOR_TOP_K",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_BASE_URL",
    "GENERATION_MODEL",
    "EMBEDDING_MODEL",
    "ENABLE_THINKING",
    "TEST_DATA_PATH",
    "TEST_DATASET_KEY",
    "MAX_EPISODES",
    "DB_PATH",
    "ENABLE_DYNAMIC_EVOLUTION",
    "APPEND_FIRST_MODE",
    "CONTEXT_REUSE_THRESHOLD",
    "CONTEXT_CONFLICT_THRESHOLD",
    "CONTEXT_CANDIDATE_LIMIT",
    "ENABLE_EVENT_RELATIONS",
    "EVENT_RELATION_WINDOW_SECONDS",
    "EVENT_RELATION_CANDIDATE_LIMIT",
    "EVENT_RELATION_MAX_LINKS_PER_EVENT",
    "EVENT_RELATION_CONFIDENCE_THRESHOLD",
    "NEXT_RECENT_WINDOW_SECONDS",
    "NEXT_MAX_PREDECESSORS",
    "NEXT_MIN_SCORE",
    "PATTERN_ASSIGN_THRESHOLD",
    "PATTERN_DRIFT_THRESHOLD",
    "PATTERN_SPLIT_DRIFT_THRESHOLD",
    "PATTERN_MERGE_THRESHOLD",
    "PATTERN_CANDIDATE_LIMIT",
    "REINFORCEMENT_STEP",
    "DECAY_STEP",
    "STALE_SECONDS",
    "ARCHIVE_EVENT_SECONDS",
    "RETRIEVAL_WEIGHT_EVENT_SIM",
    "RETRIEVAL_WEIGHT_CONTEXT",
    "RETRIEVAL_WEIGHT_PATTERN",
    "RETRIEVAL_WEIGHT_RECENCY",
    "RETRIEVAL_WEIGHT_VALIDITY",
    "RETRIEVAL_WEIGHT_SUPPORT",
    "ENABLE_AUTO_CONSOLIDATION",
    "CONSOLIDATION_MIN_INTERVAL_SECONDS",
    "WEAK_EDGE_PRUNE_THRESHOLD",
    "CONSOLIDATION_LOG_PATH",
    "OFFLINE_MODE",
]

# 版本信息
__version__ = "0.2.0"
__author__ = "LiMem Team"
