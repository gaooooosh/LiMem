# -*- coding: utf-8 -*-
import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any

_DEFAULT_PARTICIPANT_HINTS = (
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
)
_DEFAULT_DYNAMIC_CHANGE_HINTS = (
    "说", "问", "播放", "导航", "打开", "开启", "关闭", "切换", "设置", "开始", "停止",
    "暂停", "恢复", "提醒", "发现", "检测", "选择", "决定", "请求", "回复", "开始导航",
    "调高", "调低", "拉满", "调整", "调节", "启动", "升高", "降低", "增大", "减小",
    "增加", "减少", "升温", "降温", "调到", "调为", "切到", "切为", "进入", "退出",
    "激活", "取消", "确认", "执行", "解锁", "锁定",
)
_DEFAULT_EVENT_SUMMARY_PARTICIPANT_SEPARATOR = "、"
_DEFAULT_EVENT_SUMMARY_SEPARATOR = "；"
_DEFAULT_EVENT_SUMMARY_TIME_PREFIX = "时间:"
_DEFAULT_EVENT_SUMMARY_RESULT_PREFIX = "结果:"
_DEFAULT_STRUCTURED_DIALOGUE_MARKERS = (
    "用户说:", "用户说：", "车机回答:", "车机回答：", "->", "|",
    "[", "]", "{", "}", "来源:", "屏幕:", "应用:", "payload", "source",
)
_DEFAULT_TELEMETRY_MARKERS = (
    "环境感知",
    "来源: 环境感知",
    "cabin_env",
    "weather",
    "spatial",
    "noise_db",
    "temp_in",
    "temp_out",
)
_DEFAULT_TELEMETRY_ROLES = frozenset({"环境", "环境感知"})
_DEFAULT_PASSIVE_SCREEN_PREFIX = "[屏幕操作数据]"
_DEFAULT_PASSIVE_SCREEN_METADATA_MARKERS = ("屏幕:", "应用:")
_DEFAULT_PASSIVE_SCREEN_DYNAMIC_HINTS = (
    "用户说", "车机回答", "播放", "打开", "开启", "关闭", "切换", "点击", "选择",
    "搜索", "输入", "导航", "发起", "开始", "停止", "暂停", "恢复",
    "调到", "调整", "启动", "提醒",
)
_SKIP_DYNAMIC_CHECK = object()
_DEFAULT_ENTITY_GENERIC_TERMS = {
    "内容", "歌曲", "歌", "音乐", "视频", "动画片", "电影", "纪录片", "节目", "专辑",
    "路线", "导航", "地址", "位置", "地方", "东西", "事情", "信息", "答案", "问题",
    "用户说", "系统说", "车机回答",
}
_DEFAULT_ENTITY_TIME_TERMS = {
    "上次", "上一次", "下次", "通常", "经常", "刚刚", "刚才", "今天", "昨天", "明天",
    "上午", "下午", "晚上", "夜里", "早上", "随后", "之后", "以前", "现在",
}
_DEFAULT_ENTITY_ACTION_TERMS = {
    "播放", "放", "听", "看", "导航", "去", "到", "打开", "开启", "关闭", "切换",
    "设置", "查看", "查询", "收听", "提醒", "告诉",
}
_DEFAULT_ENTITY_ACTION_PHRASE_HINTS = {
    "开会", "勿扰", "开勿扰", "导航去", "导航到", "播放", "暂停", "打开", "关闭",
    "切换", "设置", "查看", "查询", "收听", "调整", "提醒",
}
_DEFAULT_ENTITY_STOP_WORDS = {
    "的", "了", "吗", "呢", "啊", "呀", "吧", "这", "那个", "这个", "一下", "一下子",
    "用户", "我", "我们", "你", "你们", "他", "她", "它",
}
_DEFAULT_ENTITY_PREFIXES = (
    "用户说", "车机回答", "系统提示", "系统说", "帮我", "给我", "给孩子", "给", "帮", "请", "我想", "我要",
    "我想要", "我需要", "想听", "想看", "想去", "播放", "放", "听", "看", "导航到", "导航去",
    "导航", "去", "到", "打开", "开启", "关闭", "切换到", "切换", "设置", "查看", "查询",
)
_DEFAULT_ENTITY_TRAILING_SUFFIXES = (
    "的歌", "的歌曲", "的音乐", "的专辑", "动画片", "电影", "纪录片", "视频",
)


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


