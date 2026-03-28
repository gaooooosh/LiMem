# -*- coding: utf-8 -*-
"""Utilities for loading and splitting trips.json into Episode objects."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from limem import Episode
from limem.utils import parse_time_to_unix


@dataclass
class TripSplitResult:
    base_episodes: list[Episode]
    debug_episodes: list[Episode]
    split_index: int
    split_ratio: float
    total_episodes: int


def safe_compact_json(data: Any, max_len: int = 220) -> str:
    text = json.dumps(data, ensure_ascii=False)
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _compact_join(parts: list[str], max_len: int = 220) -> str:
    text = " | ".join(part.strip() for part in parts if str(part).strip())
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def _extract_payload_descriptions(payload: dict[str, Any]) -> list[str]:
    descriptions: list[str] = []
    preferred_keys = [
        "detail_desc",
        "description",
        "DESCRIPTION",
        "desc",
        "summary",
    ]
    for key in preferred_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            descriptions.append(value.strip())

    visual = payload.get("visual")
    if isinstance(visual, dict):
        for item in visual.values():
            if not isinstance(item, dict):
                continue
            description = str(item.get("description", "") or "").strip()
            if description:
                descriptions.append(description)

    return descriptions


def _clean_tts_text(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = cleaned.replace("（触发前确认）", "").replace("（触发后反馈）", "")
    cleaned = cleaned.replace("(触发前确认)", "").replace("(触发后反馈)", "")
    return cleaned.strip()


def _summarize_dialog_payload(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    query = str(payload.get("query", "") or "").strip()
    tts = _clean_tts_text(payload.get("tts", ""))
    if query:
        parts.append(f"用户说: {query}")
    if tts:
        parts.append(f"车机回答: {tts}")
    return _compact_join(parts)


def _summarize_media_payload(payload: dict[str, Any]) -> str:
    app = str(payload.get("app_name", "") or payload.get("APP", "") or "").strip()
    title = str(payload.get("title", "") or payload.get("MEDIA_NAME", "") or "").strip()
    artist = str(payload.get("artist", "") or "").strip()
    parts: list[str] = []
    if app and title:
        parts.append(f"{app}播放《{title}》")
    elif title:
        parts.append(f"播放《{title}》")
    elif app:
        parts.append(f"{app}播放")
    if artist:
        parts.append(f"歌手:{artist}")
    return _compact_join(parts)


def _summarize_navigation_payload(payload: dict[str, Any]) -> str:
    start_poi = str(payload.get("startPoi", "") or "").strip()
    end_poi = str(payload.get("endPoi", "") or "").strip()
    cost_time = payload.get("costTime")
    parts: list[str] = []
    if start_poi and end_poi:
        parts.append(f"从{start_poi}导航到{end_poi}")
    elif end_poi:
        parts.append(f"导航到{end_poi}")
    if cost_time not in (None, "", 0, "0"):
        parts.append(f"用时{cost_time}分钟")
    return _compact_join(parts)


def _summarize_screen_payload(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    app = str(payload.get("APP", "") or payload.get("app_name", "") or "").strip()
    media_name = str(payload.get("MEDIA_NAME", "") or "").strip()
    description = str(payload.get("DESCRIPTION", "") or "").strip()
    screen = str(payload.get("SCREEN", "") or "").strip()
    if screen:
        parts.append(screen)
    if app:
        parts.append(app)
    if media_name:
        parts.append(media_name)
    if description:
        parts.append(description)
    return _compact_join(parts)


def _is_low_signal_visual_description(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    low_signal_markers = [
        "未说话",
        "目视前方",
        "姿态稳定",
        "姿态自然",
        "状态平稳",
        "乘客姿态自然",
    ]
    dynamic_markers = [
        "说话",
        "交谈",
        "打电话",
        "转头",
        "看向",
        "离座",
        "拿起",
        "操作",
        "疲劳",
        "分心",
        "打哈欠",
    ]
    if any(marker in normalized for marker in dynamic_markers):
        return False
    return any(marker in normalized for marker in low_signal_markers)


def _summarize_visual_payload(visual: dict[str, Any]) -> str:
    descriptions: list[str] = []
    for item in visual.values():
        if not isinstance(item, dict):
            continue
        description = str(item.get("description", "") or "").strip()
        if description:
            descriptions.append(description)
    if descriptions and all(_is_low_signal_visual_description(item) for item in descriptions):
        return ""
    return _compact_join(descriptions)


def _summarize_vehicle_state_payload(payload: dict[str, Any]) -> str:
    body = payload.get("body_status", {}) if isinstance(payload.get("body_status"), dict) else {}
    seat = payload.get("seat_and_thermal", {}) if isinstance(payload.get("seat_and_thermal"), dict) else {}
    adas = payload.get("adas_and_chassis", {}) if isinstance(payload.get("adas_and_chassis"), dict) else {}

    parts: list[str] = []
    motion = str(body.get("vehicle_motion", "") or "").strip()
    windows = body.get("window_pct")
    fan = seat.get("hvac_fan")
    temp = seat.get("hvac_temp_set")
    mode = str(seat.get("hvac_mode", "") or "").strip()
    outside_temp = adas.get("outside_temp_hint")

    if motion:
        parts.append(f"车辆状态快照: {motion}")
    if isinstance(windows, list) and windows:
        if all(str(item) in {"0", "0.0"} or item == 0 for item in windows):
            parts.append("车窗全关")
        else:
            parts.append(f"车窗开度={windows}")
    if fan not in (None, ""):
        parts.append(f"风量{fan}档")
    if temp not in (None, ""):
        parts.append(f"空调设定{temp}度")
    if mode:
        parts.append(f"空调模式={mode}")
    if outside_temp not in (None, ""):
        parts.append(f"外界约{outside_temp}度")
    return _compact_join(parts)


def extract_episode_text(record: dict[str, Any], bucket_name: str) -> str:
    payload = record.get("payload", {}) if isinstance(record.get("payload"), dict) else {}
    parts: list[str] = []
    if payload:
        if bucket_name == "车机对话数据":
            dialog_summary = _summarize_dialog_payload(payload)
            if dialog_summary:
                parts.append(dialog_summary)
        elif bucket_name == "媒体播放数据":
            media_summary = _summarize_media_payload(payload)
            if media_summary:
                parts.append(media_summary)
        elif bucket_name in {"导航记录数据", "导航状态数据"}:
            navigation_summary = _summarize_navigation_payload(payload)
            if navigation_summary:
                parts.append(navigation_summary)
        elif bucket_name == "屏幕操作数据":
            screen_summary = _summarize_screen_payload(payload)
            if screen_summary:
                parts.append(screen_summary)
        elif bucket_name == "车辆状态数据":
            vehicle_summary = _summarize_vehicle_state_payload(payload)
            if vehicle_summary:
                parts.append(vehicle_summary)

        if not parts:
            description_parts = _extract_payload_descriptions(payload)
            if description_parts:
                parts.extend(description_parts)

    visual = record.get("visual", {}) if isinstance(record.get("visual"), dict) else {}
    if not parts and visual:
        visual_summary = _summarize_visual_payload(visual)
        if visual_summary:
            parts.append(visual_summary)

    detail = str(record.get("detail", "") or "").strip()
    if not parts and detail:
        parts.append(detail)

    if not parts:
        return ""
    return f"[{bucket_name}] " + " | ".join(parts)


def load_trips_episodes(
    path: str,
    max_items: int = 0,
    include_buckets: Optional[set[str]] = None,
    sort_by_time: bool = True,
) -> list[Episode]:
    """Load trips.json and flatten all event-like records into Episode list."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"trips.json not found: {path}. "
            "Place a compatible trips.json at the repo root or pass --trips-path /path/to/trips.json."
        )

    with open(path, "r", encoding="utf-8") as f:
        trips = json.load(f)

    episodes: list[Episode] = []
    if not isinstance(trips, list):
        return episodes

    for trip_index, trip in enumerate(trips):
        if not isinstance(trip, dict):
            continue
        for bucket_name, bucket_value in trip.items():
            if include_buckets and bucket_name not in include_buckets:
                continue
            if not isinstance(bucket_value, list):
                continue
            for record_index, record in enumerate(bucket_value):
                if not isinstance(record, dict):
                    continue
                start_time = record.get("start_time")
                ts = parse_time_to_unix(start_time) if isinstance(start_time, str) and start_time else 0
                text = extract_episode_text(record, bucket_name)
                if not text.strip():
                    continue
                metadata = {
                    "trip_index": trip_index,
                    "bucket_name": bucket_name,
                    "record_index": record_index,
                    "source": str(record.get("source", "") or ""),
                    "start_time": start_time or "",
                }
                episodes.append(Episode(content=text, timestamp=ts, metadata=metadata))

    if sort_by_time:
        episodes.sort(
            key=lambda ep: (
                int(ep.timestamp or 0),
                int(ep.metadata.get("trip_index", 0)),
                int(ep.metadata.get("record_index", 0)),
            )
        )

    if max_items > 0:
        return episodes[:max_items]
    return episodes


