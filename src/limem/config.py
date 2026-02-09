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
DECAY_RATE = float(os.getenv("DECAY_RATE", "0.01"))
EPISODE_TTL = int(os.getenv("EPISODE_TTL", "3600"))
DECAY_RATE_P2 = float(os.getenv("DECAY_RATE_P2", str(DECAY_RATE)))
DECAY_RATE_P3 = float(os.getenv("DECAY_RATE_P3", str(DECAY_RATE * 2)))
PRUNE_C_VALID_THRESHOLD = int(os.getenv("PRUNE_C_VALID_THRESHOLD", "100"))
PRUNE_EVIDENCE_TOP_K = int(os.getenv("PRUNE_EVIDENCE_TOP_K", "5"))
DEFAULT_USER_ID = os.getenv("DEFAULT_USER_ID", "default_user")

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

# Proactive engine knobs.
PROACTIVE_WEIGHT_THRESHOLD = float(os.getenv("PROACTIVE_WEIGHT_THRESHOLD", "0.2"))
PROACTIVE_TIME_GAP_SEC = int(os.getenv("PROACTIVE_TIME_GAP_SEC", "1800"))
