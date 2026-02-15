#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断失败测试的详细信息"""

import os
import sys

PROJECT_ROOT = os.path.dirname(__file__)
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem.config import DB_PATH
from limem.db import open_connection


def diagnose_failed_tests():
    """诊断失败测试的具体事件"""
    conn = open_connection(DB_PATH)

    print("=" * 80)
    print("失败测试诊断")
    print("=" * 80)

    # 测试 #15: 音乐播放记录
    print("\n🔍 测试 #15: 回忆音乐播放记录")
    print("-" * 80)
    print("查询: 之前播放过什么歌？")
    print("\n相关事件:")
    resp = conn.execute("""
        MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
        WHERE en.id IN ['张叶蕾', '还是分开', '网易云音乐', '歌', '播放']
        RETURN DISTINCT e.summary, e.action, e.causality, e.last_active
        ORDER BY e.last_active DESC
    """)
    while resp.has_next():
        summary, action, causality, last_active = resp.get_next()
        print(f"  - {summary}")
        if action:
            print(f"    动作: {action}")
        if causality:
            print(f"    因果: {causality}")

    # 测试 #19, #20: 电池预热
    print("\n🔍 测试 #19, #20: 电池预热相关")
    print("-" * 80)
    resp = conn.execute("""
        MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
        WHERE en.id IN ['电池预热', '充电', '充电效率', '电池温度', '电池寿命']
        RETURN DISTINCT e.summary, e.action, e.causality, e.last_active
        ORDER BY e.last_active DESC
    """)
    while resp.has_next():
        summary, action, causality, last_active = resp.get_next()
        print(f"  - {summary}")
        if action:
            print(f"    动作: {action}")
        if causality:
            print(f"    因果: {causality}")

    # 测试 #22: 文化类音频
    print("\n🔍 测试 #22: 文化类音频播放")
    print("-" * 80)
    resp = conn.execute("""
        MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
        WHERE en.id IN ['纪录片', '音频', '文化讲解', '播放']
        RETURN DISTINCT e.summary, e.action, COLLECT(en.id) as entities
        ORDER BY e.last_active DESC
        LIMIT 10
    """)
    while resp.has_next():
        summary, action, entities = resp.get_next()
        print(f"  - {summary}")
        if action:
            print(f"    动作: {action}")
        print(f"    实体: {entities}")

    print("\n" + "=" * 80)
    print("诊断完成")
    print("=" * 80)


if __name__ == "__main__":
    diagnose_failed_tests()
