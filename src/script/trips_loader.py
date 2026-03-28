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


def extract_episode_text(record: dict[str, Any], bucket_name: str) -> str:
    detail = str(record.get("detail", "") or "").strip()
    if detail:
        return detail

    payload = record.get("payload", {}) if isinstance(record.get("payload"), dict) else {}
    parts: list[str] = []
    if payload.get("query"):
        parts.append(f"用户说: {payload.get('query')}")
    if payload.get("tts"):
        parts.append(f"车机回答: {payload.get('tts')}")
    if payload.get("title"):
        parts.append(f"内容: {payload.get('title')}")
    if payload.get("endPoi"):
        parts.append(f"目的地: {payload.get('endPoi')}")

    if not parts:
        parts.append(safe_compact_json(record))
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
