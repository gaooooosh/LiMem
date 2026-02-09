# -*- coding: utf-8 -*-
import json

from .utils import parse_time_to_unix


def _resolve_dataset_keys(data, dataset_key):
    if not dataset_key or dataset_key.strip().lower() == "all":
        return list(data.keys())
    if "," in dataset_key:
        keys = []
        for part in dataset_key.split(","):
            key = part.strip()
            if key:
                keys.append(key)
        return keys
    return [dataset_key]


def _extract_records(records, max_items):
    episodes = []
    for rec in records:
        if rec.get("source") != "车机对话":
            continue
        payload = rec.get("payload", {})
        detail = rec.get("detail", "")
        if detail:
            text = detail
        else:
            query = payload.get("query", "")
            tts = payload.get("tts", "")
            text = f"用户说: {query} -> 车机回答: {tts}"
        ts = rec.get("start_time", "")
        if ts:
            unix_ts = parse_time_to_unix(ts)
        else:
            unix_ts = 0
        episodes.append((text, unix_ts))
        if max_items and len(episodes) >= max_items:
            break
    return episodes


def load_test_episodes(path, dataset_key, max_items):
    # Experimental data loader: keep it flat and transparent for debugging.
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    keys = _resolve_dataset_keys(data, dataset_key)
    episodes = []
    for key in keys:
        records = data.get(key, [])
        if not records:
            continue
        for text, unix_ts in _extract_records(records, max_items):
            episodes.append((text, unix_ts, key))
    return episodes
