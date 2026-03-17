# -*- coding: utf-8 -*-
"""Utilities for loading trips.json into Episode objects."""

from __future__ import annotations

import json
from typing import Any, Optional

from limem import Episode
from limem.utils import parse_time_to_unix


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

    # fallback for non-dialog records
    if not parts:
        parts.append(safe_compact_json(record))
    return f"[{bucket_name}] " + " | ".join(parts)


def load_trips_episodes(
    path: str,
    max_items: int = 0,
    include_buckets: Optional[set[str]] = None,
) -> list[Episode]:
    """Load trips.json and flatten all event-like records into Episode list."""
    with open(path, "r", encoding="utf-8") as f:
        trips = json.load(f)

    episodes: list[Episode] = []
    if not isinstance(trips, list):
        return episodes

    for trip in trips:
        if not isinstance(trip, dict):
            continue
        for bucket_name, bucket_value in trip.items():
            if include_buckets and bucket_name not in include_buckets:
                continue
            if not isinstance(bucket_value, list):
                continue
            for record in bucket_value:
                if not isinstance(record, dict):
                    continue
                start_time = record.get("start_time")
                ts = parse_time_to_unix(start_time) if isinstance(start_time, str) and start_time else 0
                text = extract_episode_text(record, bucket_name)
                episodes.append(Episode(content=text, timestamp=ts))
                if max_items and len(episodes) >= max_items:
                    return episodes
    return episodes