def _infer_participants_from_text(
    text: str,
    participant_hints: tuple[tuple[str, str], ...] | None = None,
) -> list[dict[str, str]]:
    if not text:
        return []

    hints = _DEFAULT_PARTICIPANT_HINTS if participant_hints is None else participant_hints
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


def _looks_like_dynamic_change(
    summary: str,
    action: str,
    dynamic_hints: tuple[str, ...] | None = None,
) -> bool:
    text = f"{summary} {action}".strip().lower()
    if not text:
        return False

    hints = _DEFAULT_DYNAMIC_CHANGE_HINTS if dynamic_hints is None else dynamic_hints
    return any(token in text for token in hints)


def _build_event_summary(
    participants: list[dict[str, str]],
    action: str,
    time_range: dict[str, Any],
    result: str,
    participant_separator: str | None = None,
    field_separator: str | None = None,
    time_prefix: str | None = None,
    result_prefix: str | None = None,
) -> str:
    resolved_participant_separator = (
        _DEFAULT_EVENT_SUMMARY_PARTICIPANT_SEPARATOR
        if participant_separator is None
        else participant_separator
    )
    resolved_field_separator = _DEFAULT_EVENT_SUMMARY_SEPARATOR if field_separator is None else field_separator
    resolved_time_prefix = _DEFAULT_EVENT_SUMMARY_TIME_PREFIX if time_prefix is None else time_prefix
    resolved_result_prefix = _DEFAULT_EVENT_SUMMARY_RESULT_PREFIX if result_prefix is None else result_prefix

    actor_text = resolved_participant_separator.join(item["role"] for item in participants[:3] if item.get("role"))
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
        parts.append(f"{resolved_time_prefix}{time_text}")
    if result:
        parts.append(f"{resolved_result_prefix}{result}")

    return resolved_field_separator.join(parts)


def _looks_like_episode_text(
    summary: str,
    episode_text: str,
    structured_dialogue_markers: tuple[str, ...] | None = None,
) -> bool:
    summary_text = _to_text(summary)
    episode = _to_text(episode_text)
    if not summary_text or not episode:
        return False

    normalized_summary = summary_text.replace(" ", "")
    normalized_episode = episode.replace(" ", "")

    stripped_episode = _strip_leading_temporal_prefix(episode)
    normalized_stripped_episode = stripped_episode.replace(" ", "")
    if (
        normalized_summary == normalized_stripped_episode
        and not _looks_like_structured_dialogue_or_record(episode, markers=structured_dialogue_markers)
    ):
        return False

    if normalized_summary == normalized_episode:
        return True

    prefix_markers = ("用户说:", "用户说：", "车机回答:", "车机回答：", "[")
    if summary_text.startswith(prefix_markers):
        return True

    overlap = len(set(normalized_summary) & set(normalized_episode))
    ratio = overlap / max(1, len(set(normalized_summary)))
    length_ratio = len(normalized_summary) / max(1, len(normalized_episode))
    if (
        len(summary_text) >= 20
        and ratio >= 0.92
        and length_ratio >= 0.88
        and _looks_like_structured_dialogue_or_record(episode, markers=structured_dialogue_markers)
    ):
        return True
    return False


def _looks_like_structured_dialogue_or_record(
    text: str,
    markers: tuple[str, ...] | None = None,
) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    resolved_markers = _DEFAULT_STRUCTURED_DIALOGUE_MARKERS if markers is None else markers
    return any(marker in normalized for marker in resolved_markers)


_LEADING_TEMPORAL_PREFIX_PATTERN = re.compile(
    r"^\s*(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s*)?"
    r"(?:(?:凌晨|清晨|早上|上午|中午|下午|傍晚|晚上|夜里|深夜)"
    r"(?:\d+(?:点半|点左右|点\d+分|点)?)?"
    r"(?:左右)?\s*[，,]?\s*)+"
)


