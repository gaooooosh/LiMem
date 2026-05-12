# -*- coding: utf-8 -*-
"""端到端：注册实体（重要实体）算法验证。

按照 plan §8 覆盖四个场景：
  1. 精确名链接（Case A）
  2. 向量高分链接（Stage 2，Case A）
  3. 历史合并（Case B）
  4. 保守缺省（Stage 3 不确定）

执行方式：
    PYTHONPATH=src python3 tests/test_registered_entity_e2e.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

# 让脚本可在仓库根执行
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# 装载 .env（DashScope key 等）
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv(ROOT / ".env")
except Exception:
    pass

# 覆盖 .env 中的容器路径，避免在本地宿主环境写 /app/outputs/...
# 必须在 limem 任何子模块 import 前完成，因为 config.py 读取一次性环境变量。
_LOG_DIR = Path(tempfile.gettempdir()) / "limem_reg_e2e_logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
os.environ["CONSOLIDATION_LOG_PATH"] = str(_LOG_DIR / "consolidation_log.jsonl")
os.environ["EVENT_MERGE_TRACE_LOG_PATH"] = str(_LOG_DIR / "event_merge_trace.jsonl")
# 关闭 SOCKS，避免 httpx 抛 socksio 依赖错误
os.environ.pop("all_proxy", None)
os.environ.pop("ALL_PROXY", None)

from limem import create_ltm  # noqa: E402
from limem.core.episode import Episode  # noqa: E402,F401
from limem.core.event import Event  # noqa: E402


def _fresh_db(suffix: str) -> str:
    base = Path(tempfile.gettempdir()) / f"limem_reg_e2e_{suffix}_{int(time.time())}.kz"
    if base.exists():
        if base.is_dir():
            shutil.rmtree(base)
        else:
            base.unlink()
    parent = base.parent
    parent.mkdir(parents=True, exist_ok=True)
    return str(base)


# ----------------- 图数据库探针（绕过 store 高阶接口，直查关系） -----------------


def _exec_rows(ltm, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
    resp = ltm.store.conn.execute(cypher, params or {})
    rows = []
    while resp.has_next():
        rows.append(list(resp.get_next()))
    return rows


def _entity_row(ltm, eid: str) -> Optional[dict[str, Any]]:
    rows = _exec_rows(
        ltm,
        """
        MATCH (e:Entity {id: $id})
        RETURN e.id, e.type, e.registered, e.status, e.canonical_id,
               e.aliases, e.merged_from, e.description
        """,
        {"id": eid},
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "id": r[0],
        "type": r[1],
        "registered": bool(r[2]) if r[2] is not None else False,
        "status": r[3] or "active",
        "canonical_id": r[4],
        "aliases": r[5] or "[]",
        "merged_from": r[6] or "[]",
        "description": r[7] or "",
    }


def _involves_targets(ltm, event_id: str) -> list[str]:
    rows = _exec_rows(
        ltm,
        "MATCH (e:Event {id: $id})-[:INVOLVES]->(en:Entity) RETURN en.id",
        {"id": event_id},
    )
    return sorted(r[0] for r in rows)


def _all_event_ids(ltm) -> list[str]:
    rows = _exec_rows(ltm, "MATCH (e:Event) RETURN e.id ORDER BY e.created_at")
    return [r[0] for r in rows]


def _merge_trace_rows(ltm) -> list[dict[str, Any]]:
    rows = _exec_rows(
        ltm,
        """
        MATCH (s:Entity)-[r:ENTITY_MERGE_TRACE]->(t:Entity)
        RETURN s.id, t.id, r.merge_reason, r.similarity_score, r.merged_at
        """,
    )
    return [
        {
            "source": r[0],
            "target": r[1],
            "merge_reason": r[2],
            "similarity_score": r[3],
            "merged_at": r[4],
        }
        for r in rows
    ]


# ----------------- 单场景实现 -----------------


def scenario_1_exact_name_link() -> bool:
    print("\n[1] 精确名链接（Case A）---------------------------")
    db_path = _fresh_db("exact")
    ltm = create_ltm(db_path=db_path)

    reg = ltm.register_entity(
        entity_id="小明",
        entity_type="person",
        description="我的女朋友小明，喜欢跑步，是软件工程师",
    )
    print("  register:", reg.get("action"), "->", reg.get("entity", {}).get("id"))

    ltm.ingest_text("今天和小明一起跑了步，他状态很好。")
    event_ids = _all_event_ids(ltm)
    print("  events:", event_ids)
    assert event_ids, "expected at least 1 event"

    xiaoming = _entity_row(ltm, "小明")
    print("  entity[小明]:", xiaoming)
    assert xiaoming is not None, "registered entity missing"
    assert xiaoming["registered"] is True
    assert xiaoming["status"] == "active"
    assert xiaoming["canonical_id"] is None

    targets = []
    for eid in event_ids:
        ts = _involves_targets(ltm, eid)
        targets.extend(ts)
    print("  involves targets:", targets)
    assert "小明" in targets, "INVOLVES should point to registered entity"

    traces = _merge_trace_rows(ltm)
    print("  merge traces:", traces)
    assert not traces, "no ENTITY_MERGE_TRACE expected for case A exact match"

    ltm.store.close()
    print("  -> PASS")
    return True


def scenario_2_vector_high_link() -> bool:
    """Stage 2 / Stage 3 路径确定性测试。

    LLM 抽取行为不可控；为隔离算法路径，这里直接构造 Event 并通过
    write(evolve=True) 触发 _create_entities_for_events 解析。
    """
    print("\n[2] 向量高分链接（Stage 2/3，Case A）------------")
    db_path = _fresh_db("vector")
    ltm = create_ltm(db_path=db_path)

    ltm.register_entity(
        entity_id="Project Helios",
        entity_type="project",
        description="代号 Helios 的内部项目，负责跨境物流自动化与多仓调度算法",
    )
    print("  registered: Project Helios")

    # 抽取出来的 participant 是一个语义近似但名字完全不同的别名
    now = int(time.time())
    evt = Event(
        id=f"evt_synth_{now}",
        summary="今天上午开会讨论代号 Helios 的物流项目进度，下周要交付仓库调度模块。",
        action="开会",
        causality="",
        participants=[
            {"id": "Helios 跨境物流项目", "type": "project"},
            {"id": "团队", "type": "group"},
        ],
        evidence=[],
        timestamp=now,
        last_active=now,
        created_at=now,
        updated_at=now,
        valid_from=now,
        valid_to=None,
        status="active",
        support_count=1,
        payload={},
    )
    ltm.write(item=evt, kind="event", evolve=True)

    event_ids = _all_event_ids(ltm)
    print("  events:", event_ids)
    assert evt.id in event_ids

    entity = _entity_row(ltm, "Project Helios")
    print("  entity[Project Helios]:", entity)
    assert entity is not None and entity["registered"]

    targets = _involves_targets(ltm, evt.id)
    print("  involves targets:", targets)

    # 注册实体应当被命中（Stage 2 vector_high 或 Stage 3 llm_yes 均接受）
    assert "Project Helios" in targets, \
        "registered entity should be linked from semantically-equivalent extracted name"
    # 别名应当收录抽取名
    aliases = entity["aliases"]
    print("  canonical aliases:", aliases)
    assert "Helios" in aliases or "项目" in aliases or "Helios 跨境物流项目" in aliases, \
        "the surface form should be appended to aliases"

    ltm.store.close()
    print("  -> PASS")
    return True


def scenario_3_legacy_merge() -> bool:
    print("\n[3] 历史合并（Case B：register 时回扫别名）-------")
    db_path = _fresh_db("legacy")
    ltm = create_ltm(db_path=db_path)

    # 先注入两条提及 "小明" 的对话，让 "小明" 作为抽取节点物化
    ltm.ingest_text("昨天和小明吃了火锅。")
    ltm.ingest_text("小明帮我修了一下电脑。")

    pre_entity = _entity_row(ltm, "小明")
    print("  pre-register entity[小明]:", pre_entity)
    assert pre_entity is not None
    assert pre_entity["registered"] is False
    pre_targets = []
    for eid in _all_event_ids(ltm):
        pre_targets.extend(_involves_targets(ltm, eid))
    print("  pre-register involves targets:", pre_targets)
    assert pre_targets.count("小明") >= 1, "expected at least one INVOLVES on 小明"

    # 用新 id 注册，并把 "小明" 列为别名 → 触发 Case B 回扫
    reg = ltm.register_entity(
        entity_id="小明老板",
        entity_type="person",
        description="我朋友小明，老家在云南，目前在做电商创业",
        aliases=["小明"],
    )
    print("  register result:", reg.get("action"))

    canonical = _entity_row(ltm, "小明老板")
    merged_node = _entity_row(ltm, "小明")
    print("  canonical:", canonical)
    print("  merged-node:", merged_node)
    assert canonical is not None and canonical["registered"]
    assert merged_node is not None
    assert merged_node["status"] == "merged", \
        f"expected status=merged, got {merged_node['status']}"
    assert merged_node["canonical_id"] == "小明老板", \
        f"expected canonical_id=小明老板, got {merged_node['canonical_id']}"

    # 所有 INVOLVES 应迁移至 "小明老板"
    post_targets = []
    for eid in _all_event_ids(ltm):
        post_targets.extend(_involves_targets(ltm, eid))
    print("  post-register involves targets:", post_targets)
    assert "小明" not in post_targets, "INVOLVES should no longer reach merged node"
    assert "小明老板" in post_targets, "INVOLVES should reach canonical"

    traces = _merge_trace_rows(ltm)
    print("  merge traces:", traces)
    assert any(
        t["source"] == "小明" and t["target"] == "小明老板"
        for t in traces
    ), "expected ENTITY_MERGE_TRACE 小明 -> 小明老板"

    ltm.store.close()
    print("  -> PASS")
    return True


def scenario_4_conservative_default() -> bool:
    print("\n[4] 保守缺省（Stage 3 不确定 / 不同语境）--------")
    db_path = _fresh_db("conservative")
    ltm = create_ltm(db_path=db_path)

    ltm.register_entity(
        entity_id="小明老板",
        entity_type="person",
        description="我的女朋友，小名叫小明，喜欢做饭和看电影",
    )

    # 一个完全不同语境（公司创始人）的"小明"提及 → 应当不被链接到注册实体
    ltm.ingest_text("在新闻里看到小明集团创始人辞职，引发资本市场震动。")
    event_ids = _all_event_ids(ltm)
    targets = []
    for eid in event_ids:
        targets.extend(_involves_targets(ltm, eid))
    print("  involves targets:", targets)
    print("  events:", event_ids)

    # 注册实体不应该被链接到
    assert "小明老板" not in targets, \
        "registered entity should NOT be linked from a different context"

    # 抽取实体 (e.g. "小明" 或 "小明集团") 应当独立物化为非注册节点
    has_independent_extracted = False
    for t in targets:
        row = _entity_row(ltm, t)
        if row and not row["registered"]:
            has_independent_extracted = True
            break
    print("  has_independent_extracted:", has_independent_extracted)
    # 算法允许 extractor 一个也不产；若有，则必须不是注册节点
    if has_independent_extracted:
        print("  -> PASS")
    else:
        print("  -> PASS (no entity extracted, but no spurious link either)")

    traces = _merge_trace_rows(ltm)
    print("  merge traces:", traces)
    assert not traces, "no merge trace expected for conservative reject"

    ltm.store.close()
    return True


def scenario_5_list_endpoint() -> bool:
    """list_registered_entities() 透传：验证字段与无 embedding。"""
    print("\n[5] list_registered_entities ---------------------------")
    db_path = _fresh_db("list")
    ltm = create_ltm(db_path=db_path)

    ltm.register_entity(
        entity_id="u_alice",
        entity_type="person",
        description="产品经理 Alice",
        aliases=["alice", "小李"],
    )
    ltm.register_entity(
        entity_id="proj_apollo",
        entity_type="project",
        description="阿波罗内部知识图谱项目",
        aliases=["apollo"],
    )

    items = ltm.list_registered_entities()
    print(f"  items count: {len(items)}")
    assert isinstance(items, list), "list_registered_entities should return list"
    assert len(items) >= 2, "expected at least 2 registered entities"

    ids = {it.get("id") for it in items}
    assert "u_alice" in ids and "proj_apollo" in ids, f"missing expected ids in {ids}"

    for it in items:
        # 必备字段
        for f in ("id", "type", "description", "aliases", "registered", "status", "updated_at"):
            assert f in it, f"missing field {f} in {it}"
        # 不应泄漏 embedding 大数组
        assert "embedding" not in it, "embedding must NOT be returned to UI"
        assert "description_embedding" not in it, "description_embedding must NOT be returned"
        assert it["registered"] is True, "list should only include registered=True"

    print("  -> PASS")
    ltm.store.close()
    return True


def main() -> int:
    scenarios = [
        ("exact_name_link", scenario_1_exact_name_link),
        ("vector_high_link", scenario_2_vector_high_link),
        ("legacy_merge", scenario_3_legacy_merge),
        ("conservative_default", scenario_4_conservative_default),
        ("list_endpoint", scenario_5_list_endpoint),
    ]
    failed = []
    for name, fn in scenarios:
        try:
            ok = fn()
            if not ok:
                failed.append(name)
        except AssertionError as exc:
            print(f"  !! ASSERTION FAIL in {name}: {exc}")
            failed.append(name)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"  !! EXCEPTION in {name}: {exc}")
            failed.append(name)
    print("\n========== SUMMARY ==========")
    print(f"  scenarios run: {len(scenarios)}")
    print(f"  failed:        {failed}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
