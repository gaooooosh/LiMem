# -*- coding: utf-8 -*-
import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any


def hash_summary(summary):
    return hashlib.sha256(summary.encode("utf-8")).hexdigest()


def parse_time_to_unix(ts):
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
    if raw is None or raw == "":
        return default

    text = str(raw).strip()
    if text.startswith("```"):
        lines = text.split("\n", 1)
        if len(lines) > 1:
            text = lines[1]
        if "```" in text:
            text = text.rsplit("```", 1)[0].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    obj_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if obj_match:
        try:
            return json.loads(obj_match.group(0))
        except json.JSONDecodeError:
            pass

    arr_match = re.search(r"\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]", text, re.DOTALL)
    if arr_match:
        try:
            return json.loads(arr_match.group(0))
        except json.JSONDecodeError:
            pass

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
        parts = [_to_text(item) for item in value]
        return ", ".join(part for part in parts if part)
    if isinstance(value, dict):
        parts = [_to_text(item) for item in value.values()]
        return ", ".join(part for part in parts if part)
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

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue

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

    items = actor_value if isinstance(actor_value, list) else [actor_value]
    for item in items:
        if isinstance(item, dict):
            role = _pick_first(item, ["role", "name", "actor", "id", "label"], "")
            seat = _pick_first(item, ["seat", "position", "loc"], "")
            if role:
                add_participant(role, seat)
            else:
                add_participant(item)
        else:
            add_participant(item)
    return _dedupe_participants(participants)


def _dedupe_participants(participants: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for item in participants:
        if not isinstance(item, dict):
            continue
        role = _to_text(item.get("role", ""))
        seat = _to_text(item.get("seat", ""))
        if not role:
            continue
        key = (role, seat)
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"role": role, "seat": seat})
    return deduped


def _guess_bucket_from_text(text: str) -> str:
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
    text_hint = ""

    if isinstance(time_value, dict):
        start = _parse_timestamp(_pick_first(time_value, ["start", "begin", "timestamp"], 0))
        end = _parse_timestamp(_pick_first(time_value, ["end", "finish"], 0))
        text_hint = _to_text(_pick_first(time_value, ["text", "label", "description"], ""))
        display_time_bucket = _to_text(
            _pick_first(time_value, ["display_time_bucket", "bucket", "time_bucket"], "")
        )
    elif isinstance(time_value, (int, float, str)):
        start = _parse_timestamp(time_value)
        text_hint = _to_text(time_value)

    if not display_time_bucket and text_hint:
        display_time_bucket = _guess_bucket_from_text(text_hint)
    if start > 0 and end == 0:
        end = start

    return {
        "start": start,
        "end": end,
        "display_time_bucket": display_time_bucket,
    }


def _normalize_evidence(evidence_value: Any) -> list[dict[str, Any]]:
    if not evidence_value:
        return []
    items = evidence_value if isinstance(evidence_value, list) else [evidence_value]
    result: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            try:
                confidence = float(item.get("confidence", 1.0))
            except (TypeError, ValueError):
                confidence = 1.0
            result.append(
                {
                    "source": _to_text(item.get("source", "")),
                    "snippet": _to_text(item.get("snippet", "")),
                    "timestamp": _parse_timestamp(item.get("timestamp", 0)),
                    "confidence": confidence,
                }
            )
            continue
        snippet = _to_text(item)
        if snippet:
            result.append(
                {
                    "source": "llm_extraction",
                    "snippet": snippet,
                    "timestamp": 0,
                    "confidence": 1.0,
                }
            )
    return result


def _normalize_entity_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", _to_text(value)).strip(" ，,。；;：:\"'()[]{}")
    if len(text) <= 1:
        return ""
    return text


def normalize_entity_candidates(
    payload: Any,
    source_text: str = "",
) -> list[str]:
    del source_text
    if isinstance(payload, dict):
        raw_entities = payload.get("entities", payload.get("entity", []))
    else:
        raw_entities = payload

    if not isinstance(raw_entities, list):
        raw_entities = [raw_entities]

    result: list[str] = []
    seen: set[str] = set()
    for item in raw_entities:
        if isinstance(item, dict):
            text = _pick_first(item, ["name", "entity", "id", "label"], "")
        else:
            text = item
        name = _normalize_entity_name(text)
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def normalize_event_payload(
    payload: Any,
    episode_text: str = "",
    *,
    participant_hints: tuple[tuple[str, str], ...] | None = None,
) -> dict[str, Any]:
    del episode_text, participant_hints
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
    action = _to_text(
        _pick_first(
            event_payload,
            ["action", "Action", "what_happened", "WhatHappened", "event_action"],
            "",
        )
    )
    time_value = _pick_first(
        event_payload,
        ["time", "Time", "time_range", "timestamp", "happened_at"],
        {},
    )
    outcome = _to_text(
        _pick_first(
            event_payload,
            ["outcome", "Outcome", "result", "impact", "effect"],
            "",
        )
    )
    summary = _to_text(_pick_first(event_payload, ["summary", "Summary", "event_summary"], ""))
    causality = _to_text(_pick_first(event_payload, ["causality", "cause", "reason"], ""))

    if not summary:
        summary = action or causality or outcome
    if not action:
        action = summary
    if not causality:
        causality = outcome

    return {
        "summary": summary,
        "participants": _normalize_participants(actor_value),
        "time_range": _normalize_time_range(time_value),
        "action": action,
        "causality": causality,
        "evidence": _normalize_evidence(event_payload.get("evidence", [])),
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
    current_dir = os.path.dirname(os.path.abspath(__file__))
    prompts_dir = os.path.join(os.path.dirname(current_dir), "prompts")
    prompt_path = os.path.join(prompts_dir, prompt_name)
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"⚠️ Prompt file not found: {prompt_path}")
        return ""
    except Exception as exc:
        print(f"⚠️ Error reading prompt file {prompt_path}: {exc}")
        return ""
