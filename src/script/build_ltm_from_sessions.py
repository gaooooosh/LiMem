# -*- coding: utf-8 -*-
"""Build and visually debug dynamic evolution LTM from session_v1.json."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
import time
from datetime import datetime
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem import create_ltm
from limem.visualization import visualize_graph
from script.session_loader import load_and_split_session_episodes

# Reuse helpers from trips build script
from script.build_ltm_from_trips import (
    _combine_extraction_summaries,
    _capture_snapshot,
    _episode_to_dict,
    _ingest_result_to_dict,
    _render_html_report,
    _run_phase,
)


def _report_base_path(output_dir: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(output_dir, f"sessions_debug_report_{ts}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build dynamic LTM from session_v1.json with split-phase debugging"
    )
    parser.add_argument(
        "--sessions-path",
        default=os.path.join(PROJECT_ROOT, "session_v1.json"),
        help="Path to session_v1.json",
    )
    parser.add_argument(
        "--db-path",
        default=os.path.join(PROJECT_ROOT, "DB", "dynamic_sessions.kz"),
        help="Output Kuzu DB path",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Max records to ingest before split (0 means all)",
    )
    parser.add_argument(
        "--debug-max-items",
        type=int,
        default=0,
        help="Max records to replay in the debug phase (0 means all remaining)",
    )
    parser.add_argument(
        "--sources",
        default="",
        help="Comma-separated source types to include (empty means all)",
    )
    parser.add_argument(
        "--split-index",
        type=int,
        default=0,
        help="Absolute split index after sorting. 0 means use split-ratio.",
    )
    parser.add_argument(
        "--split-ratio",
        type=float,
        default=1.0,
        help="Base-phase ratio used when split-index is 0.",
    )
    parser.add_argument(
        "--no-sort",
        action="store_true",
        help="Keep original flattened order instead of sorting by timestamp.",
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="Use online LLM/embedding mode (default offline)",
    )
    parser.add_argument(
        "--legacy-merge",
        action="store_true",
        help="Disable append-first and use legacy merge strategy",
    )
    parser.add_argument(
        "--clear-db",
        action="store_true",
        help="Delete existing DB file before build",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="Print progress every N episodes",
    )
    parser.add_argument(
        "--debug-snapshot-every",
        type=int,
        default=10,
        help="Capture a debug snapshot every N debug-phase episodes",
    )
    parser.add_argument(
        "--snapshot-limit",
        type=int,
        default=12,
        help="How many events/contexts to include in each snapshot",
    )
    parser.add_argument(
        "--run-consolidation",
        action="store_true",
        help="Deprecated: bulk sessions build now always runs one final consolidation pass",
    )
    parser.add_argument(
        "--skip-visualize",
        action="store_true",
        help="Skip automatic graph visualization after build",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=8,
        help="Batch size for parallel LLM extraction (0 = serial mode)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.join(PROJECT_ROOT, "outputs"),
        help="Report output directory",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    os.makedirs(os.path.dirname(args.db_path), exist_ok=True)
    os.makedirs(args.output_dir, exist_ok=True)

    if args.clear_db and os.path.exists(args.db_path):
        import shutil

        if os.path.isdir(args.db_path):
            shutil.rmtree(args.db_path)
        else:
            os.remove(args.db_path)

    include_sources = {x.strip() for x in args.sources.split(",") if x.strip()} or None
    split_result = load_and_split_session_episodes(
        path=args.sessions_path,
        max_items=args.max_items,
        include_sources=include_sources,
        sort_by_time=not args.no_sort,
        split_index=args.split_index,
        split_ratio=args.split_ratio,
        debug_max_items=args.debug_max_items,
    )

    print(
        "Loaded episodes:",
        split_result.total_episodes,
        f"(base={len(split_result.base_episodes)}, debug={len(split_result.debug_episodes)})",
    )
    if split_result.total_episodes == 0:
        print("No episodes loaded, stop.")
        return

    ltm = create_ltm(
        db_path=args.db_path,
        config={
            "extractor_type": "unified",
            "offline_mode": not args.online,
            "enable_dynamic_evolution": True,
            "append_first_mode": not args.legacy_merge,
            "bulk_ingest_mode": True,
            "deferred_evolution": True,
            "llm_concurrency": 4,
        },
    )

    base_phase = _run_phase(
        ltm=ltm,
        episodes=split_result.base_episodes,
        phase_name="base",
        progress_every=args.progress_every,
        capture_every=0,
        snapshot_limit=args.snapshot_limit,
        batch_size=args.batch_size,
    )
    debug_phase = _run_phase(
        ltm=ltm,
        episodes=split_result.debug_episodes,
        phase_name="debug",
        progress_every=args.progress_every,
        capture_every=max(args.debug_snapshot_every, 0),
        snapshot_limit=args.snapshot_limit,
        batch_size=args.batch_size,
    )

    print("[post] Running consolidation (event merge + context merge + decay) ...")
    t0 = time.perf_counter()
    consolidation_report: dict[str, Any] = ltm.run_consolidation()
    print(f"[post] Consolidation done in {time.perf_counter() - t0:.1f}s: {consolidation_report}")

    print("[post] Gathering final stats and snapshot ...")
    final_stats = ltm.get_stats()
    final_snapshot = _capture_snapshot(ltm, args.snapshot_limit)
    print("[post] Stats:", final_stats)

    print("[post] Writing reports ...")
    report = {
        "sessions_path": args.sessions_path,
        "db_path": args.db_path,
        "extractor_type": "unified",
        "offline_mode": not args.online,
        "append_first_mode": not args.legacy_merge,
        "bulk_ingest_mode": True,
        "llm_concurrency": 4,
        "split": {
            "split_index": split_result.split_index,
            "split_ratio": split_result.split_ratio,
            "total_episodes": split_result.total_episodes,
            "base_episodes": len(split_result.base_episodes),
            "debug_episodes": len(split_result.debug_episodes),
        },
        "base_phase": base_phase,
        "debug_phase": debug_phase,
        "extraction_summary": _combine_extraction_summaries(
            base_phase.get("extraction_summary", {}),
            debug_phase.get("extraction_summary", {}),
        ),
        "consolidation_report": consolidation_report,
        "final_stats": final_stats,
        "final_snapshot": final_snapshot,
    }

    base_path = _report_base_path(args.output_dir)
    json_path = base_path + ".json"
    html_path = base_path + ".html"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(_render_html_report(report))
    print(f"Debug report saved: {json_path}")
    print(f"Visual report saved: {html_path}")

    # Auto-generate graph visualization
    if not args.skip_visualize:
        print("[post] Generating graph visualization ...")
        viz_path = os.path.join(args.output_dir, "sessions_graph.html")
        try:
            out = visualize_graph(
                db_path=args.db_path,
                output_path=viz_path,
                title="LiMem Session 图拓扑可视化",
            )
            print(f"Graph visualization saved: {out}")
        except Exception as ex:
            print(f"Graph visualization failed: {ex}")

    print("[done] Build complete.")


if __name__ == "__main__":
    main()
