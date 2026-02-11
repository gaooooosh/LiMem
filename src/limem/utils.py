# -*- coding: utf-8 -*-
import hashlib
import json
import os
from datetime import datetime


def hash_summary(summary):
    # Event ID is a deterministic hash of its summary (as specified).
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()


def parse_time_to_unix(ts):
    # Simple parser for example.json timestamps (YYYY-MM-DD HH:MM:SS).
    return int(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").timestamp())


def safe_json_dumps(payload):
    return json.dumps(payload, ensure_ascii=True)


def safe_json_loads(raw, default):
    if raw is None or raw == "":
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def time_bucket_from_ts(ts):
    if ts <= 0:
        return ""
    hour = datetime.fromtimestamp(ts).hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 23:
        return "evening"
    return "night"


def load_prompt(prompt_name):
    """Load prompt content from prompts directory.

    Args:
        prompt_name: Name of the prompt file (e.g., 'extract_event_system.txt')

    Returns:
        Prompt content as string, or empty string if file not found.
    """
    # Get the directory where this utils.py file is located
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Build path to prompts directory (sibling to limem directory)
    prompts_dir = os.path.join(os.path.dirname(current_dir), "prompts")
    prompt_path = os.path.join(prompts_dir, prompt_name)

    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"⚠️ Prompt file not found: {prompt_path}")
        return ""
    except Exception as e:
        print(f"⚠️ Error reading prompt file {prompt_path}: {e}")
        return ""