def _strip_leading_temporal_prefix(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    stripped = _LEADING_TEMPORAL_PREFIX_PATTERN.sub("", normalized, count=1)
    return stripped.strip(" ，,")


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


def _looks_like_telemetry_snapshot(
    text: str,
    participants: list[dict[str, str]],
    telemetry_markers: tuple[str, ...] | None = None,
    telemetry_roles: set[str] | frozenset[str] | None = None,
) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False

    resolved_markers = _DEFAULT_TELEMETRY_MARKERS if telemetry_markers is None else telemetry_markers
    allowed_roles = _DEFAULT_TELEMETRY_ROLES if telemetry_roles is None else set(telemetry_roles)
    marker_hits = sum(1 for marker in resolved_markers if marker in normalized)
    if marker_hits < 2:
        return False

    # Telemetry snapshots typically only involve environment actors and
    # do not carry an actionable user/system operation.
    roles = {str(item.get("role", "") or "").strip() for item in participants if isinstance(item, dict)}
    if not roles:
        return True
    return roles.issubset(allowed_roles)


def _looks_like_passive_screen_app_metadata(
    episode_text: str,
    prefix: str | None = None,
    metadata_markers: tuple[str, ...] | None = None,
    dynamic_hints: tuple[str, ...] | None = None,
) -> bool:
    text = str(episode_text or "").strip()
    resolved_prefix = _DEFAULT_PASSIVE_SCREEN_PREFIX if prefix is None else prefix
    if not resolved_prefix:
        return False
    resolved_markers = (
        _DEFAULT_PASSIVE_SCREEN_METADATA_MARKERS
        if metadata_markers is None
        else metadata_markers
    )
    resolved_dynamic_hints = (
        _DEFAULT_PASSIVE_SCREEN_DYNAMIC_HINTS
        if dynamic_hints is None
        else dynamic_hints
    )
    if not text.startswith(resolved_prefix):
        return False
    if not any(marker in text for marker in resolved_markers):
        return False

    # Metadata-only screen snapshots should not become memory events.
    return not any(token in text for token in resolved_dynamic_hints)


def _normalize_entity_name(
    value: Any,
    generic_terms: set[str] | None = None,
    time_terms: set[str] | None = None,
    action_terms: set[str] | None = None,
    action_phrase_hints: set[str] | None = None,
    stop_words: set[str] | None = None,
    prefixes: tuple[str, ...] | None = None,
    trailing_suffixes: tuple[str, ...] | None = None,
) -> str:
    text = _to_text(value)
    if not text:
        return ""

    resolved_generic_terms = _DEFAULT_ENTITY_GENERIC_TERMS if generic_terms is None else generic_terms
    resolved_time_terms = _DEFAULT_ENTITY_TIME_TERMS if time_terms is None else time_terms
    resolved_action_terms = _DEFAULT_ENTITY_ACTION_TERMS if action_terms is None else action_terms
    resolved_action_phrase_hints = (
        _DEFAULT_ENTITY_ACTION_PHRASE_HINTS
        if action_phrase_hints is None
        else action_phrase_hints
    )
    resolved_stop_words = _DEFAULT_ENTITY_STOP_WORDS if stop_words is None else stop_words
    resolved_prefixes = _DEFAULT_ENTITY_PREFIXES if prefixes is None else prefixes
    resolved_trailing_suffixes = (
        _DEFAULT_ENTITY_TRAILING_SUFFIXES
        if trailing_suffixes is None
        else trailing_suffixes
    )

    text = re.sub(r"^[\"'“”‘’《》【】\[\]()（）]+|[\"'“”‘’《》【】\[\]()（）]+$", "", text)
    text = re.sub(r"^(内容|目的地|地点|位置)[:：]", "", text).strip()

    changed = True
    while changed and text:
        changed = False
        for prefix in resolved_prefixes:
            if text.startswith(prefix) and len(text) > len(prefix) + 1:
                text = text[len(prefix):].strip(" ：:，,的")
                changed = True

    for suffix in resolved_trailing_suffixes:
        if text.endswith(suffix) and len(text) > len(suffix) + 1:
            text = text[: -len(suffix)].strip(" 的")
            break

    text = text.strip(" ，,。：:;；!?？！")
    if not text or text in resolved_stop_words:
        return ""
    if text in resolved_generic_terms or text in resolved_time_terms or text in resolved_action_terms:
        return ""
    if text in resolved_action_phrase_hints:
        return ""
    if text.isdigit():
        return ""
    if len(text) <= 1:
        return ""

    if any(term in text for term in resolved_action_terms) and any(term in text for term in resolved_generic_terms):
        return ""
    if any(term in text for term in resolved_time_terms) and len(text) <= 4:
        return ""
    if any(text.startswith(term) for term in resolved_action_phrase_hints):
        return ""

    return text


def normalize_entity_candidates(
    payload: Any,
    source_text: str = "",
    *,
    entity_generic_terms: set[str] | None = None,
    entity_time_terms: set[str] | None = None,
    entity_action_terms: set[str] | None = None,
    entity_action_phrase_hints: set[str] | None = None,
    entity_stop_words: set[str] | None = None,
    entity_prefixes: tuple[str, ...] | None = None,
    entity_trailing_suffixes: tuple[str, ...] | None = None,
) -> list[str]:
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
        name = _normalize_entity_name(
            text,
            generic_terms=entity_generic_terms,
            time_terms=entity_time_terms,
            action_terms=entity_action_terms,
            action_phrase_hints=entity_action_phrase_hints,
            stop_words=entity_stop_words,
            prefixes=entity_prefixes,
            trailing_suffixes=entity_trailing_suffixes,
        )
        if not name or name in seen:
            continue
        seen.add(name)
        normalized.append(name)

    return normalized


def normalize_event_payload(
    payload: Any,
    episode_text: str = "",
    *,
    participant_hints: tuple[tuple[str, str], ...] | None = None,
    dynamic_hints: Any = None,
    structured_dialogue_markers: tuple[str, ...] | None = None,
    telemetry_markers: tuple[str, ...] | None = None,
    telemetry_roles: set[str] | frozenset[str] | None = None,
    passive_screen_prefix: str | None = None,
    passive_screen_markers: tuple[str, ...] | None = None,
    passive_screen_dynamic_hints: tuple[str, ...] | None = None,
    event_summary_participant_separator: str | None = None,
    event_summary_separator: str | None = None,
    event_summary_time_prefix: str | None = None,
    event_summary_result_prefix: str | None = None,
) -> dict[str, Any]:
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
        participants = _infer_participants_from_text(
            f"{episode_text} {_to_text(event_payload)}",
            participant_hints=participant_hints,
        )
    participants = _dedupe_participants(participants)
    time_range = _normalize_time_range(time_value)
    action = _sanitize_event_field_text(action_value)
    result = _sanitize_event_field_text(outcome_value)
    summary = _sanitize_event_field_text(
        _pick_first(event_payload, ["summary", "Summary", "event_summary"], "")
    )

    if summary and (
        _looks_like_episode_text(
            summary,
            episode_text,
            structured_dialogue_markers=structured_dialogue_markers,
        )
        or _looks_like_raw_record(summary)
    ):
        summary = ""
    if action and _looks_like_raw_record(action):
        action = ""
    if result and _looks_like_raw_record(result):
        result = ""

    if not summary:
        summary = _build_event_summary(
            participants,
            action,
            time_range,
            result,
            participant_separator=event_summary_participant_separator,
            field_separator=event_summary_separator,
            time_prefix=event_summary_time_prefix,
            result_prefix=event_summary_result_prefix,
        )

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
    if _looks_like_telemetry_snapshot(
        telemetry_hint_text,
        participants,
        telemetry_markers=telemetry_markers,
        telemetry_roles=telemetry_roles,
    ):
        summary = ""
        action = ""
        causality = ""

    if _looks_like_passive_screen_app_metadata(
        episode_text,
        prefix=passive_screen_prefix,
        metadata_markers=passive_screen_markers,
        dynamic_hints=passive_screen_dynamic_hints,
    ):
        summary = ""
        action = ""
        causality = ""

    if dynamic_hints is not _SKIP_DYNAMIC_CHECK:
        if not _looks_like_dynamic_change(summary, action, dynamic_hints=dynamic_hints):
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
