# -*- coding: utf-8 -*-
"""Build and validate dynamic evolution memory graph with trips.json."""

import json
import os
import sys
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem import create_ltm, Episode
from limem.utils import parse_time_to_unix


def _extract_episode_text(record: dict[str, Any], bucket_name: str) -> str:
    detail = str(record.get("detail", "") or "").strip()
    if detail:
        return detail

    payload = record.get("payload", {}) if isinstance(record.get("payload"), dict) else {}
    parts = []
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


def safe_compact_json(data: Any) -> str:
    text = json.dumps(data, ensure_ascii=False)
    if len(text) > 220:
        return text[:220] + "..."
    return text


def load_trips_episodes(path: str, max_items: int = 500) -> list[Episode]:
    with open(path, "r", encoding="utf-8") as f:
        trips = json.load(f)
    episodes: list[Episode] = []
    if not isinstance(trips, list):
        return episodes

    for trip in trips:
        if not isinstance(trip, dict):
            continue
        for bucket_name, bucket_value in trip.items():
            if not isinstance(bucket_value, list):
                continue
            for record in bucket_value:
                if not isinstance(record, dict):
                    continue
                start_time = record.get("start_time")
                ts = parse_time_to_unix(start_time) if isinstance(start_time, str) and start_time else 0
                text = _extract_episode_text(record, bucket_name)
                episodes.append(Episode(content=text, timestamp=ts))
                if max_items and len(episodes) >= max_items:
                    return episodes
    return episodes


def main():
    db_path = os.path.join(PROJECT_ROOT, "DB", "dynamic_trips_test.kz")
    trips_path = os.path.join(PROJECT_ROOT, "trips.json")

    if os.path.exists(db_path):
        os.remove(db_path)

    ltm = create_ltm(
        db_path=db_path,
        config={
            "offline_mode": True,
            "enable_dynamic_evolution": True,
            "append_first_mode": True,
            "generate_answer": False,
            "search_top_k": 5,
        },
    )

    episodes = load_trips_episodes(trips_path, max_items=600)
    print(f"Loaded episodes from trips.json: {len(episodes)}")

    for i, ep in enumerate(episodes, 1):
        ltm.ingest(ep)
        if i % 100 == 0:
            print(f"Ingested {i} episodes...")

    stats = ltm.get_stats()
    print("Stats:", stats)

    queries = [
        "用户在开会场景下通常会怎么设置车机？",
        "用户和导航相关的行为模式是什么？",
        "播放媒体和勿扰模式有什么共同情景？",
    ]
    for q in queries:
        result = ltm.search(q, top_k=5, generate_answer=False)
        compact = [f"{e.event_id[:14]}.. | w={e.weight:.4f} | {e.summary[:42]}" for e in result.top_k_events]
        print(f"\nQuery: {q}")
        print("Top events:")
        for row in compact:
            print(" ", row)
        evo = ltm.retrieve_memories(q, top_k=5)
        print("Evolution-aware compressed:")
        for row in evo[:3]:
            print(" ", row.get("event_id"), f"score={row.get('evolution_score', 0):.4f}", row.get("compressed_contexts", []))

    consolidation_report = ltm.run_consolidation()
    print("Consolidation report:", consolidation_report)


if __name__ == "__main__":
    main()
