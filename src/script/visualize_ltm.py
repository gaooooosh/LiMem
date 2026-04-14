#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LiMem 图可视化命令行工具

使用方式:
    # 基本用法
    python -m limem.visualization.scripts.visualize_ltm --db ./my_memory.kz

    # 指定输出路径
    python -m limem.visualization.scripts.visualize_ltm --db ./my_memory.kz -o ./viz/graph.html

    # 自定义参数
    python -m limem.visualization.scripts.visualize_ltm --db ./my_memory.kz --max-events 50 --max-entities 30

    # 启动本地服务器查看
    python -m limem.visualization.scripts.visualize_ltm --db ./my_memory.kz --serve
"""

from __future__ import annotations

import argparse
import http.server
import os
import shlex
import socketserver
import subprocess
import sys
import webbrowser
from pathlib import Path

# 添加 src 目录到路径
SRC_DIR = Path(__file__).parent.parent.parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from limem.visualization import GraphVisualizer, VisualizationConfig


def _open_browser_quietly(url: str) -> bool:
    """Best-effort browser launch without leaking helper logs to stdout."""
    browser_cmd = os.environ.get("BROWSER")
    if browser_cmd:
        try:
            cmd = shlex.split(browser_cmd)
            if cmd:
                if any("%s" in part for part in cmd):
                    cmd = [part.replace("%s", url) for part in cmd]
                else:
                    cmd.append(url)
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return True
        except Exception:
            pass

    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(
        description="LiMem 图数据库可视化工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --db ./DB/memory.kz                    # 生成可视化到默认位置
  %(prog)s --db ./DB/memory.kz -o ./viz/graph.html  # 指定输出路径
  %(prog)s --db ./DB/memory.kz --serve            # 生成并启动服务器
  %(prog)s --db ./DB/memory.kz --stats            # 仅显示统计信息
        """,
    )

    # 必需参数
    parser.add_argument(
        "--db",
        type=str,
        required=True,
        help="Kuzu 数据库路径",
    )

    # 输出选项
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="输出 HTML 文件路径 (默认: <db_dir>/viz/graph_topology.html)",
    )

    # 节点限制
    parser.add_argument(
        "--max-events",
        type=int,
        default=100,
        help="最大事件节点数 (默认: 100)",
    )
    parser.add_argument(
        "--max-entities",
        type=int,
        default=50,
        help="最大实体节点数 (默认: 50)",
    )
    parser.add_argument(
        "--max-contexts",
        type=int,
        default=20,
        help="最大上下文节点数 (默认: 20)",
    )

    # 视图选项
    parser.add_argument(
        "--title",
        type=str,
        default="LiMem 图拓扑可视化",
        help="页面标题",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1200,
        help="视图宽度 (默认: 1200)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=800,
        help="视图高度 (默认: 800)",
    )

    # 功能选项
    parser.add_argument(
        "--serve",
        action="store_true",
        help="生成后启动本地 HTTP 服务器",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="HTTP 服务器端口 (默认: 8765)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="不自动打开浏览器",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="仅显示图统计信息，不生成可视化",
    )
    parser.add_argument(
        "--cdn",
        action="store_true",
        help="使用 D3.js CDN 而非本地文件",
    )

    args = parser.parse_args()

    # 检查数据库是否存在
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"错误: 数据库不存在: {db_path}")
        sys.exit(1)

    # 配置
    config = VisualizationConfig(
        max_events=args.max_events,
        max_entities=args.max_entities,
        max_contexts=args.max_contexts,
        width=args.width,
        height=args.height,
    )

    # 创建可视化器
    visualizer = GraphVisualizer(db_path, config=config)

    # 仅显示统计
    if args.stats:
        print("=" * 50)
        print("LiMem 图数据库统计")
        print("=" * 50)
        stats = visualizer.get_stats()
        for key, value in stats.items():
            if isinstance(value, dict):
                print(f"\n{key}:")
                for k, v in value.items():
                    print(f"  {k}: {v}")
            else:
                print(f"{key}: {value}")
        return

    # 确定输出路径
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = db_path.parent / "viz" / "graph_topology.html"

    # 生成可视化
    print("=" * 50)
    print("LiMem 图可视化")
    print("=" * 50)
    print(f"数据库: {db_path}")
    print(f"输出: {output_path}")

    result_path = visualizer.export_html(
        output_path,
        title=args.title,
        include_d3_local=not args.cdn,
        copy_d3_library=not args.cdn,
    )

    print(f"\n✓ 可视化已生成: {result_path}")

    # 启动服务器
    if args.serve:
        viz_dir = Path(result_path).parent
        os.chdir(viz_dir)

        port = args.port
        handler = http.server.SimpleHTTPRequestHandler

        print(f"\n启动 HTTP 服务器: http://localhost:{port}")
        print("按 Ctrl+C 停止")

        if not args.no_browser:
            url = f"http://localhost:{port}/{Path(result_path).name}"
            if not _open_browser_quietly(url):
                print(f"浏览器未自动打开，请手动访问: {url}")

        with socketserver.TCPServer(("", port), handler) as httpd:
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n服务器已停止")
    else:
        print(f"\n查看方式:")
        print(f"  1. 直接打开: file://{result_path}")
        print(f"  2. 或启动服务器: python -m http.server 8765 --directory {Path(result_path).parent}")


if __name__ == "__main__":
    main()
