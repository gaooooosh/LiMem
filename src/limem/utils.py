# -*- coding: utf-8 -*-
import hashlib
import json
import os
import re
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


def _dedupe_participants(participants: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
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
        unique.append({"role": role, "seat": seat})
    return unique


def _infer_participants_from_text(text: str) -> list[dict[str, str]]:
    if not text:
        return []

    hints = [
        ("用户", "用户"),
        ("我", "用户"),
        ("我们", "用户"),
        ("车机", "系统"),
        ("系统", "系统"),
        ("助手", "agent"),
        ("agent", "agent"),
        ("环境", "环境"),
        ("天气", "环境"),
        ("路况", "环境"),
    ]
    participants: list[dict[str, str]] = []
    for needle, role in hints:
        if needle in text:
            participants.append({"role": role, "seat": ""})
    return _dedupe_participants(participants)


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
    text_hint = ""

    if isinstance(time_value, dict):
        start = _parse_timestamp(_pick_first(time_value, ["start", "begin", "timestamp"], 0))
        end = _parse_timestamp(_pick_first(time_value, ["end", "finish"], 0))
        text_hint = _to_text(_pick_first(time_value, ["text", "label", "description"], ""))
        display_time_bucket = _to_text(
            _pick_first(time_value, ["display_time_bucket", "bucket", "time_bucket"], "")
        )
        if not display_time_bucket:
            display_time_bucket = _guess_bucket_from_text(text_hint or _to_text(time_value))
    elif isinstance(time_value, (int, float)):
        start = _parse_timestamp(time_value)
    elif isinstance(time_value, str):
        start = _parse_timestamp(time_value)
        text_hint = time_value
        display_time_bucket = _guess_bucket_from_text(time_value)

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


def _looks_like_dynamic_change(summary: str, action: str) -> bool:
    text = f"{summary} {action}".strip().lower()
    if not text:
        return False

    dynamic_hints = [
        "说", "问", "播放", "导航", "打开", "关闭", "切换", "设置", "开始", "停止",
        "暂停", "恢复", "提醒", "发现", "检测", "选择", "决定", "请求", "回复", "开始导航",
    ]
    return any(token in text for token in dynamic_hints)


def _build_event_summary(
    participants: list[dict[str, str]],
    action: str,
    time_range: dict[str, Any],
    result: str,
) -> str:
    actor_text = "、".join(item["role"] for item in participants[:3] if item.get("role"))
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
    if time_text:
        parts.append(f"时间:{time_text}")
    if result:
        parts.append(f"结果:{result}")

    return "；".join(parts).strip("；")


def _looks_like_episode_text(summary: str, episode_text: str) -> bool:
    summary_text = _to_text(summary)
    episode = _to_text(episode_text)
    if not summary_text or not episode:
        return False

    normalized_summary = summary_text.replace(" ", "")
    normalized_episode = episode.replace(" ", "")
    if normalized_summary == normalized_episode:
        return True

    prefix_markers = ("用户说:", "用户说：", "车机回答:", "车机回答：", "[")
    if summary_text.startswith(prefix_markers):
        return True

    overlap = len(set(normalized_summary) & set(normalized_episode))
    ratio = overlap / max(1, len(set(normalized_summary)))
    return len(summary_text) >= 20 and ratio >= 0.85


_DATETIME_INLINE_PATTERN = re.compile(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?\b")
_NOISY_RECORD_MARKERS = (
    '"start_time"',
    '"end_time"',
    '"payload"',
    '"source"',
    '"screen"',
    '"app"',
    "{",
    "}",
)


def _sanitize_event_field_text(value: Any) -> str:
    text = _to_text(value)
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text)
    text = _DATETIME_INLINE_PATTERN.sub("", text)
    text = re.sub(r"(还有[:：]).*$", "", text)
    text = re.sub(r"(时间[:：]).*$", "", text)
    text = text.strip(" ，,。；;:：|")
    return text


def _looks_like_raw_record(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    marker_hits = sum(1 for marker in _NOISY_RECORD_MARKERS if marker in normalized)
    return marker_hits >= 3


def _looks_like_telemetry_snapshot(text: str, participants: list[dict[str, str]]) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False

    telemetry_markers = (
        "环境感知",
        "来源: 环境感知",
        "cabin_env",
        "weather",
        "spatial",
        "noise_db",
        "temp_in",
        "temp_out",
    )
    marker_hits = sum(1 for marker in telemetry_markers if marker in normalized)
    if marker_hits < 2:
        return False

    # Telemetry snapshots typically only involve environment actors and
    # do not carry an actionable user/system operation.
    roles = {str(item.get("role", "") or "").strip() for item in participants if isinstance(item, dict)}
    if not roles:
        return True
    return roles.issubset({"环境", "环境感知"})


_ENTITY_GENERIC_TERMS = {
    "内容", "歌曲", "歌", "音乐", "视频", "动画片", "电影", "纪录片", "节目", "专辑",
    "路线", "导航", "地址", "位置", "地方", "东西", "事情", "信息", "答案", "问题",
    "用户说", "系统说", "车机回答",
}
_ENTITY_TIME_TERMS = {
    "上次", "上一次", "下次", "通常", "经常", "刚刚", "刚才", "今天", "昨天", "明天",
    "上午", "下午", "晚上", "夜里", "早上", "随后", "之后", "以前", "现在",
}
_ENTITY_ACTION_TERMS = {
    "播放", "放", "听", "看", "导航", "去", "到", "打开", "开启", "关闭", "切换",
    "设置", "查看", "查询", "收听", "提醒", "告诉",
}
_ENTITY_ACTION_PHRASE_HINTS = {
    "开会", "勿扰", "开勿扰", "导航去", "导航到", "播放", "暂停", "打开", "关闭",
    "切换", "设置", "查看", "查询", "收听", "调整", "提醒",
}
_ENTITY_STOP_WORDS = {
    "的", "了", "吗", "呢", "啊", "呀", "吧", "这", "那个", "这个", "一下", "一下子",
    "用户", "我", "我们", "你", "你们", "他", "她", "它",
}
_ENTITY_PREFIXES = (
    "用户说", "车机回答", "系统提示", "系统说", "帮我", "给我", "给孩子", "给", "帮", "请", "我想", "我要",
    "我想要", "我需要", "想听", "想看", "想去", "播放", "放", "听", "看", "导航到", "导航去",
    "导航", "去", "到", "打开", "开启", "关闭", "切换到", "切换", "设置", "查看", "查询",
)
_ENTITY_TRAILING_SUFFIXES = (
    "的歌", "的歌曲", "的音乐", "的专辑", "动画片", "电影", "纪录片", "视频",
)


def _normalize_entity_name(value: Any) -> str:
    text = _to_text(value)
    if not text:
        return ""

    text = re.sub(r"^[\"'“”‘’《》【】\[\]()（）]+|[\"'“”‘’《》【】\[\]()（）]+$", "", text)
    text = re.sub(r"^(内容|目的地|地点|位置)[:：]", "", text).strip()

    changed = True
    while changed and text:
        changed = False
        for prefix in _ENTITY_PREFIXES:
            if text.startswith(prefix) and len(text) > len(prefix) + 1:
                text = text[len(prefix):].strip(" ：:，,的")
                changed = True

    for suffix in _ENTITY_TRAILING_SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)].strip(" 的")
            break

    text = text.strip(" ，,。：:;；!?？！")
    if not text or text in _ENTITY_STOP_WORDS:
        return ""
    if text in _ENTITY_GENERIC_TERMS or text in _ENTITY_TIME_TERMS or text in _ENTITY_ACTION_TERMS:
        return ""
    if text in _ENTITY_ACTION_PHRASE_HINTS:
        return ""
    if text.isdigit():
        return ""
    if len(text) <= 1:
        return ""

    if any(term in text for term in _ENTITY_ACTION_TERMS) and any(term in text for term in _ENTITY_GENERIC_TERMS):
        return ""
    if any(term in text for term in _ENTITY_TIME_TERMS) and len(text) <= 4:
        return ""
    if any(text.startswith(term) for term in _ENTITY_ACTION_PHRASE_HINTS):
        return ""

    return text


