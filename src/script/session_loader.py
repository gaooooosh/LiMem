# -*- coding: utf-8 -*-
"""Utilities for loading session_v1.json into Episode objects.

Session format:
  [ { "user_id": "...", "session": { ... }, "log_list": [ ... ] }, ... ]

Each log_list entry may contain:
  - "detail" (text description, preferred)
  - "visual" (camera data with DRIVER/PASSENGER descriptions)
  - "payload" (structured sensor/interaction data, fallback to trips_loader)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

from limem.utils import parse_time_to_unix
from script.trips_loader import (
    TripSplitResult,
    _clean_text_fragment,
    extract_episode_text,
    safe_compact_json,
    split_trips_episodes,
)

if TYPE_CHECKING:
    from limem.core.episode import Episode


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


def extract_session_episode_text(record: dict[str, Any], session_desc: str) -> str:
    """Convert a single log_list record into episode text.

    Priority:
      1. detail field (most informative)
      2. visual field (camera descriptions)
      3. Fallback to trips_loader.extract_episode_text for payload
    """
    # 1. detail field
    detail = str(record.get("detail", "") or "").strip()
    if detail:
        return detail

    # 2. visual field (camera records)
    visual = record.get("visual")
    if isinstance(visual, dict):
        text = _extract_visual_text(visual)
        if text:
            source = _clean_text_fragment(record.get("source", ""), max_len=20)
            prefix = f"[{source}] " if source else ""
            return f"{prefix}{text}"

    # 3. Fallback to trips_loader for payload-based extraction
    source = _clean_text_fragment(record.get("source", ""), max_len=20) or "session"
    return extract_episode_text(record, source)


def load_session_episodes(
    path: str,
    max_items: int = 0,
    include_sources: Optional[set[str]] = None,
    sort_by_time: bool = True,
) -> list["Episode"]:
    """Load session_v1.json and flatten all log records into Episode list.

    Args:
        path: Path to session_v1.json
        max_items: Max episodes to return (0 = all)
        include_sources: Filter by source types (None = all)
        sort_by_time: Sort by timestamp
    """
    from limem.core.episode import Episode

    with open(path, "r", encoding="utf-8") as f:
        sessions = json.load(f)

    episodes: list[Episode] = []
    if not isinstance(sessions, list):
        return episodes

    for session_index, session_obj in enumerate(sessions):
        if not isinstance(session_obj, dict):
            continue

        user_id = session_obj.get("user_id", "")
        session_meta = session_obj.get("session", {})
        if not isinstance(session_meta, dict):
            session_meta = {}
        session_id = session_meta.get("session_id", "")
        session_desc = session_meta.get("session_desc", "")
        session_start = session_meta.get("start_time", "")
        triggered_scenes = session_meta.get("triggered_scenes", [])

        log_list = session_obj.get("log_list", [])
        if not isinstance(log_list, list):
            continue

        for record_index, record in enumerate(log_list):
            if not isinstance(record, dict):
                continue

            source = str(record.get("source", "") or "")
            if include_sources and source not in include_sources:
                continue

            start_time = record.get("start_time")
            ts = parse_time_to_unix(start_time) if isinstance(start_time, str) and start_time else 0

            text = extract_session_episode_text(record, session_desc)

            metadata = {
                "session_index": session_index,
                "session_id": session_id,
                "user_id": user_id,
                "source": source,
                "start_time": start_time or "",
                "triggered_scenes": triggered_scenes if isinstance(triggered_scenes, list) else [],
            }
            episodes.append(Episode(content=text, timestamp=ts, metadata=metadata))

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
