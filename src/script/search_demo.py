# -*- coding: utf-8 -*-
"""Manual search demo for dynamic evolution LTM."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem import create_ltm


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search demo for dynamic LTM")
    parser.add_argument(
        "--db-path",
        default=os.path.join(PROJECT_ROOT, "DB", "dynamic_trips.kz"),
        help="Kuzu DB path",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Top-K for retrieval",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="Use online extraction/answer mode (default offline)",
    )
    parser.add_argument(
        "--answer",
        action="store_true",
        help="Generate answer text",
    )
    parser.add_argument(
        "--query",
        default="",
        help="One-shot query. Empty means interactive mode unless --demo is set.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run built-in demo queries",
    )
    parser.add_argument(
        "--no-evolution",
        action="store_true",
        help="Skip evolution-aware compressed retrieval output",
    )
    return parser.parse_args()


def _demo_queries() -> list[str]:
    return [
        "用户在开会场景下通常会怎么设置车机？",
        "用户和导航相关的行为模式是什么？",
        "播放媒体和勿扰模式有什么共同情景？",
        "用户困了时系统通常怎么响应？",
        "与儿童安抚相关的记忆有哪些？",
    ]


def _print_stats(ltm) -> None:
    stats = ltm.get_stats()
    print("\n=== DB Stats ===")
    for key in sorted(stats.keys()):
        print(f"{key:>20}: {stats[key]}")


def _print_classic_result(result: Any, top_k: int) -> None:
    print(f"Entities: {result.entities}")
    print(f"Ranked events: {len(result.ranked_events)}")
    print(f"Top-{top_k}:")
    for idx, ev in enumerate(result.top_k_events[:top_k], 1):
        print(
            f"  [{idx}] {ev.event_id[:20]}.."
            f" w={ev.weight:.4f}"
            f" c_valid={ev.c_valid}"
            f" summary={ev.summary[:80]}"
        )
    if result.answer:
        print("Answer:")
        print(result.answer)


def _print_evolution_rows(rows: list[dict[str, Any]], top_k: int) -> None:
    print(f"Evolution-aware Top-{top_k}:")
    for idx, row in enumerate(rows[:top_k], 1):
        print(
            f"  [{idx}] {str(row.get('event_id', ''))[:20]}.."
            f" score={float(row.get('evolution_score', 0.0)):.4f}"
            f" sim={float(row.get('event_similarity', 0.0)):.3f}"
            f" ctx={float(row.get('context_match', 0.0)):.3f}"
        )
        summary = str(row.get("summary", "") or "")
        if summary:
            print(f"       summary={summary[:100]}")
        ctx = row.get("compressed_contexts") or []
        if ctx:
            print(f"       contexts={ctx[:2]}")


def run_query(ltm, query: str, top_k: int, gen_answer: bool, show_evolution: bool) -> None:
    print("\n" + "=" * 90)
    print(f"Query: {query}")
    print("=" * 90)
    result = ltm.search(query=query, top_k=top_k, generate_answer=gen_answer)
    _print_classic_result(result, top_k=top_k)
    if show_evolution:
        rows = ltm.retrieve_memories(query=query, top_k=top_k)
        _print_evolution_rows(rows, top_k=top_k)


def interactive_loop(ltm, top_k: int, gen_answer: bool, show_evolution: bool) -> None:
    print("\nEnter query (type 'exit' to quit)")
    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExit.")
            break
        if not q:
            continue
        if q.lower() in {"exit", "quit", "q"}:
            break
        run_query(ltm, q, top_k=top_k, gen_answer=gen_answer, show_evolution=show_evolution)


def main() -> None:
    args = _parse_args()
    if not os.path.exists(args.db_path):
        raise FileNotFoundError(f"DB not found: {args.db_path}. Build first with build_ltm_from_trips.py")

    ltm = create_ltm(
        db_path=args.db_path,
        config={
            "offline_mode": not args.online,
            "enable_dynamic_evolution": True,
            "append_first_mode": True,
            "generate_answer": args.answer,
            "search_top_k": args.top_k,
        },
    )
    print(f"Connected DB: {args.db_path}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _print_stats(ltm)

    show_evolution = not args.no_evolution
    if args.query:
        run_query(
            ltm=ltm,
            query=args.query,
            top_k=args.top_k,
            gen_answer=args.answer,
            show_evolution=show_evolution,
        )
        return

    if args.demo:
        for q in _demo_queries():
            run_query(
                ltm=ltm,
                query=q,
                top_k=args.top_k,
                gen_answer=args.answer,
                show_evolution=show_evolution,
            )
        return

    interactive_loop(
        ltm=ltm,
        top_k=args.top_k,
        gen_answer=args.answer,
        show_evolution=show_evolution,
    )


if __name__ == "__main__":
    main()
