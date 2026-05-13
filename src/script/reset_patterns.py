# -*- coding: utf-8 -*-
"""一次性脚本：清空指定 Kuzu DB 中所有 Pattern 节点与 HAS_PATTERN 边。

用法：
    python src/script/reset_patterns.py --db-path ./DB/service.kz
    python src/script/reset_patterns.py --db-path ./DB/service.kz --yes  # 跳过确认

适用场景：
- 测试/开发环境重置 pattern 数据；
- 上线"内联注册 + 批量注册"前对历史 pattern 数据做归零。

注意：本脚本会硬删除节点，无法撤销。
"""

from __future__ import annotations

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import kuzu  # type: ignore  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Drop all Pattern nodes & HAS_PATTERN edges.")
    parser.add_argument(
        "--db-path",
        required=True,
        help="Kuzu DB path, e.g. ./DB/service.kz",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation.",
    )
    return parser.parse_args()


def _confirm(db_path: str) -> bool:
    print(f"⚠️  即将删除 {db_path} 中所有 Pattern 节点与 HAS_PATTERN 关系。")
    answer = input("输入 'YES' 继续，其他任意键取消: ").strip()
    return answer == "YES"


def main() -> int:
    args = _parse_args()
    db_path = os.path.abspath(args.db_path)
    if not os.path.exists(db_path):
        print(f"DB path not found: {db_path}", file=sys.stderr)
        return 2
    if not args.yes and not _confirm(db_path):
        print("Aborted.")
        return 1

    db = kuzu.Database(db_path)
    conn = kuzu.Connection(db)

    # 计数（仅供日志）
    resp = conn.execute("MATCH (p:Pattern) RETURN count(*)")
    pattern_before = resp.get_next()[0] if resp.has_next() else 0
    resp = conn.execute("MATCH ()-[r:HAS_PATTERN]->() RETURN count(*)")
    edge_before = resp.get_next()[0] if resp.has_next() else 0
    print(f"before: pattern_count={pattern_before}, has_pattern_edge_count={edge_before}")

    # 删除：先断边再删节点（保险）
    conn.execute("MATCH ()-[r:HAS_PATTERN]->() DELETE r")
    conn.execute("MATCH (p:Pattern) DETACH DELETE p")

    resp = conn.execute("MATCH (p:Pattern) RETURN count(*)")
    pattern_after = resp.get_next()[0] if resp.has_next() else 0
    resp = conn.execute("MATCH ()-[r:HAS_PATTERN]->() RETURN count(*)")
    edge_after = resp.get_next()[0] if resp.has_next() else 0
    print(f"after : pattern_count={pattern_after}, has_pattern_edge_count={edge_after}")

    if pattern_after == 0 and edge_after == 0:
        print("✓ Reset done.")
        return 0
    print("⚠ Reset incomplete, residual rows remain.", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
