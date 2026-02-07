# -*- coding: utf-8 -*-
from .config import (
    DB_PATH,
    EXPORT_EVERY_EPISODE,
    EXPORT_FULL_GRAPH,
    EXPORT_GRAPH,
    GRAPH_MAX_EVENTS,
    INCLUDE_EPISODES_IN_GRAPH,
    MAX_EPISODES,
    TEST_DATA_PATH,
    TEST_DATASET_KEY,
)
from .data import load_test_episodes
from .db import init_db, open_connection
from .ltm import ResearchLTM
from .viz import MemoryVisualizer


def run_demo():
    # 0) Init DB
    conn = open_connection(DB_PATH)
    init_db(conn)
    ltm = ResearchLTM(conn)
    visualizer = MemoryVisualizer(conn) if EXPORT_GRAPH else None

    # 1) Load test episodes from example.json and process sequentially.
    episodes = load_test_episodes(TEST_DATA_PATH, TEST_DATASET_KEY, MAX_EPISODES)
    print(
        f"📦 Loaded {len(episodes)} episodes from {TEST_DATA_PATH} | {TEST_DATASET_KEY}"
    )
    last_event_id = None
    last_time = 0
    for text, ts in episodes:
        last_event_id = ltm.process_new_episode(text, ts)
        last_time = ts
        if visualizer and EXPORT_EVERY_EPISODE and last_event_id:
            visualizer.export_event_subgraph(last_event_id, f"event_{last_event_id}")

    if visualizer and EXPORT_FULL_GRAPH:
        visualizer.export_memory_graph(
            "memory_graph",
            include_episodes=INCLUDE_EPISODES_IN_GRAPH,
            max_events=GRAPH_MAX_EVENTS,
        )

    # 2) Peek decay after a long gap (no DB update).
    if last_event_id:
        future_time = last_time + 100000
        ltm.peek_decayed_weights(last_event_id, future_time)
        if visualizer and not EXPORT_EVERY_EPISODE:
            visualizer.export_event_subgraph(last_event_id, f"event_{last_event_id}")

    # 3) TTL cleanup (Episodes only).
    ltm.cleanup_ttl(last_time + 100001)


if __name__ == "__main__":
    run_demo()
