# -*- coding: utf-8 -*-
import os
import re

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    def load_dotenv(*args, **kwargs):
        return False

load_dotenv()


DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DASHSCOPE_NATIVE_SDK_URL_RE = re.compile(
    r"/api/v\d+(?:/(?:services|chat/completions|embeddings))?/?$",
    re.IGNORECASE,
)


def env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def normalize_dashscope_base_url(base_url: str | None) -> str:
    """Normalize user-provided DashScope endpoints for the OpenAI-compatible mode.

    This project uses the OpenAI-compatible API (`/compatible-mode/v1`).
    Users who have the old native SDK URL (`.../api/v1`) in their .env will
    be automatically upgraded to the compatible-mode endpoint.
    """
    value = (base_url or DEFAULT_DASHSCOPE_BASE_URL).strip()
    if not value:
        return DEFAULT_DASHSCOPE_BASE_URL
    value = value.rstrip("/")
    return _DASHSCOPE_NATIVE_SDK_URL_RE.sub("/compatible-mode/v1", value)


# =========================
# Experiment Knobs (Research First)
# =========================
# These parameters correspond to the hyperparameters in the paper/algorithm.
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.65"))

# Multi-dimensional merge weights (must sum to 1.0)
# These control how different similarity dimensions contribute to event merging
MERGE_WEIGHT_SEMANTIC = float(os.getenv("MERGE_WEIGHT_SEMANTIC", "0.4"))
MERGE_WEIGHT_ENTITY = float(os.getenv("MERGE_WEIGHT_ENTITY", "0.3"))
MERGE_WEIGHT_TIME = float(os.getenv("MERGE_WEIGHT_TIME", "0.2"))
MERGE_WEIGHT_ACTION = float(os.getenv("MERGE_WEIGHT_ACTION", "0.1"))

# Time window for temporal proximity in merge decisions (seconds)
# Events within this window get boosted similarity scores
# Default: 300 seconds (5 minutes)
MERGE_TIME_WINDOW = int(os.getenv("MERGE_TIME_WINDOW", "300"))

# FIX: Adjusted DECAY_RATE for time-based memory retrieval
# For time differences in seconds, use 1e-8 for half-life of ~2.2 years
# Formula: half_life = ln(2) / DECAY_RATE = 0.693 / 1e-8 ≈ 69,300,000 seconds ≈ 2.2 years
# This allows memories to persist for years while still favoring recent events
DECAY_RATE = float(os.getenv("DECAY_RATE", "1e-8"))

EPISODE_TTL = int(os.getenv("EPISODE_TTL", "3600"))
PRUNE_C_VALID_THRESHOLD = int(os.getenv("PRUNE_C_VALID_THRESHOLD", "100"))
PRUNE_EVIDENCE_TOP_K = int(os.getenv("PRUNE_EVIDENCE_TOP_K", "5"))
DEFAULT_USER_ID = os.getenv("DEFAULT_USER_ID", "default_user")

# =========================
# Search/Retrieval Parameters
# =========================
# Top-K events to return from search
SEARCH_TOP_K = int(os.getenv("SEARCH_TOP_K", "5"))
# Weight decay rate for search (lambda parameter)
SEARCH_LAMBDA = float(os.getenv("SEARCH_LAMBDA", "1e-9"))
# Maximum entities to extract from query
SEARCH_MAX_ENTITIES = int(os.getenv("SEARCH_MAX_ENTITIES", "10"))
# LLM generation max tokens for answer
SEARCH_MAX_TOKENS = int(os.getenv("SEARCH_MAX_TOKENS", "512"))
# LLM temperature for answer generation
SEARCH_TEMPERATURE = float(os.getenv("SEARCH_TEMPERATURE", "0.7"))
# Enable vector similarity matching for entities
SEARCH_ENABLE_VECTOR_MATCH = env_bool("SEARCH_ENABLE_VECTOR_MATCH", True)
# Minimum similarity threshold for vector match
SEARCH_VECTOR_THRESHOLD = float(os.getenv("SEARCH_VECTOR_THRESHOLD", "0.5"))
# Top-K similar entities to retrieve via vector
SEARCH_VECTOR_TOP_K = int(os.getenv("SEARCH_VECTOR_TOP_K", "10"))
RANKER_ENTITY_SIGNAL_WEIGHT = float(os.getenv("RANKER_ENTITY_SIGNAL_WEIGHT", "0.40"))

