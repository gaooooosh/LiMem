# -*- coding: utf-8 -*-
import os

from dotenv import load_dotenv

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
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.85"))

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

# Visualization knobs.
EXPORT_GRAPH = env_bool("EXPORT_GRAPH", True)
EXPORT_EVERY_EPISODE = env_bool("EXPORT_EVERY_EPISODE", True)
EXPORT_FULL_GRAPH = env_bool("EXPORT_FULL_GRAPH", True)
INCLUDE_EPISODES_IN_GRAPH = env_bool("INCLUDE_EPISODES_IN_GRAPH", False)
GRAPH_OUTPUT_DIR = os.getenv("GRAPH_OUTPUT_DIR", "./outputs")
GRAPH_MAX_EVENTS = int(os.getenv("GRAPH_MAX_EVENTS", "0"))
KUZU_EXPLORER_URL = os.getenv("KUZU_EXPLORER_URL", "")

