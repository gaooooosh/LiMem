# -*- coding: utf-8 -*-
"""Demo script for LTM Search functionality.

This script demonstrates complete retrieval pipeline:
1. Entity Extraction (LLM)
2. Graph Path Search (Kuzu Cypher)
3. Weight-based Reranking
4. LLM Summarization

Usage:
    python -m limem.search_demo
"""

import os
import sys

# Add src to path if needed
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem.config import DB_PATH
from limem.db import init_db, open_connection
from limem.ltm import ResearchLTM
from limem.search import LTMSearcher, RetrievalConfig


def get_db_stats(conn):
    """Get database statistics including node and event counts."""
    stats = {}

    # Count node types
    node_types = ["Event", "Entity", "Episode", "User"]
    for node_type in node_types:
        resp = conn.execute(f"MATCH (n:{node_type}) RETURN count(*)")
        if resp.has_next():
            stats[node_type] = resp.get_next()[0]
        else:
            stats[node_type] = 0

    # Count relationships
    rel_types = ["INVOLVES", "EXTRACTED_FROM", "PERMANENT_TRAIT"]
    for rel_type in rel_types:
        resp = conn.execute(f"MATCH ()-[r:{rel_type}]->() RETURN count(*)")
        if resp.has_next():
            stats[rel_type] = resp.get_next()[0]
        else:
            stats[rel_type] = 0

    return stats


def print_db_stats(stats):
    """Print database statistics in a formatted way."""
    print("\n" + "=" * 80)
    print("📊 Database Statistics")
    print("=" * 80)

    print("\n📦 Nodes:")
    print(f"  • Events:   {stats.get('Event', 0):>6}")
    print(f"  • Entities: {stats.get('Entity', 0):>6}")
    print(f"  • Episodes: {stats.get('Episode', 0):>6}")
    print(f"  • Users:    {stats.get('User', 0):>6}")
    print(f"  ──────────────────────")
    print(f"  • Total:    {sum(stats.get(k, 0) for k in ['Event', 'Entity', 'Episode', 'User']):>6}")

    print("\n🔗 Relationships:")
    print(f"  • INVOLVES:        {stats.get('INVOLVES', 0):>6}")
    print(f"  • EXTRACTED_FROM:  {stats.get('EXTRACTED_FROM', 0):>6}")
    print(f"  • PERMANENT_TRAIT: {stats.get('PERMANENT_TRAIT', 0):>6}")
    print(f"  ──────────────────────")
    print(f"  • Total:            {sum(stats.get(k, 0) for k in ['INVOLVES', 'EXTRACTED_FROM', 'PERMANENT_TRAIT']):>6}")

    print("\n" + "=" * 80 + "\n")


