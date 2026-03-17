# -*- coding: utf-8 -*-
import os

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    def load_dotenv(*args, **kwargs):
        return False

load_dotenv()


def env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


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

# Dashscope / Aliyun settings.
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL = os.getenv(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"
)
GENERATION_MODEL = os.getenv("GENERATION_MODEL", "qwen3-1.7b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v2")
ENABLE_THINKING = env_bool("ENABLE_THINKING", False)

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

# Write path: append-first Event ingestion.
APPEND_FIRST_MODE = env_bool("APPEND_FIRST_MODE", True)

# Context resolution thresholds.
CONTEXT_REUSE_THRESHOLD = float(os.getenv("CONTEXT_REUSE_THRESHOLD", "0.62"))
CONTEXT_CONFLICT_THRESHOLD = float(os.getenv("CONTEXT_CONFLICT_THRESHOLD", "0.72"))
CONTEXT_CANDIDATE_LIMIT = int(os.getenv("CONTEXT_CANDIDATE_LIMIT", "24"))

# Local NEXT evolution thresholds.
NEXT_RECENT_WINDOW_SECONDS = int(os.getenv("NEXT_RECENT_WINDOW_SECONDS", "43200"))  # 12h
NEXT_MAX_PREDECESSORS = int(os.getenv("NEXT_MAX_PREDECESSORS", "2"))
NEXT_MIN_SCORE = float(os.getenv("NEXT_MIN_SCORE", "0.25"))

# Pattern induction thresholds.
PATTERN_ASSIGN_THRESHOLD = float(os.getenv("PATTERN_ASSIGN_THRESHOLD", "0.64"))
PATTERN_DRIFT_THRESHOLD = float(os.getenv("PATTERN_DRIFT_THRESHOLD", "0.42"))
PATTERN_SPLIT_DRIFT_THRESHOLD = float(os.getenv("PATTERN_SPLIT_DRIFT_THRESHOLD", "0.78"))
PATTERN_MERGE_THRESHOLD = float(os.getenv("PATTERN_MERGE_THRESHOLD", "0.84"))
PATTERN_CANDIDATE_LIMIT = int(os.getenv("PATTERN_CANDIDATE_LIMIT", "24"))

# Reinforcement / decay controls.
REINFORCEMENT_STEP = float(os.getenv("REINFORCEMENT_STEP", "0.06"))
DECAY_STEP = float(os.getenv("DECAY_STEP", "0.04"))
STALE_SECONDS = int(os.getenv("STALE_SECONDS", "2592000"))  # 30 days
ARCHIVE_EVENT_SECONDS = int(os.getenv("ARCHIVE_EVENT_SECONDS", "7776000"))  # 90 days

# Retrieval (evolution-aware) weights.
RETRIEVAL_WEIGHT_EVENT_SIM = float(os.getenv("RETRIEVAL_WEIGHT_EVENT_SIM", "0.33"))
RETRIEVAL_WEIGHT_CONTEXT = float(os.getenv("RETRIEVAL_WEIGHT_CONTEXT", "0.20"))
RETRIEVAL_WEIGHT_PATTERN = float(os.getenv("RETRIEVAL_WEIGHT_PATTERN", "0.22"))
RETRIEVAL_WEIGHT_RECENCY = float(os.getenv("RETRIEVAL_WEIGHT_RECENCY", "0.10"))
RETRIEVAL_WEIGHT_VALIDITY = float(os.getenv("RETRIEVAL_WEIGHT_VALIDITY", "0.10"))
RETRIEVAL_WEIGHT_SUPPORT = float(os.getenv("RETRIEVAL_WEIGHT_SUPPORT", "0.05"))

# Consolidation / forgetting.
ENABLE_AUTO_CONSOLIDATION = env_bool("ENABLE_AUTO_CONSOLIDATION", True)
CONSOLIDATION_MIN_INTERVAL_SECONDS = int(
    os.getenv("CONSOLIDATION_MIN_INTERVAL_SECONDS", "1800")
)
WEAK_EDGE_PRUNE_THRESHOLD = float(os.getenv("WEAK_EDGE_PRUNE_THRESHOLD", "0.18"))
CONSOLIDATION_LOG_PATH = os.getenv(
    "CONSOLIDATION_LOG_PATH", "./outputs/consolidation_log.jsonl"
)

# Offline mode allows testing without external LLM/embedding APIs.
OFFLINE_MODE = env_bool("OFFLINE_MODE", False)

# Visualization knobs.
EXPORT_GRAPH = env_bool("EXPORT_GRAPH", True)
EXPORT_EVERY_EPISODE = env_bool("EXPORT_EVERY_EPISODE", True)
EXPORT_FULL_GRAPH = env_bool("EXPORT_FULL_GRAPH", True)
INCLUDE_EPISODES_IN_GRAPH = env_bool("INCLUDE_EPISODES_IN_GRAPH", False)
GRAPH_OUTPUT_DIR = os.getenv("GRAPH_OUTPUT_DIR", "./outputs")
GRAPH_MAX_EVENTS = int(os.getenv("GRAPH_MAX_EVENTS", "0"))
KUZU_EXPLORER_URL = os.getenv("KUZU_EXPLORER_URL", "")
