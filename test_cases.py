#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LiMem 系统测试脚本

从理想车主的日常使用角度设计测试用例，覆盖所有场景。
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(__file__)
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from limem.config import DB_PATH
from limem.db import open_connection
from limem.search import LTMSearcher


# 测试用例定义
# 每个测试用例包含：query, expected_keywords (期望找到的关键信息), scenario (场景描述)
TEST_CASES = [
    # ========== 场景1: 长城相关 (input_records_1_mock) ==========
    {
        "query": "我之前想去看长城，记得吗？",
        "expected_keywords": ["长城", "八达岭", "旅游", "想去"],
        "scenario": "回忆用户表达想去长城旅游的意愿",
        "category": "长城相关"
    },
    {
        "query": "之前问过长城有多长吗？",
        "expected_keywords": ["长城", "2.1万公里", "长度"],
        "scenario": "回忆用户询问长城长度",
        "category": "长城相关"
    },
    {
        "query": "帮我找长城的历史纪录片",
        "expected_keywords": ["长城", "历史", "纪录片", "播放"],
        "scenario": "检索长城纪录片播放记录",
        "category": "长城相关"
    },
    {
        "query": "导航去八达岭长城",
        "expected_keywords": ["八达岭长城", "导航", "70分钟"],
        "scenario": "回忆导航到八达岭长城",
        "category": "长城相关"
    },

    # ========== 场景2: 医院就医 (input_records_2_mock) ==========
    {
        "query": "我今天身体不太舒服，上次去医院是去哪了？",
        "expected_keywords": ["北京协和医院", "身体不舒服", "医院"],
        "scenario": "回忆用户身体不适并导航到医院",
        "category": "医院就医"
    },
    {
        "query": "我记得原本打算去公司，后来改去哪了？",
        "expected_keywords": ["北京协和医院", "公司", "更改", "导航"],
        "scenario": "回忆更改导航目的地",
        "category": "医院就医"
    },

    # ========== 场景3: 传统节日 (input_records_3_mock) ==========
    {
        "query": "端午节除了纪念屈原还有哪些习俗？",
        "expected_keywords": ["端午节", "屈原", "赛龙舟", "吃粽子", "挂艾草"],
        "scenario": "回忆端午节习俗问答",
        "category": "传统节日"
    },
    {
        "query": "粽子和端午节有什么关系？",
        "expected_keywords": ["粽子", "端午节", "纪念屈原"],
        "scenario": "回忆粽子与端午节的关系",
        "category": "传统节日"
    },
    {
        "query": "中秋节是怎么来的？",
        "expected_keywords": ["中秋节", "祭月活动", "团圆", "来历"],
        "scenario": "回忆中秋节起源",
        "category": "传统节日"
    },
    {
        "query": "帮我找关于端午节文化的音频",
        "expected_keywords": ["端午节", "文化讲解", "音频", "播放"],
        "scenario": "回忆播放端午节文化音频",
        "category": "传统节日"
    },
    {
        "query": "播放中秋节的文化讲解",
        "expected_keywords": ["中秋节", "文化讲解", "音频", "喜马拉雅"],
        "scenario": "回忆播放中秋节文化音频",
        "category": "传统节日"
    },

    # ========== 场景4: 书店咖啡馆 (input_records_4_mock) ==========
    {
        "query": "推荐一下附近的咖啡馆",
        "expected_keywords": ["北京图书大厦", "咖啡馆", "COSTA COFFEE", "漫咖啡"],
        "scenario": "回忆咖啡馆推荐",
        "category": "书店咖啡馆"
    },
    {
        "query": "我之前是不是想去三联书店？后来改去哪了？",
        "expected_keywords": ["北京图书大厦", "三联书店", "导航"],
        "scenario": "回忆更改书店目的地",
        "category": "书店咖啡馆"
    },

    # ========== 场景5: 导航取消 (input_records_5_real) ==========
    {
        "query": "之前导航去杭州大厦，后来去了吗？",
        "expected_keywords": ["杭州大厦", "导航", "不想去了", "取消"],
        "scenario": "回忆导航取消操作",
        "category": "导航取消"
    },

    # ========== 场景6: 音乐播放 (input_records_6_real) ==========
    {
        "query": "之前播放过什么歌？",
        "expected_keywords": ["还是分开", "张叶蕾", "网易云音乐", "播放"],
        "scenario": "回忆音乐播放记录",
        "category": "音乐播放"
    },
    {
        "query": "把音量调大一点",
        "expected_keywords": ["音量", "25", "大点声音", "媒体"],
        "scenario": "回忆音量调整",
        "category": "音乐播放"
    },
    {
        "query": "给我们放首歌",
        "expected_keywords": ["放歌", "音乐", "播放"],
        "scenario": "回忆音乐播放请求",
        "category": "音乐播放"
    },

    # ========== 场景7: 电池预热 (input_records_7_real) ==========
    {
        "query": "充电前需要打开电池保温吗？",
        "expected_keywords": ["电池保温", "充电", "天气", "预热"],
        "scenario": "回忆电池保温询问",
        "category": "电池预热"
    },
    {
        "query": "电池预热有什么作用？",
        "expected_keywords": ["电池预热", "充电效率", "电池温度", "电池寿命"],
        "scenario": "回忆电池预热作用",
        "category": "电池预热"
    },
    {
        "query": "我打开电池预热了吗？",
        "expected_keywords": ["电池预热", "打开", "充电管理"],
        "scenario": "回忆电池预热状态",
        "category": "电池预热"
    },

    # ========== 跨场景关联查询 ==========
    {
        "query": "我最近对历史文化感兴趣，问了些什么？",
        "expected_keywords": ["长城", "端午节", "中秋节", "历史"],
        "scenario": "跨场景检索历史文化相关记忆",
        "category": "跨场景"
    },
    {
        "query": "我播放过哪些文化类的音频？",
        "expected_keywords": ["长城纪录片", "端午节音频", "中秋节音频"],
        "scenario": "检索所有文化类音频播放记录",
        "category": "跨场景"
    },
    {
        "query": "我导航去过哪些地方？",
        "expected_keywords": ["八达岭长城", "北京协和医院", "北京图书大厦", "杭州大厦"],
        "scenario": "检索所有导航记录",
        "category": "跨场景"
    },
]


