# -*- coding: utf-8 -*-
import hashlib
import json
import os
from datetime import datetime
from typing import Any


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


def robust_json_loads(raw, default=None):
    """Robust JSON parser that handles LLM output with extra text.

    Args:
        raw: Raw string that may contain JSON with extra text
        default: Default value if parsing fails completely

    Returns:
        Parsed JSON object or default value
    """
    if raw is None or raw == "":
        return default

    # Remove markdown code blocks
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n", 1)
        if len(lines) > 1:
            text = lines[1]
        if "```" in text:
            text = text.rsplit("```", 1)[0].strip()

    # Try direct parsing first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON object/array from text
    import re

    # Try to find JSON object {...}
    obj_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except json.JSONDecodeError:
            pass

    # Try to find JSON array [...]
    arr_match = re.search(r'\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]', text, re.DOTALL)
    if arr_match:
        try:
            return json.loads(arr_match.group(0))
        except json.JSONDecodeError:
            pass

    # All attempts failed
    return default


def _pick_first(payload: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if value in (None, "", [], {}):
            continue
        return value
    return default


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            text = _to_text(item)
            if text:
                parts.append(text)
        return ", ".join(parts)
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            text = _to_text(v)
            if text:
                parts.append(f"{k}={text}")
        return ", ".join(parts)
    return str(value).strip()


def _parse_timestamp(value: Any) -> int:
    if isinstance(value, (int, float)):
        ts = int(value)
        return ts if ts > 0 else 0

    if not isinstance(value, str):
        return 0

    text = value.strip()
    if not text:
        return 0

    if text.isdigit():
        ts = int(text)
        return ts if ts > 0 else 0

    # Common datetime formats.
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue

    # ISO format, including trailing 'Z'.
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def _normalize_participants(actor_value: Any) -> list[dict[str, str]]:
    participants: list[dict[str, str]] = []

    def add_participant(role_value: Any, seat_value: Any = "") -> None:
        role = _to_text(role_value)
        seat = _to_text(seat_value)
        if role:
            participants.append({"role": role, "seat": seat})

    if isinstance(actor_value, list):
        items = actor_value
    else:
        items = [actor_value]

    for item in items:
        if isinstance(item, dict):
            role = _pick_first(item, ["role", "name", "actor", "id", "label"], "")
            seat = _pick_first(item, ["seat", "position", "loc"], "")
            if role:
                add_participant(role, seat)
            else:
                text = _to_text(item)
                if text:
                    add_participant(text)
        else:
            add_participant(item)

    return participants


def _normalize_location(context_value: Any) -> dict[str, str]:
    geo_context = ""
    digital_context = ""

    if isinstance(context_value, dict):
        geo_context = _to_text(
            _pick_first(
                context_value,
                ["geo_context", "location", "place", "scene", "physical_context"],
                "",
            )
        )
        digital_context = _to_text(
            _pick_first(
                context_value,
                ["digital_context", "app", "system", "platform", "device"],
                "",
            )
        )

        if not geo_context and not digital_context:
            geo_context = _to_text(context_value)
    elif isinstance(context_value, list):
        geo_context = ", ".join(_to_text(item) for item in context_value if _to_text(item))
    else:
        geo_context = _to_text(context_value)

    return {
        "geo_context": geo_context,
        "digital_context": digital_context,
    }


def _guess_bucket_from_text(text: str) -> str:
    import re

    normalized = text.lower()
    if (
        "morning" in normalized
        or "上午" in normalized
        or "清晨" in normalized
        or "早上" in normalized
        or re.search(r"\b\d{1,2}\s*am\b", normalized)
        or re.search(r"\bam\b", normalized)
    ):
        return "morning"
    if (
        "afternoon" in normalized
        or "下午" in normalized
        or re.search(r"\b\d{1,2}\s*pm\b", normalized)
        or re.search(r"\bpm\b", normalized)
    ):
        return "afternoon"
    if any(token in normalized for token in ["evening", "傍晚", "晚上"]):
        return "evening"
    if any(token in normalized for token in ["night", "深夜", "夜里"]):
        return "night"
    return ""


def _normalize_time_range(time_value: Any) -> dict[str, Any]:
    start = 0
    end = 0
    display_time_bucket = ""

    if isinstance(time_value, dict):
        start = _parse_timestamp(_pick_first(time_value, ["start", "begin", "timestamp"], 0))
        end = _parse_timestamp(_pick_first(time_value, ["end", "finish"], 0))
        display_time_bucket = _to_text(
            _pick_first(time_value, ["display_time_bucket", "bucket", "time_bucket"], "")
        )
        if not display_time_bucket:
            display_time_bucket = _guess_bucket_from_text(_to_text(time_value))
    elif isinstance(time_value, (int, float)):
        start = _parse_timestamp(time_value)
    elif isinstance(time_value, str):
        start = _parse_timestamp(time_value)
        display_time_bucket = _guess_bucket_from_text(time_value)

    if start > 0 and end == 0:
        end = start

    return {
        "start": start,
        "end": end,
        "display_time_bucket": display_time_bucket,
    }


def _normalize_consistency(value: Any) -> str:
    if isinstance(value, (int, float)):
        score = float(value)
        if score >= 0.8:
            return "consistent"
        if score <= 0.2:
            return "inconsistent"
        return "uncertain"

    text = _to_text(value).lower()
    if text in {"consistent", "inconsistent", "uncertain"}:
        return text
    if text in {"true", "yes"}:
        return "consistent"
    if text in {"false", "no"}:
        return "inconsistent"
    return "uncertain"


def _normalize_evidence(evidence_value: Any) -> list[dict[str, Any]]:
    if not evidence_value:
        return []

    normalized: list[dict[str, Any]] = []
    if not isinstance(evidence_value, list):
        evidence_value = [evidence_value]

    for item in evidence_value:
        if isinstance(item, dict):
            confidence_raw = item.get("confidence", 1.0)
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = 1.0
            normalized.append(
                {
                    "source": _to_text(item.get("source", "")),
                    "snippet": _to_text(item.get("snippet", "")),
                    "timestamp": _parse_timestamp(item.get("timestamp", 0)),
                    "confidence": confidence,
                }
            )
        else:
            snippet = _to_text(item)
            if snippet:
                normalized.append(
                    {
                        "source": "llm_extraction",
                        "snippet": snippet,
                        "timestamp": 0,
                        "confidence": 1.0,
                    }
                )
    return normalized


def normalize_event_payload(payload: Any, episode_text: str = "") -> dict[str, Any]:
    """Normalize LLM event output to the system's canonical event schema.

    Supports both:
    - Legacy schema fields: summary/participants/time_range/location/action/causality
    - New tuple schema: Actor/Action/Context/Time/Outcome
    """
    if not isinstance(payload, dict):
        payload = {}

    event_payload = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    if not isinstance(event_payload, dict):
        event_payload = {}

    actor_value = _pick_first(
        event_payload,
        ["actor", "Actor", "actors", "Actors", "participants", "participant"],
        [],
    )
    action_value = _pick_first(
        event_payload,
        ["action", "Action", "what_happened", "WhatHappened", "event_action"],
        "",
    )
    context_value = _pick_first(
        event_payload,
        ["context", "Context", "location", "scene", "situation"],
        {},
    )
    time_value = _pick_first(
        event_payload,
        ["time", "Time", "time_range", "timestamp", "happened_at"],
        {},
    )
    outcome_value = _pick_first(
        event_payload,
        ["outcome", "Outcome", "result", "impact", "effect"],
        "",
    )

    participants = _normalize_participants(actor_value)
    location = _normalize_location(context_value)
    time_range = _normalize_time_range(time_value)
    action = _to_text(action_value)
    outcome = _to_text(outcome_value)
    summary = _to_text(
        _pick_first(event_payload, ["summary", "Summary", "event_summary"], "")
    )

    if not summary:
        actor_text = "、".join(item["role"] for item in participants[:3] if item.get("role"))
        context_text = " / ".join(
            text for text in [location.get("geo_context", ""), location.get("digital_context", "")]
            if text
        )
        time_text = ""
        if time_range.get("start", 0) > 0:
            time_text = datetime.fromtimestamp(time_range["start"]).strftime("%Y-%m-%d %H:%M")
        elif time_range.get("display_time_bucket", ""):
            time_text = time_range["display_time_bucket"]

        parts = []
        if actor_text and action:
            parts.append(f"{actor_text}{action}")
        elif action:
            parts.append(action)
        if context_text:
            parts.append(f"情景:{context_text}")
        if time_text:
            parts.append(f"时间:{time_text}")
        if outcome:
            parts.append(f"结果:{outcome}")

        summary = "；".join(parts).strip("；")

    if not summary and episode_text:
        summary = episode_text[:120]

    causality = _to_text(_pick_first(event_payload, ["causality", "cause", "reason"], ""))
    if not causality:
        causality = outcome

    return {
        "summary": summary,
        "participants": participants,
        "time_range": time_range,
        "location": location,
        "action": action,
        "causality": causality,
        "evidence": _normalize_evidence(event_payload.get("evidence", [])),
        "consistency": _normalize_consistency(event_payload.get("consistency", "uncertain")),
        # Keep the new formal tuple fields for traceability/debugging.
        "actor": participants,
        "context": location,
        "time": time_range,
        "outcome": outcome,
    }


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