# Dashscope / Aliyun settings.
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = normalize_dashscope_base_url(
    os.getenv("DASHSCOPE_BASE_URL", DEFAULT_DASHSCOPE_BASE_URL)
)
GENERATION_MODEL = os.getenv("GENERATION_MODEL", "qwen3-1.7b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v2")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "25"))
ENABLE_THINKING = env_bool("ENABLE_THINKING", False)
EXTRACTOR_TYPE = os.getenv("EXTRACTOR_TYPE", "adaptive").strip().lower()
SKIP_DYNAMIC_CHANGE_FILTER = env_bool("SKIP_DYNAMIC_CHANGE_FILTER", True)

# Dataset knobs.
TEST_DATA_PATH = os.getenv("TEST_DATA_PATH", "./example.json")
TEST_DATASET_KEY = os.getenv("TEST_DATASET_KEY", "input_records_1_mock")
MAX_EPISODES = int(os.getenv("MAX_EPISODES", "6"))

# DB / storage.
DB_PATH = os.getenv("DB_PATH", "./DB/demo_db.kz")

# =========================
# Dynamic Evolution Graph Parameters
# =========================
# Master switch for the dynamic evolution pipeline.
ENABLE_DYNAMIC_EVOLUTION = env_bool("ENABLE_DYNAMIC_EVOLUTION", True)
ENABLE_EVENT_RELATIONS = env_bool("ENABLE_EVENT_RELATIONS", True)
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "4"))
BULK_INGEST_MODE = env_bool("BULK_INGEST_MODE", False)
DEFERRED_EVOLUTION = env_bool("DEFERRED_EVOLUTION", False)

# Write path: append-first Event ingestion.
APPEND_FIRST_MODE = env_bool("APPEND_FIRST_MODE", True)
ENABLE_LEGACY_ONLINE_EVENT_MERGE = env_bool(
    "ENABLE_LEGACY_ONLINE_EVENT_MERGE", False
)