def print_detailed_search_result(query: str, result: dict, conn):
    """Print detailed search result for debugging."""
    print(f"\n🔍 Query: {query}")
    print("=" * 80)

    # 1. Extracted entities
    print("\n📝 Stage 1: Extracted Entities")
    print("-" * 80)
    entities = result.get('entities', [])
    if entities:
        # Display in matrix format (4 columns)
        cols = 4
        for i in range(0, len(entities), cols):
            row_entities = entities[i:i+cols]
            row_str = "  ".join(f"{e:<18}" for e in row_entities)
            print(f"  {row_str}")
    else:
        print("  (No entities extracted)")

    # 2. Ranked events with full details
    print("\n📊 Stage 2: Weight-based Reranking (All Ranked Events)")
    print("-" * 80)
    ranked_events = result.get('ranked_events', [])
    if ranked_events:
        for i, event in enumerate(ranked_events, 1):
            print(f"\n  [{i}] Event ID: {event.event_id}")
            print(f"      Summary:       {event.summary}")
            print(f"      Weight:        {event.weight:.6f}")
            print(f"      c_valid:       {event.c_valid}")
            print(f"      Priority:      {event.priority}")
            print(f"      t_valid:       {event.t_valid}")
            print(f"      t_expired:     {event.t_expired}")
            print(f"      t_invalid:     {event.t_invalid}")
            if event.action:
                print(f"      Action:        {event.action}")
            if event.causality:
                print(f"      Causality:     {event.causality}")
            if event.participants:
                print(f"      Participants:  {event.participants}")
            if event.location:
                print(f"      Location:      {event.location}")
            if event.time_range:
                print(f"      Time Range:    {event.time_range}")
    else:
        print("  (No ranked events)")

    # 2.1 Weight calculation details
    debug_info = result.get('debug', {})
    weight_details = debug_info.get('weight_calculation_details', [])
    if weight_details:
        print("\n🔧 Weight Calculation Details (Full Debug)")
        print("-" * 80)
        for i, detail in enumerate(weight_details, 1):
            print(f"\n  [{i}] Event: {detail['event_id']}")
            print(f"      Summary:            {detail['summary']}")
            print(f"      Final Weight:       {detail['weight']:.6f}")
            print(f"      c_valid:           {detail['c_valid']}")
            print(f"      t_valid:           {detail['t_valid']}")
            print(f"      t_now:             {detail['t_now']}")
            print(f"      time_diff:         {detail['time_diff']}")
            print(f"      t_expired:         {detail['t_expired']}")
            print(f"      t_invalid:         {detail['t_invalid']}")
            print(f"      priority:          {detail['priority']}")
            print(f"      match_type:        {detail['match_type']}")
            entity_weights = detail.get('entity_match_weights', {})
            if entity_weights:
                print(f"      entity_match_weights:")
                for entity_id, weight in entity_weights.items():
                    print(f"        - {entity_id}: {weight:.4f}")
            else:
                print(f"      entity_match_weights: (none)")

    # 3. Top-K events
    print("\n🏆 Stage 3: Top-K Selected Events")
    print("-" * 80)
    top_k_events = result.get('top_k_events', [])
    if top_k_events:
        for i, event in enumerate(top_k_events, 1):
            print(f"\n  [{i}] Event ID: {event.event_id}")
            print(f"      Summary:       {event.summary}")
            print(f"      Weight:        {event.weight:.6f}")
            print(f"      c_valid:       {event.c_valid}")
            print(f"      Priority:      {event.priority}")
    else:
        print("  (No top-k events)")

    # 4. Debug summary
    debug_info = result.get('debug', {})
    if debug_info:
        print("\n🔧 Debug Summary")
        print("-" * 80)
        print(f"  Entity count:         {debug_info.get('entity_count', 0)}")
        print(f"  Raw event count:     {debug_info.get('raw_event_count', 0)}")
        print(f"  Ranked event count:   {debug_info.get('ranked_event_count', 0)}")
        print(f"  Top-K count:         {debug_info.get('top_k_count', 0)}")

    # 5. Generated answer
    print("\n💬 Generated Answer")
    print("-" * 80)
    print(result.get('answer', '(No answer generated)'))

    print("\n" + "=" * 80)


def search_demo():
    """Run search demo with sample queries."""
    print("=" * 80)
    print("LiMem LTM Search Demo - Full Debug Mode")
    print("=" * 80)

    # Initialize database connection
    print("\n📁 Connecting to database...")
    conn = open_connection(DB_PATH)
    init_db(conn)
    print("✅ Database connected")

    # Display database statistics
    stats = get_db_stats(conn)
    print_db_stats(stats)

    # Initialize LTM system (for reference)
    ltm = ResearchLTM(conn)

    # Initialize Searcher
    config = RetrievalConfig(
        default_top_k=5,
        lambda_param=0.01,
        max_entities=10,
        enable_vector_match=True,
    )
    searcher = LTMSearcher(conn, config)
    print("✅ LTMSearcher initialized\n")

    # Sample queries for testing
    test_queries = [
        "放孩子最喜欢的那个动画片",
        "我通常早上听什么音乐？",
        "回家时一般会怎么走？",
    ]

    print("-" * 80)
    print("Running Search Tests")
    print("-" * 80)

    for query in test_queries:
        # Execute search with debug info
        result = searcher.search_debug(query, top_k=5)
        print_detailed_search_result(query, result, conn)

    print("\n✅ Demo complete!")


def interactive_search():
    """Interactive search mode for manual testing."""
    print("=" * 80)
    print("LiMem LTM Interactive Search - Full Debug Mode")
    print("=" * 80)
    print("Type 'quit' to exit\n")

    # Initialize database connection
    conn = open_connection(DB_PATH)
    init_db(conn)

    # Display database statistics
    stats = get_db_stats(conn)
    print_db_stats(stats)

    # Initialize Searcher
    config = RetrievalConfig(
        default_top_k=5,
        lambda_param=0.01,
        enable_vector_match=True,
    )
    searcher = LTMSearcher(conn, config)

    while True:
        query = input("\n🔍 Enter your query: ").strip()
        if not query:
            continue
        if query.lower() in {"quit", "exit", "q"}:
            print("👋 Goodbye!")
            break

        result = searcher.search_debug(query, top_k=5)
        print_detailed_search_result(query, result, conn)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="LiMem LTM Search Demo")
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Run in interactive mode",
    )
    args = parser.parse_args()

    if args.interactive:
        interactive_search()
    else:
        search_demo()
