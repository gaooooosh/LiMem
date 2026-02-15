#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库诊断脚本"""

import os
import sys

PROJECT_ROOT = os.path.dirname(__file__)
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem.config import DB_PATH
from limem.db import open_connection


def diagnose_database():
    """诊断数据库内容"""
    conn = open_connection(DB_PATH)

    print("=" * 80)
    print("数据库诊断")
    print("=" * 80)

    # 1. 查询所有事件
    print("\n📊 所有事件:")
    print("-" * 80)
    resp = conn.execute("MATCH (e:Event) RETURN e.id, e.summary, e.last_active ORDER BY e.last_active DESC")
    event_count = 0
    while resp.has_next():
        event_id, summary, last_active = resp.get_next()
        event_count += 1
        print(f"{event_count}. [{event_id[:16]}...] {summary}")
        print(f"   Last active: {last_active}")
    print(f"\n总计: {event_count} 个事件")

    # 2. 查询所有实体
    print("\n🧩 所有实体:")
    print("-" * 80)
    resp = conn.execute("MATCH (en:Entity) RETURN en.id, en.type ORDER BY en.id")
    entity_count = 0
    while resp.has_next():
        entity_id, entity_type = resp.get_next()
        entity_count += 1
        print(f"{entity_count}. [{entity_id}] (类型: {entity_type})")
    print(f"\n总计: {entity_count} 个实体")

    # 3. 查询事件-实体关系
    print("\n🔗 事件-实体关系 (INVOLVES):")
    print("-" * 80)
    resp = conn.execute("""
        MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
        RETURN e.summary, en.id, r.c_valid, r.t_valid
        ORDER BY r.c_valid DESC
        LIMIT 30
    """)
    relation_count = 0
    while resp.has_next():
        summary, entity_id, c_valid, t_valid = resp.get_next()
        relation_count += 1
        print(f"{relation_count}. 事件: {summary[:60]}...")
        print(f"   -> 实体: {entity_id}, c_valid={c_valid}, t_valid={t_valid}")
    print(f"\n总计 (显示前30): {relation_count} 条关系")

    # 4. 检查每个事件有多少实体关联
    print("\n📈 每个事件的实体数量:")
    print("-" * 80)
    resp = conn.execute("""
        MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
        WITH e, count(en) as entity_count
        RETURN e.summary, entity_count
        ORDER BY entity_count DESC
    """)
    while resp.has_next():
        summary, entity_count = resp.get_next()
        print(f"  {entity_count} 个实体 -> {summary[:60]}...")

    # 5. 检查是否有孤立实体（没有事件的实体）
    print("\n⚠️  孤立实体（没有INVOLVES关系）:")
    print("-" * 80)
    resp = conn.execute("""
        MATCH (en:Entity)
        WHERE NOT (en)<-[:INVOLVES]-(:Event)
        RETURN en.id, en.type
    """)
    isolated_count = 0
    while resp.has_next():
        entity_id, entity_type = resp.get_next()
        isolated_count += 1
        print(f"  {isolated_count}. [{entity_id}] (类型: {entity_type})")
    if isolated_count == 0:
        print("  (无)")
    print(f"总计: {isolated_count} 个孤立实体")

    # 6. 检查事件摘要的详细内容
    print("\n📝 事件摘要详细内容:")
    print("-" * 80)
    resp = conn.execute("MATCH (e:Event) RETURN e.summary, e.action, e.causality ORDER BY e.last_active DESC")
    while resp.has_next():
        summary, action, causality = resp.get_next()
        print(f"\n摘要: {summary}")
        if action:
            print(f"动作: {action}")
        if causality:
            print(f"因果: {causality}")

    print("\n" + "=" * 80)
    print("诊断完成")
    print("=" * 80)


if __name__ == "__main__":
    diagnose_database()