# Context resolution thresholds.
CONTEXT_REUSE_THRESHOLD = float(os.getenv("CONTEXT_REUSE_THRESHOLD", "0.50"))
CONTEXT_CONFLICT_THRESHOLD = float(os.getenv("CONTEXT_CONFLICT_THRESHOLD", "0.72"))
CONTEXT_CANDIDATE_LIMIT = int(os.getenv("CONTEXT_CANDIDATE_LIMIT", "24"))
CONTEXT_QUERY_CANDIDATE_LIMIT = int(os.getenv("CONTEXT_QUERY_CANDIDATE_LIMIT", "16"))
CONTEXT_EXTRACTION_BATCH_SIZE = int(os.getenv("CONTEXT_EXTRACTION_BATCH_SIZE", "8"))
CONTEXT_CORE_SLOT_WEIGHT = float(os.getenv("CONTEXT_CORE_SLOT_WEIGHT", "0.75"))
CONTEXT_AUX_SLOT_WEIGHT = float(os.getenv("CONTEXT_AUX_SLOT_WEIGHT", "0.25"))
CONTEXT_FUZZY_MATCH_THRESHOLD = float(
    os.getenv("CONTEXT_FUZZY_MATCH_THRESHOLD", "0.45")
)
CONTEXT_SUMMARY_CONTAINMENT_BONUS = float(
    os.getenv("CONTEXT_SUMMARY_CONTAINMENT_BONUS", "0.60")
)
CONTEXT_SUMMARY_SEMANTIC_LEXICAL_WEIGHT = float(
    os.getenv("CONTEXT_SUMMARY_SEMANTIC_LEXICAL_WEIGHT", "0.40")
)
CONTEXT_SUMMARY_SEMANTIC_EMBEDDING_WEIGHT = float(
    os.getenv("CONTEXT_SUMMARY_SEMANTIC_EMBEDDING_WEIGHT", "0.60")
)
CONTEXT_SPARSE_SLOT_SUMMARY_FALLBACK = float(
    os.getenv("CONTEXT_SPARSE_SLOT_SUMMARY_FALLBACK", "0.30")
)
CONTEXT_MERGE_CONTAINMENT_WEIGHT_DENSE = float(
    os.getenv("CONTEXT_MERGE_CONTAINMENT_WEIGHT_DENSE", "0.30")
)
CONTEXT_MERGE_CONTAINMENT_WEIGHT_MID = float(
    os.getenv("CONTEXT_MERGE_CONTAINMENT_WEIGHT_MID", "0.15")
)
CONTEXT_MERGE_CONTAINMENT_WEIGHT_SPARSE = float(
    os.getenv("CONTEXT_MERGE_CONTAINMENT_WEIGHT_SPARSE", "0.05")
)
CONTEXT_SIMILARITY_SLOT_WEIGHT = float(
    os.getenv("CONTEXT_SIMILARITY_SLOT_WEIGHT", "0.28")
)
CONTEXT_SIMILARITY_SET_SLOT_WEIGHT = float(
    os.getenv("CONTEXT_SIMILARITY_SET_SLOT_WEIGHT", "0.10")
)
CONTEXT_SIMILARITY_SUMMARY_WEIGHT = float(
    os.getenv("CONTEXT_SIMILARITY_SUMMARY_WEIGHT", "0.30")
)
CONTEXT_SIMILARITY_SUBTYPE_WEIGHT = float(
    os.getenv("CONTEXT_SIMILARITY_SUBTYPE_WEIGHT", "0.14")
)
CONTEXT_SIMILARITY_ACTIVE_WEIGHT = float(
    os.getenv("CONTEXT_SIMILARITY_ACTIVE_WEIGHT", "0.06")
)
CONTEXT_SIMILARITY_TEMPORAL_WEIGHT = float(
    os.getenv("CONTEXT_SIMILARITY_TEMPORAL_WEIGHT", "0.08")
)
CONTEXT_SIMILARITY_EMBEDDING_WEIGHT = float(
    os.getenv("CONTEXT_SIMILARITY_EMBEDDING_WEIGHT", "0.04")
)
CONTEXT_SUBTYPE_COMPATIBLE_SCORE = float(
    os.getenv("CONTEXT_SUBTYPE_COMPATIBLE_SCORE", "0.78")
)
CONTEXT_SUBTYPE_MISMATCH_FLOOR = float(
    os.getenv("CONTEXT_SUBTYPE_MISMATCH_FLOOR", "0.15")
)

# Reinforcement / decay controls.
REINFORCEMENT_STEP = float(os.getenv("REINFORCEMENT_STEP", "0.06"))
DECAY_STEP = float(os.getenv("DECAY_STEP", "0.04"))
STALE_SECONDS = int(os.getenv("STALE_SECONDS", "2592000"))  # 30 days
ARCHIVE_EVENT_SECONDS = int(os.getenv("ARCHIVE_EVENT_SECONDS", "7776000"))  # 90 days

# Retrieval (evolution-aware) weights.
RETRIEVAL_WEIGHT_EVENT_SIM = float(os.getenv("RETRIEVAL_WEIGHT_EVENT_SIM", "0.33"))
RETRIEVAL_WEIGHT_CONTEXT = float(os.getenv("RETRIEVAL_WEIGHT_CONTEXT", "0.20"))
RETRIEVAL_WEIGHT_RECENCY = float(os.getenv("RETRIEVAL_WEIGHT_RECENCY", "0.10"))
RETRIEVAL_WEIGHT_VALIDITY = float(os.getenv("RETRIEVAL_WEIGHT_VALIDITY", "0.10"))
RETRIEVAL_WEIGHT_SUPPORT = float(os.getenv("RETRIEVAL_WEIGHT_SUPPORT", "0.05"))
RETRIEVAL_DEFAULT_CANDIDATE_LIMIT = int(
    os.getenv("RETRIEVAL_DEFAULT_CANDIDATE_LIMIT", "40")
)