def evaluate_relevance(ranked_events, expected_keywords):
    """评估检索结果的相关性

    Args:
        ranked_events: 检索到的事件列表
        expected_keywords: 期望找到的关键词列表

    Returns:
        (relevance_score, found_keywords, details)
    """
    if not ranked_events:
        return 0.0, [], {"reason": "没有检索到任何事件"}

    # 收集所有事件文本
    all_text = ""
    for event in ranked_events:
        all_text += event.summary + " "
        if event.action:
            all_text += event.action + " "
        if event.causality:
            all_text += event.causality + " "

    # 检查每个关键词
    found_keywords = []
    for keyword in expected_keywords:
        if keyword.lower() in all_text.lower():
            found_keywords.append(keyword)

    # 计算相关性得分
    relevance_score = len(found_keywords) / len(expected_keywords) if expected_keywords else 0.0

    details = {
        "total_keywords": len(expected_keywords),
        "found_keywords": len(found_keywords),
        "missing_keywords": [kw for kw in expected_keywords if kw not in found_keywords],
        "top_event_summary": ranked_events[0].summary if ranked_events else "None",
    }

    return relevance_score, found_keywords, details


def run_test_case(searcher, test_case, test_id):
    """运行单个测试用例

    Args:
        searcher: LTMSearcher 实例
        test_case: 测试用例字典
        test_id: 测试编号

    Returns:
        测试结果字典
    """
    query = test_case["query"]
    expected_keywords = test_case["expected_keywords"]
    scenario = test_case["scenario"]
    category = test_case["category"]

    print(f"\n{'='*80}")
    print(f"测试 #{test_id}: {scenario}")
    print(f"类别: {category}")
    print(f"查询: {query}")
    print(f"期望关键词: {expected_keywords}")
    print(f"{'='*80}")

    # 执行搜索
    result = searcher.search_debug(query)

    # 评估结果
    ranked_events = result.get("ranked_events", [])
    relevance_score, found_keywords, details = evaluate_relevance(
        ranked_events, expected_keywords
    )

    # 判断是否通过
    passed = relevance_score >= 0.5  # 至少找到50%的关键词

    # 打印结果
    print(f"\n📊 检索结果:")
    print(f"  - 检索到事件数: {len(ranked_events)}")
    print(f"  - Top-1 事件: {details['top_event_summary']}")
    print(f"\n🎯 相关性评估:")
    print(f"  - 找到关键词: {found_keywords}")
    print(f"  - 缺失关键词: {details['missing_keywords']}")
    print(f"  - 相关性得分: {relevance_score:.2%}")
    print(f"\n✅ 测试结果: {'通过 ✓' if passed else '失败 ✗'}")

    # 打印生成的答案
    answer = result.get("answer", "")
    if answer:
        print(f"\n💬 系统回答:")
        print(f"  {answer[:200]}...")

    return {
        "test_id": test_id,
        "query": query,
        "scenario": scenario,
        "category": category,
        "passed": passed,
        "relevance_score": relevance_score,
        "found_keywords": found_keywords,
        "missing_keywords": details["missing_keywords"],
        "ranked_event_count": len(ranked_events),
        "top_event_summary": details["top_event_summary"],
        "answer": answer,
    }


