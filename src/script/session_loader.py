# -*- coding: utf-8 -*-
"""Utilities for loading session_v1.json into session-level Episode objects.

Session format:
  [ { "user_id": "...", "session": { ... }, "log_list": [ ... ] }, ... ]

Each Episode now represents one complete vehicle session. The Episode content is
composed as a narrative with a short header plus a time-ordered timeline of all
included log records.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from limem.utils import parse_time_to_unix
from script.trips_loader import (
    TripSplitResult,
    _clean_text_fragment,
    extract_episode_text,
    split_trips_episodes,
)

if TYPE_CHECKING:
    from limem.core.episode import Episode


_DETAIL_PREFIX_PATTERNS = (
    re.compile(r"^\s*20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?\s*"),
    re.compile(
        r"^\s*20\d{2}[-/]\d{1,2}[-/]\d{1,2}\s*"
        r"(?:凌晨|清晨|早上|上午|中午|下午|傍晚|晚上)?\s*"
        r"\d{1,2}(?:[:点时]\d{1,2})?(?:分)?(?:左右)?[，, ]*"
    ),
)

_VEHICLE_MODE_LABELS = {
    "face_foot": "面/脚送风",
    "face": "面部送风",
    "foot": "脚部送风",
    "defrost": "除雾",
}

_VEHICLE_STATUS_LABELS = {
    "charging": "充电中",
    "full": "已充满",
    "park": "驻车",
    "parking": "驻车",
    "ready": "就绪",
    "off": "关闭",
}

_MEDIA_AREA_LABELS = {
    1: "前排音区",
    2: "后排音区",
}


def _extract_visual_text(visual: dict[str, Any]) -> str:
    """Extract human-readable text from a visual (camera) record."""
    parts: list[str] = []
    for role in ("DRIVER", "PASSENGER"):
        info = visual.get(role)
        if not isinstance(info, dict):
            continue
        desc = _clean_text_fragment(info.get("description", ""), max_len=80)
        if not desc or desc in ("无其他乘客", "无乘客", "空"):
            continue
        emotion = _clean_text_fragment(info.get("emotion", ""), max_len=15)
        action = _clean_text_fragment(info.get("action", ""), max_len=15)
        role_label = "主驾" if role == "DRIVER" else "乘客"
        extras = [x for x in (emotion, action) if x and x != "其他" and x != "未说话"]
        if extras:
            parts.append(f"{role_label}: {desc} ({', '.join(extras)})")
        else:
            parts.append(f"{role_label}: {desc}")
    return " | ".join(parts)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def _parse_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _format_clock(value: Any) -> str:
    dt = _parse_datetime(value)
    if dt is not None:
        return dt.strftime("%H:%M")
    text = str(value or "").strip()
    if len(text) >= 16:
        return text[11:16]
    return ""


def _format_header_datetime(value: Any) -> str:
    dt = _parse_datetime(value)
    if dt is not None:
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(value or "").strip()


def _format_duration_minutes(start_time: Any, end_time: Any, total_duration_min: Any) -> str:
    if isinstance(total_duration_min, (int, float)) and total_duration_min > 0:
        return f"{int(total_duration_min)}分钟"
    start_dt = _parse_datetime(start_time)
    end_dt = _parse_datetime(end_time)
    if start_dt and end_dt and end_dt >= start_dt:
        minutes = int((end_dt - start_dt).total_seconds() // 60)
        if minutes > 0:
            return f"{minutes}分钟"
    return ""


def _normalize_detail_text(detail: Any) -> str:
    text = str(detail or "").strip()
    if not text:
        return ""
    for pattern in _DETAIL_PREFIX_PATTERNS:
        text = pattern.sub("", text, count=1)
    text = text.lstrip("，,。；;：: ")
    return _clean_text_fragment(text, max_len=220)


def _format_scalar_value(value: Any, *, unit: str = "") -> str:
    if _is_blank(value):
        return ""
    if isinstance(value, float):
        if value.is_integer():
            text = str(int(value))
        else:
            text = f"{value:.1f}".rstrip("0").rstrip(".")
    else:
        text = str(value).strip()
    return f"{text}{unit}" if text else ""


def _format_vehicle_value(field_key: str, value: Any, *, for_snapshot: bool) -> str:
    if isinstance(value, tuple):
        if field_key.endswith("window_pct"):
            if for_snapshot and not any((isinstance(item, (int, float)) and item != 0) for item in value):
                return ""
            return "/".join(_format_scalar_value(item, unit="%") or "0%" for item in value)
        return "/".join(_format_scalar_value(item) for item in value if not _is_blank(item))

    if _is_blank(value):
        if for_snapshot:
            return ""
        if field_key.endswith(("hvac_fan", "seat_vent", "seat_heat", "hvac_mode")):
            return "关闭"
        if field_key.endswith("hvac_temp_set"):
            return "未设定"
        return ""

    if field_key.endswith(("hvac_temp_set", "temp_out", "temp_in")):
        return _format_scalar_value(value, unit="°C")
    if field_key.endswith("wind_spd"):
        return _format_scalar_value(value, unit="m/s")
    if field_key.endswith("noise_db"):
        return _format_scalar_value(value, unit="dB")
    if field_key.endswith(("sunroof_pct", "sunshade_pct")):
        formatted = _format_scalar_value(value, unit="%")
        if for_snapshot and formatted == "0%":
            return ""
        return formatted
    if field_key.endswith("pwr_chg_rem"):
        return _format_scalar_value(value, unit="分钟")
    if field_key.endswith("pwr_chg_v"):
        return _format_scalar_value(value, unit="V")
    if field_key.endswith("pwr_chg_a"):
        return _format_scalar_value(value, unit="A")
    if field_key.endswith(
        (
            "pwr_chg_stat",
            "vehicle_stat",
            "vehicle_status",
            "door_stat",
            "door_status",
            "lock_status",
            "pwr_stat",
        )
    ):
        raw = str(value).strip()
        return _VEHICLE_STATUS_LABELS.get(raw, raw)
    if field_key.endswith("hvac_mode"):
        raw = str(value).strip()
        return _VEHICLE_MODE_LABELS.get(raw, raw)
    if field_key.endswith(("seat_heat", "seat_vent", "hvac_fan", "seat_ang", "lka_freq")):
        formatted = _format_scalar_value(value)
        if for_snapshot and formatted == "0":
            return ""
        return formatted
    return _clean_text_fragment(value, max_len=40)


def _normalize_state_value(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_normalize_state_value(item) for item in value)
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, str):
        return value.strip()
    return value


def _seat_zone_label(item: dict[str, Any], index: int) -> str:
    explicit = _clean_text_fragment(
        item.get("seat_pos_name") or item.get("seat") or item.get("seat_area"),
        max_len=12,
    )
    if explicit:
        return explicit
    return f"区域{index + 1}"


def _capture_vehicle_state_fields(
    state: dict[str, dict[str, Any]],
    prefix_id: str,
    prefix_label: str,
    container: dict[str, Any],
    fields: tuple[tuple[str, str], ...],
) -> None:
    for field_key, label in fields:
        if field_key not in container:
            continue
        state[f"{prefix_id}.{field_key}"] = {
            "field_key": field_key,
            "label": f"{prefix_label}{label}" if prefix_label else label,
            "value": _normalize_state_value(container.get(field_key)),
        }


def _capture_vehicle_state(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return state

    body = payload.get("body")
    if isinstance(body, dict):
        _capture_vehicle_state_fields(
            state,
            "body",
            "",
            body,
            (
                ("window_pct", "车窗开度"),
                ("sunroof_pct", "天窗开度"),
                ("sunshade_pct", "遮阳帘开度"),
                ("door_lock_status", "车门锁状态"),
            ),
        )

    tracked_hvac_fields = (
        ("seat_vent", "座椅通风"),
        ("seat_heat", "座椅加热"),
        ("hvac_fan", "空调风量"),
        ("hvac_temp_set", "空调温度"),
        ("hvac_mode", "空调模式"),
    )
    seat_hvac = payload.get("seat_hvac")
    if isinstance(seat_hvac, dict):
        _capture_vehicle_state_fields(state, "seat_hvac", "", seat_hvac, tracked_hvac_fields)
    elif isinstance(seat_hvac, list):
        for index, item in enumerate(seat_hvac):
            if not isinstance(item, dict):
                continue
            zone = _seat_zone_label(item, index)
            _capture_vehicle_state_fields(
                state,
                f"seat_hvac.{index}",
                zone,
                item,
                tracked_hvac_fields,
            )

    for top_key, prefix in (("seat_hvac_main", "主驾"), ("seat_hvac_second_row", "二排")):
        top_value = payload.get(top_key)
        if isinstance(top_value, dict):
            _capture_vehicle_state_fields(state, top_key, prefix, top_value, tracked_hvac_fields)

    power = payload.get("power")
    if isinstance(power, dict):
        _capture_vehicle_state_fields(
            state,
            "power",
            "",
            power,
            (
                ("pwr_chg_stat", "充电状态"),
                ("pwr_chg_rem", "剩余充电时间"),
                ("pwr_chg_v", "充电电压"),
                ("pwr_chg_a", "充电电流"),
                ("pwr_stat", "电源状态"),
                ("park_status", "驻车状态"),
                ("gear_status", "档位"),
            ),
        )

    safety = payload.get("safety")
    if isinstance(safety, dict):
        _capture_vehicle_state_fields(
            state,
            "safety",
            "",
            safety,
            (
                ("lane_deviation", "车道偏离"),
                ("abnormal_lane_deviation", "异常偏离"),
                ("continuous_driving_duration", "连续驾驶时长"),
                ("continuous_driving_time", "连续驾驶时长"),
            ),
        )

    _capture_vehicle_state_fields(
        state,
        "top",
        "",
        payload,
        (
            ("vehicle_stat", "车辆状态"),
            ("vehicle_status", "车辆状态"),
            ("door_stat", "车门状态"),
            ("door_status", "车门状态"),
            ("lock_status", "锁止状态"),
        ),
    )

    for text_key in ("desc", "status_desc", "additional_desc", "extend_desc"):
        if text_key in payload:
            state[f"text.{text_key}"] = {
                "field_key": text_key,
                "label": "状态说明",
                "value": _normalize_state_value(payload.get(text_key)),
            }
    return state


def _summarize_vehicle_state(state: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in state.values():
        field_key = str(item.get("field_key", ""))
        label = str(item.get("label", "") or "")
        value = _format_vehicle_value(field_key, item.get("value"), for_snapshot=True)
        if not label or not value:
            continue
        parts.append(f"{label}={value}")
    return ", ".join(parts[:6])


def _diff_vehicle_state(
    previous_state: dict[str, dict[str, Any]],
    current_state: dict[str, dict[str, Any]],
) -> str:
    changes: list[str] = []
    for field_id, item in current_state.items():
        if field_id not in previous_state:
            continue
        previous_item = previous_state[field_id]
        if previous_item.get("value") == item.get("value"):
            continue
        field_key = str(item.get("field_key", ""))
        before = _format_vehicle_value(field_key, previous_item.get("value"), for_snapshot=False)
        after = _format_vehicle_value(field_key, item.get("value"), for_snapshot=False)
        label = str(item.get("label", "") or "")
        if label and before and after:
            changes.append(f"{label} {before}→{after}")
    return ", ".join(changes[:6])


def _summarize_environment_payload(payload: dict[str, Any]) -> str:
    cabin = payload.get("cabin") if isinstance(payload.get("cabin"), dict) else {}
    env = payload.get("env") if isinstance(payload.get("env"), dict) else {}
    location = payload.get("location") if isinstance(payload.get("location"), dict) else {}

    parts: list[str] = []
    temp_out = _format_vehicle_value("env.temp_out", env.get("temp_out"), for_snapshot=False)
    if temp_out:
        parts.append(f"室外{temp_out}")
    temp_in = _format_vehicle_value("cabin.temp_in", cabin.get("temp_in"), for_snapshot=False)
    if temp_in:
        parts.append(f"车内{temp_in}")
    noise = _format_vehicle_value("cabin.noise_db", cabin.get("noise_db"), for_snapshot=False)
    if noise:
        parts.append(f"噪音{noise}")
    wind = _format_vehicle_value("env.wind_spd", env.get("wind_spd"), for_snapshot=False)
    if wind:
        parts.append(f"风速{wind}")
    warning = _clean_text_fragment(env.get("warning", ""), max_len=40)
    if warning:
        parts.append(f"预警={warning}")
    location_text = _clean_text_fragment(
        location.get("geo_desc") or location.get("desc") or location.get("geo_type"),
        max_len=40,
    )
    if location_text:
        parts.append(f"位置={location_text}")
    return ", ".join(parts)


def _summarize_schedule_payload(payload: dict[str, Any]) -> str:
    event_name = _clean_text_fragment(payload.get("cal_evt", ""), max_len=60)
    source = _clean_text_fragment(payload.get("cal_src", ""), max_len=20)
    start_text = _format_clock(payload.get("cal_start"))
    end_text = _format_clock(payload.get("cal_end"))
    desc = _clean_text_fragment(payload.get("desc", ""), max_len=80)
    status = _clean_text_fragment(payload.get("cal_status", ""), max_len=24)
    level = _clean_text_fragment(payload.get("cal_level", ""), max_len=24)
    reminder = _clean_text_fragment(payload.get("remind_status", ""), max_len=24)
    follow_up = _clean_text_fragment(payload.get("follow_up_conflict", ""), max_len=24)

    if event_name and event_name.lower() != "none":
        summary = event_name
        if start_text or end_text:
            summary += f" {start_text or '--'}-{end_text or '--'}"
        if source:
            summary += f" ({source})"
        extras = [item for item in (status, level, reminder) if item]
        if follow_up and follow_up != "无":
            extras.append(f"后续冲突={follow_up}")
        if desc:
            extras.append(desc)
        if extras:
            summary += ", " + ", ".join(extras)
        return summary

    if desc:
        return desc
    if status:
        return f"日程状态={status}"
    return "无相关日程"


def _summarize_audio_payload(payload: dict[str, Any]) -> str:
    audio = payload.get("audio") if isinstance(payload.get("audio"), dict) else {}
    voiceprint_user = (
        audio.get("voiceprint_user") if isinstance(audio.get("voiceprint_user"), dict) else {}
    )
    recognized = [
        f"{seat}为{name}"
        for seat, name in voiceprint_user.items()
        if _clean_text_fragment(name, max_len=20)
    ]
    parts: list[str] = []
    if recognized:
        parts.append(", ".join(recognized[:3]))
        parts.append(f"车内共{len(recognized)}人")
    zone = _clean_text_fragment(audio.get("audio_source_zone", ""), max_len=16)
    user = _clean_text_fragment(audio.get("audio_source_user", ""), max_len=20)
    if zone or user:
        parts.append(f"音源={zone}{user}")
    noise = _format_vehicle_value("audio.noise_db", audio.get("noise_db"), for_snapshot=False)
    if noise:
        parts.append(f"噪音{noise}")
    return ", ".join(parts)


def _summarize_dialog_payload(payload: dict[str, Any]) -> str:
    query = _clean_text_fragment(payload.get("query", ""), max_len=80)
    tts = _clean_text_fragment(payload.get("tts", ""), max_len=120)
    if query and tts:
        return f"用户说「{query}」→「{tts}」"
    if query:
        return f"用户说「{query}」"
    if tts:
        return f"车机播报「{tts}」"
    domain = _clean_text_fragment(payload.get("domain", ""), max_len=24)
    return f"对话域={domain}" if domain else ""


def _summarize_navigation_payload(payload: dict[str, Any]) -> str:
    start_poi = _clean_text_fragment(payload.get("startPoi", ""), max_len=36)
    end_poi = _clean_text_fragment(payload.get("endPoi", ""), max_len=36)
    cost_text = _format_scalar_value(payload.get("costTime"), unit="分钟")
    has_end = bool(_clean_text_fragment(payload.get("endTime", ""), max_len=32))

    if has_end and end_poi:
        return f"到达{end_poi}" + (f" ({cost_text})" if cost_text else "")
    route = " → ".join(part for part in (start_poi or "当前位置", end_poi) if part)
    if route:
        if cost_text:
            prefix = "" if has_end else "预计"
            route += f" ({prefix}{cost_text})"
        return route
    return ""


def _summarize_media_payload(payload: dict[str, Any]) -> str:
    detail_desc = _normalize_detail_text(payload.get("detail_desc", ""))
    if detail_desc:
        return detail_desc

    app_name = _clean_text_fragment(payload.get("app_name", ""), max_len=24)
    title = _clean_text_fragment(payload.get("title", ""), max_len=40)
    artist = _clean_text_fragment(payload.get("artist", ""), max_len=24)
    volume = _format_scalar_value(payload.get("volume"))
    area = payload.get("area")
    area_text = _MEDIA_AREA_LABELS.get(area, "") if isinstance(area, int) else ""

    parts: list[str] = []
    if area_text:
        parts.append(area_text)
    if app_name:
        parts.append(app_name)
    if title and artist:
        parts.append(f"播放{artist}《{title}》")
    elif title:
        parts.append(f"播放《{title}》")
    if volume:
        parts.append(f"音量{volume}级")
    return ", ".join(parts)


def _summarize_screen_payload(payload: dict[str, Any]) -> str:
    screen = _clean_text_fragment(payload.get("SCREEN") or payload.get("screen"), max_len=16)
    app_name = _clean_text_fragment(payload.get("APP") or payload.get("app"), max_len=24)
    description = _clean_text_fragment(
        payload.get("DESCRIPTION") or payload.get("description"),
        max_len=140,
    )
    media_name = _clean_text_fragment(payload.get("MEDIA_NAME", ""), max_len=40)
    play_status = _clean_text_fragment(payload.get("PLAY_STATUS", ""), max_len=20)

    parts: list[str] = []
    if screen:
        parts.append(screen)
    if app_name:
        parts.append(app_name)
    if description:
        parts.append(description)
    else:
        if media_name:
            parts.append(media_name)
        if play_status:
            parts.append(play_status)
    return "，".join(parts)


def _summarize_generic_payload(payload: dict[str, Any], source: str) -> str:
    text = extract_episode_text({"payload": payload, "source": source}, source)
    prefix = f"[{source}] "
    if text.startswith(prefix):
        text = text[len(prefix) :]
    return _clean_text_fragment(text, max_len=220)


def _record_summary(
    record: dict[str, Any],
    previous_vehicle_state: Optional[dict[str, dict[str, Any]]],
) -> tuple[str, str, Optional[dict[str, dict[str, Any]]]]:
    source = _clean_text_fragment(record.get("source", ""), max_len=20) or "session"
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}

    if source == "车辆状态":
        current_state = _capture_vehicle_state(payload)
        diff_text = (
            _diff_vehicle_state(previous_vehicle_state or {}, current_state)
            if previous_vehicle_state
            else ""
        )
        if diff_text:
            return "车辆状态变化", diff_text, current_state
        snapshot_text = _summarize_vehicle_state(current_state)
        if snapshot_text:
            return source, snapshot_text, current_state
        fallback_text = _normalize_detail_text(record.get("detail", "")) or _summarize_generic_payload(payload, source)
        return source, fallback_text, current_state

    detail = _normalize_detail_text(record.get("detail", ""))
    if detail:
        return source, detail, previous_vehicle_state

    visual = record.get("visual")
    if isinstance(visual, dict):
        visual_text = _extract_visual_text(visual)
        if visual_text:
            return source, visual_text, previous_vehicle_state

    if not isinstance(payload, dict):
        return source, "", previous_vehicle_state
    if source == "环境感知":
        return source, _summarize_environment_payload(payload), previous_vehicle_state
    if source == "日程数据":
        return source, _summarize_schedule_payload(payload), previous_vehicle_state
    if source == "车内音频":
        return source, _summarize_audio_payload(payload), previous_vehicle_state
    if source == "车机对话":
        return source, _summarize_dialog_payload(payload), previous_vehicle_state
    if source == "导航记录":
        return source, _summarize_navigation_payload(payload), previous_vehicle_state
    if source.startswith("媒体播放"):
        return source, _summarize_media_payload(payload), previous_vehicle_state
    if source == "屏幕":
        return source, _summarize_screen_payload(payload), previous_vehicle_state
    return source, _summarize_generic_payload(payload, source), previous_vehicle_state


def _compose_session_narrative(
    session_obj: dict[str, Any],
    include_sources: Optional[set[str]] = None,
) -> tuple[str, dict[str, Any]]:
    session_meta = session_obj.get("session", {})
    if not isinstance(session_meta, dict):
        session_meta = {}
    raw_log_list = session_obj.get("log_list", [])
    if not isinstance(raw_log_list, list):
        raw_log_list = []

    filtered_records: list[tuple[int, int, dict[str, Any]]] = []
    ordered_sources: list[str] = []
    seen_sources: set[str] = set()
    for record_index, record in enumerate(raw_log_list):
        if not isinstance(record, dict):
            continue
        source = str(record.get("source", "") or "")
        if include_sources and source not in include_sources:
            continue
        if source and source not in seen_sources:
            seen_sources.add(source)
            ordered_sources.append(source)
        start_time = record.get("start_time")
        ts = parse_time_to_unix(start_time) if isinstance(start_time, str) and start_time else 0
        filtered_records.append((ts, record_index, record))

    if not filtered_records:
        return "", {}

    filtered_records.sort(key=lambda item: (item[0], item[1]))

    session_desc = _clean_text_fragment(session_meta.get("session_desc", ""), max_len=280)
    triggered_scenes = session_meta.get("triggered_scenes", [])
    scenes = (
        [
            _clean_text_fragment(scene, max_len=24)
            for scene in triggered_scenes
            if _clean_text_fragment(scene, max_len=24)
        ]
        if isinstance(triggered_scenes, list)
        else []
    )
    start_time = session_meta.get("start_time", "")
    end_time = session_meta.get("end_time", "")
    duration_text = _format_duration_minutes(
        start_time,
        end_time,
        session_meta.get("total_duration_min"),
    )

    lines: list[str] = []
    previous_vehicle_state: Optional[dict[str, dict[str, Any]]] = None
    for _, _, record in filtered_records:
        source_label, summary, previous_vehicle_state = _record_summary(record, previous_vehicle_state)
        summary = _clean_text_fragment(summary, max_len=220)
        if not summary:
            continue
        clock = _format_clock(record.get("start_time")) or "--:--"
        lines.append(f"[{clock}] {source_label}: {summary}")

    if not lines:
        return "", {}

    narrative_parts: list[str] = []
    if session_desc:
        narrative_parts.append(f"【会话概要】{session_desc}")
    if scenes:
        narrative_parts.append(f"【触发场景】{', '.join(scenes)}")
    time_range_text = " ~ ".join(
        part for part in (_format_header_datetime(start_time), _format_header_datetime(end_time)) if part
    )
    if time_range_text:
        if duration_text:
            time_range_text += f" ({duration_text})"
        narrative_parts.append(f"【时间范围】{time_range_text}")
    narrative_parts.append("")
    narrative_parts.append("--- 时间线 ---")
    narrative_parts.extend(lines)

    metadata = {
        "session_id": str(session_meta.get("session_id", "") or ""),
        "user_id": str(session_obj.get("user_id", "") or ""),
        "start_time": str(start_time or ""),
        "end_time": str(end_time or ""),
        "triggered_scenes": scenes,
        "log_count": len(filtered_records),
        "sources": ordered_sources,
        "source": "session",
        "session_desc": str(session_meta.get("session_desc", "") or ""),
    }
    return "\n".join(narrative_parts), metadata


def load_session_episodes(
    path: str,
    max_items: int = 0,
    include_sources: Optional[set[str]] = None,
    sort_by_time: bool = True,
) -> list["Episode"]:
    """Load session_v1.json and build one narrative-rich Episode per session."""
    from limem.core.episode import Episode

    with open(path, "r", encoding="utf-8") as f:
        sessions = json.load(f)

    episodes: list[Episode] = []
    if not isinstance(sessions, list):
        return episodes

    for session_index, session_obj in enumerate(sessions):
        if not isinstance(session_obj, dict):
            continue

        session_meta = session_obj.get("session", {})
        if not isinstance(session_meta, dict):
            session_meta = {}
        session_start = session_meta.get("start_time", "")
        ts = parse_time_to_unix(session_start) if isinstance(session_start, str) and session_start else 0

        content, metadata = _compose_session_narrative(
            session_obj,
            include_sources=include_sources,
        )
        if not content:
            continue
        metadata["session_index"] = session_index
        episodes.append(Episode(content=content, timestamp=ts, metadata=metadata))

    if sort_by_time:
        episodes.sort(
            key=lambda ep: (
                int(ep.timestamp or 0),
                int(ep.metadata.get("session_index", 0)),
            )
        )

    if max_items > 0:
        return episodes[:max_items]
    return episodes


def load_and_split_session_episodes(
    path: str,
    max_items: int = 0,
    include_sources: Optional[set[str]] = None,
    sort_by_time: bool = True,
    split_index: int = 0,
    split_ratio: float = 0.7,
    debug_max_items: int = 0,
) -> TripSplitResult:
    """Load session episodes and split into base/debug phases."""
    episodes = load_session_episodes(
        path=path,
        max_items=max_items,
        include_sources=include_sources,
        sort_by_time=sort_by_time,
    )
    return split_trips_episodes(
        episodes=episodes,
        split_index=split_index,
        split_ratio=split_ratio,
        debug_max_items=debug_max_items,
    )