# Consolidation / forgetting.
ENABLE_AUTO_CONSOLIDATION = env_bool("ENABLE_AUTO_CONSOLIDATION", True)
CONSOLIDATION_MIN_INTERVAL_SECONDS = int(
    os.getenv("CONSOLIDATION_MIN_INTERVAL_SECONDS", "1800")
)
WEAK_EDGE_PRUNE_THRESHOLD = float(os.getenv("WEAK_EDGE_PRUNE_THRESHOLD", "0.18"))
CONSOLIDATION_LOG_PATH = os.getenv(
    "CONSOLIDATION_LOG_PATH", "./outputs/consolidation_log.jsonl"
)
EVENT_CONSOLIDATION_WINDOW_SECONDS = int(
    os.getenv("EVENT_CONSOLIDATION_WINDOW_SECONDS", "172800")
)
EVENT_CONSOLIDATION_CANDIDATE_LIMIT = int(
    os.getenv("EVENT_CONSOLIDATION_CANDIDATE_LIMIT", "12")
)
EVENT_CONSOLIDATION_EMBEDDING_CANDIDATE_THRESHOLD = float(
    os.getenv("EVENT_CONSOLIDATION_EMBEDDING_CANDIDATE_THRESHOLD", "0.80")
)
EVENT_CONSOLIDATION_EMBEDDING_TOP_K = int(
    os.getenv("EVENT_CONSOLIDATION_EMBEDDING_TOP_K", "8")
)
EVENT_CONSOLIDATION_THRESHOLD = float(
    os.getenv("EVENT_CONSOLIDATION_THRESHOLD", "0.78")
)
EVENT_CONSOLIDATION_TEXT_WEIGHT = float(
    os.getenv("EVENT_CONSOLIDATION_TEXT_WEIGHT", "0.28")
)
EVENT_CONSOLIDATION_CONTEXT_WEIGHT = float(
    os.getenv("EVENT_CONSOLIDATION_CONTEXT_WEIGHT", "0.22")
)
EVENT_CONSOLIDATION_PAYLOAD_WEIGHT = float(
    os.getenv("EVENT_CONSOLIDATION_PAYLOAD_WEIGHT", "0.20")
)
EVENT_CONSOLIDATION_TIME_WEIGHT = float(
    os.getenv("EVENT_CONSOLIDATION_TIME_WEIGHT", "0.12")
)
EVENT_MERGE_TRACE_STRATEGY_VERSION = os.getenv(
    "EVENT_MERGE_TRACE_STRATEGY_VERSION", "v1"
)
EVENT_MERGE_TRACE_LOG_PATH = os.getenv(
    "EVENT_MERGE_TRACE_LOG_PATH", "./outputs/event_merge_trace.jsonl"
)

# Visualization knobs.
EXPORT_GRAPH = env_bool("EXPORT_GRAPH", True)
EXPORT_EVERY_EPISODE = env_bool("EXPORT_EVERY_EPISODE", True)
EXPORT_FULL_GRAPH = env_bool("EXPORT_FULL_GRAPH", True)
INCLUDE_EPISODES_IN_GRAPH = env_bool("INCLUDE_EPISODES_IN_GRAPH", False)
GRAPH_OUTPUT_DIR = os.getenv("GRAPH_OUTPUT_DIR", "./outputs")
GRAPH_MAX_EVENTS = int(os.getenv("GRAPH_MAX_EVENTS", "0"))
KUZU_EXPLORER_URL = os.getenv("KUZU_EXPLORER_URL", "")