def split_trips_episodes(
    episodes: list[Episode],
    split_index: int = 0,
    split_ratio: float = 0.7,
    debug_max_items: int = 0,
) -> TripSplitResult:
    total = len(episodes)
    if total == 0:
        return TripSplitResult([], [], 0, split_ratio, 0)

    if split_index <= 0:
        normalized_ratio = min(max(split_ratio, 0.0), 1.0)
        split_index = int(total * normalized_ratio)
    else:
        normalized_ratio = split_index / total

    split_index = max(0, min(split_index, total))
    base = list(episodes[:split_index])
    debug = list(episodes[split_index:])
    if debug_max_items > 0:
        debug = debug[:debug_max_items]
    return TripSplitResult(
        base_episodes=base,
        debug_episodes=debug,
        split_index=split_index,
        split_ratio=normalized_ratio,
        total_episodes=total,
    )


def load_and_split_trips_episodes(
    path: str,
    max_items: int = 0,
    include_buckets: Optional[set[str]] = None,
    sort_by_time: bool = True,
    split_index: int = 0,
    split_ratio: float = 0.7,
    debug_max_items: int = 0,
) -> TripSplitResult:
    episodes = load_trips_episodes(
        path=path,
        max_items=max_items,
        include_buckets=include_buckets,
        sort_by_time=sort_by_time,
    )
    return split_trips_episodes(
        episodes=episodes,
        split_index=split_index,
        split_ratio=split_ratio,
        debug_max_items=debug_max_items,
    )
