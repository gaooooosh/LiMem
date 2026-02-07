# -*- coding: utf-8 -*-
import json

from .utils import parse_time_to_unix


def load_test_episodes(path, dataset_key, max_items):
    # Experimental data loader: keep it flat and transparent for debugging.
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    records = data.get(dataset_key, [])
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
