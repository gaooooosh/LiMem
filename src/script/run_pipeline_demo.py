# -*- coding: utf-8 -*-
"""Run pipeline demo web app - Algorithm data flow visualizer."""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from script.pipeline_demo import PipelineDemoConfig, create_pipeline_demo_app


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LiMem pipeline demo visualizer")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8012, help="Port to bind")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    app = create_pipeline_demo_app(PipelineDemoConfig())
    print(f"Pipeline demo running at http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