def main():
    """主测试函数"""
    print("=" * 80)
    print("LiMem 系统全面测试")
    print("=" * 80)

    # 打开数据库连接
    conn = open_connection(DB_PATH)
    searcher = LTMSearcher(conn)

    # 运行所有测试
    results = []
    for i, test_case in enumerate(TEST_CASES, 1):
        result = run_test_case(searcher, test_case, i)
        results.append(result)

    # 汇总统计
    print("\n" + "=" * 80)
    print("测试汇总")
    print("=" * 80)

    total_tests = len(results)
    passed_tests = sum(1 for r in results if r["passed"])
    failed_tests = total_tests - passed_tests
    avg_relevance = sum(r["relevance_score"] for r in results) / total_tests if total_tests > 0 else 0

    print(f"\n📈 总体统计:")
    print(f"  - 总测试数: {total_tests}")
    print(f"  - 通过: {passed_tests} ({passed_tests/total_tests:.1%})")
    print(f"  - 失败: {failed_tests} ({failed_tests/total_tests:.1%})")
    print(f"  - 平均相关性: {avg_relevance:.2%}")

    # 按类别统计
    category_stats = {}
    for result in results:
        cat = result["category"]
        if cat not in category_stats:
            category_stats[cat] = {"total": 0, "passed": 0}
        category_stats[cat]["total"] += 1
        if result["passed"]:
            category_stats[cat]["passed"] += 1

    print(f"\n📊 分类统计:")
    for cat, stats in sorted(category_stats.items()):
        pass_rate = stats["passed"] / stats["total"] if stats["total"] > 0 else 0
        print(f"  - {cat}: {stats['passed']}/{stats['total']} ({pass_rate:.1%})")

    # 列出失败的测试
    failed_results = [r for r in results if not r["passed"]]
    if failed_results:
        print(f"\n❌ 失败的测试:")
        for r in failed_results:
            print(f"  - #{r['test_id']}: {r['scenario']}")
            print(f"    查询: {r['query']}")
            print(f"    缺失关键词: {r['missing_keywords']}")
            print(f"    相关性得分: {r['relevance_score']:.2%}")

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)


if __name__ == "__main__":
    main()
