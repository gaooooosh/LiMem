# -*- coding: utf-8 -*-
"""Build dynamic evolution LTM database from trips.json."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem import create_ltm, migrate_to_dynamic_graph
from script.trips_loader import load_trips_episodes


def _to_report_path(output_dir: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(output_dir, f"build_trips_report_{ts}.json")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build dynamic LTM from trips.json")
    parser.add_argument(
        "--trips-path",
        default=os.path.join(PROJECT_ROOT, "trips.json"),
        help="Path to trips.json",
    )
    parser.add_argument(
        "--db-path",
        default=os.path.join(PROJECT_ROOT, "DB", "dynamic_trips.kz"),
        help="Output Kuzu DB path",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Max records to ingest (0 means all)",
    )
    parser.add_argument(
        "--buckets",
        default="",
        help="Comma-separated bucket names to include (empty means all)",
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
        "--skip-migration",
        action="store_true",
        help="Skip compatibility migration stage",
    )
    parser.add_argument(
        "--run-consolidation",
        action="store_true",
        help="Run one consolidation pass after build",
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
        os.remove(args.db_path)

    include_buckets = {x.strip() for x in args.buckets.split(",") if x.strip()} or None
    episodes = load_trips_episodes(
        path=args.trips_path,
        max_items=args.max_items,
        include_buckets=include_buckets,
    )
    print(f"Loaded episodes: {len(episodes)} from {args.trips_path}")
    if not episodes:
        print("No episodes loaded, stop.")
        return

    ltm = create_ltm(
        db_path=args.db_path,
        config={
            "offline_mode": not args.online,
            "enable_dynamic_evolution": True,
            "append_first_mode": not args.legacy_merge,
            "generate_answer": False,
            "search_top_k": 5,
        },
    )

    errors = 0
    for idx, episode in enumerate(episodes, 1):
        try:
            ltm.ingest(episode)
        except Exception as ex:
            errors += 1
            print(f"[WARN] ingest failed @#{idx}: {ex}")
        if args.progress_every > 0 and idx % args.progress_every == 0:
            print(f"Ingested {idx}/{len(episodes)}")

    migration_dry = {}
    migration_run = {}
    if not args.skip_migration:
        dry_report = migrate_to_dynamic_graph(ltm.store, dry_run=True)
        run_report = migrate_to_dynamic_graph(ltm.store, dry_run=False)
        migration_dry = dry_report.to_dict()
        migration_run = run_report.to_dict()
        print("Migration dry-run:", migration_dry)
        print("Migration applied:", migration_run)

    consolidation_report: dict[str, Any] = {}
    if args.run_consolidation:
        consolidation_report = ltm.run_consolidation()
        print("Consolidation report:", consolidation_report)

    stats = ltm.get_stats()
    print("Stats:", stats)

    report = {
        "trips_path": args.trips_path,
        "db_path": args.db_path,
        "episodes_loaded": len(episodes),
        "ingest_errors": errors,
        "offline_mode": not args.online,
        "append_first_mode": not args.legacy_merge,
        "migration_dry_run": migration_dry,
        "migration_applied": migration_run,
        "consolidation_report": consolidation_report,
        "stats": stats,
    }
    report_path = _to_report_path(args.output_dir)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Build report saved: {report_path}")


if __name__ == "__main__":
    main()
