# -*- coding: utf-8 -*-
"""Run interactive trips debugger web app."""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem.trips_debugger import TripsDebuggerConfig, create_trips_debugger_app


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LiMem trips debugger demo")
    parser.add_argument(
        "--trips-path",
        default=os.path.join(PROJECT_ROOT, "trips.json"),
        help="Path to trips.json",
    )
    parser.add_argument(
        "--db-path",
        default=os.path.join(PROJECT_ROOT, "DB", "trips_debugger.kz"),
        help="Kuzu DB path used by the interactive demo",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8011, help="Port to bind")
    parser.add_argument(
        "--online",
        action="store_true",
        help="Use online extraction/embedding mode instead of offline mode",
    )
    parser.add_argument(
        "--legacy-merge",
        action="store_true",
        help="Disable append-first mode",
    )
    parser.add_argument(
        "--snapshot-limit",
        type=int,
        default=80,
        help="How many nodes of each type to expose in the live graph snapshot",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=0,
        help="Limit loaded trips episodes (0 means all)",
    )
    parser.add_argument(
        "--buckets",
        default="",
        help="Comma-separated bucket names to include",
    )
    parser.add_argument(
        "--allow-auto-consolidation",
        action="store_true",
        help="Enable auto consolidation while interactively writing records",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    include_buckets = {item.strip() for item in args.buckets.split(",") if item.strip()} or None
    app = create_trips_debugger_app(
        TripsDebuggerConfig(
            trips_path=args.trips_path,
            db_path=args.db_path,
            offline_mode=not args.online,
            append_first_mode=not args.legacy_merge,
            snapshot_limit=args.snapshot_limit,
            include_buckets=include_buckets,
            max_items=args.max_items,
            enable_auto_consolidation=args.allow_auto_consolidation,
        )
    )
    print(f"Trips debugger running at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
