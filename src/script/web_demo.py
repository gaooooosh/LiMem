#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Start the LiMem Web Search Demo.

Usage:
    python src/script/web_demo.py
    python src/script/web_demo.py --port 8080
    python src/script/web_demo.py --host 127.0.0.1 --port 3000
"""

import argparse
import os
import sys

# Add src to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem.web_api import run_server


def main():
    parser = argparse.ArgumentParser(description="LiMem Web Search Demo")
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind the server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to bind the server (default: 8000)",
    )
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print("  LiMem 长期记忆检索系统 - Web界面")
    print(f"{'='*60}")
    print(f"\n  服务地址: http://{args.host}:{args.port}")
    print(f"  API文档:  http://{args.host}:{args.port}/docs")
    print(f"\n  按 Ctrl+C 停止服务")
    print(f"{'='*60}\n")

    run_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