def normalize_entity_candidates(payload: Any, source_text: str = "") -> list[str]:
    if isinstance(payload, dict):
        raw_entities = payload.get("entities", payload.get("entity", []))
    else:
        raw_entities = payload

    if not isinstance(raw_entities, list):
        raw_entities = [raw_entities]

    normalized: list[str] = []
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
        normalized.append(name)

    return normalized


def normalize_event_payload(payload: Any, episode_text: str = "") -> dict[str, Any]:
    """Normalize LLM event output to the system's canonical event schema.

    Supports both:
    - Legacy schema fields: summary/participants/time_range/action/causality
    - Tuple-like schema: Actor/Action/Time/Outcome
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
    if not participants:
        participants = _infer_participants_from_text(f"{episode_text} {_to_text(event_payload)}")
    participants = _dedupe_participants(participants)
    time_range = _normalize_time_range(time_value)
    action = _sanitize_event_field_text(action_value)
    result = _sanitize_event_field_text(outcome_value)
    summary = _sanitize_event_field_text(
        _pick_first(event_payload, ["summary", "Summary", "event_summary"], "")
    )

    if summary and (_looks_like_episode_text(summary, episode_text) or _looks_like_raw_record(summary)):
        summary = ""
    if action and _looks_like_raw_record(action):
        action = ""
    if result and _looks_like_raw_record(result):
        result = ""

    if not summary:
        summary = _build_event_summary(participants, action, time_range, result)

    causality = _to_text(_pick_first(event_payload, ["causality", "cause", "reason"], ""))
    if not causality:
        causality = result

    if not action:
        action = result or summary

    telemetry_hint_text = " ".join(
        [
            _to_text(summary),
            _to_text(action),
            _to_text(causality),
            _to_text(episode_text),
            _to_text(event_payload),
        ]
    )
    if _looks_like_telemetry_snapshot(telemetry_hint_text, participants):
        summary = ""
        action = ""
        causality = ""

    if not _looks_like_dynamic_change(summary, action):
        action = ""
        summary = ""

    return {
        "summary": summary,
        "participants": participants,
        "time_range": time_range,
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
