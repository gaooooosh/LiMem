"""Entity Pattern v2 召回单元测试 + HTTP 集成测试。

覆盖：
1. split_h2_sections：多 heading / H3不下钻 / 无 H2 整篇
2. score_section：heading*2 + body*1 + clip=5
3. mode=full 强制返回完整 content
4. mode=section + top_k_sections 限制；返回按 char_offset 升序
5. mode=auto 长度阈值切换
6. jieba 中文分词命中 + 英文 case-insensitive
7. HTTP /recall 无 pattern → 200 + 空响应
8. section 拼接分隔符 + score=0 过滤
"""

from __future__ import annotations

import pytest

from limem.retrieval.pattern_recall import (
    Section,
    recall_pattern,
    score_section,
    split_h2_sections,
)


# ---------- 单元测试 ----------


def test_split_basic_and_no_heading():
    md = "## 偏好\n- 茶\n\n## 习惯\n- 早起"
    secs = split_h2_sections(md)
    assert len(secs) == 2
    assert secs[0].heading == "## 偏好"
    assert secs[0].char_offset == 0
    assert "茶" in secs[0].body
    assert secs[1].heading == "## 习惯"

    plain = "no heading at all\njust text"
    secs2 = split_h2_sections(plain)
    assert len(secs2) == 1
    assert secs2[0].heading == ""
    assert secs2[0].body == plain


def test_split_h3_not_descended_and_prelude():
    md = "intro line\n\n## A\nbody a\n### A.1\nsub body\n## B\nbody b"
    secs = split_h2_sections(md)
    # 序章 + ## A + ## B = 3
    assert [s.heading for s in secs] == ["", "## A", "## B"]
    # H3 不下钻：sub body 应作为 ## A 的正文
    assert "### A.1" in secs[1].body
    assert "sub body" in secs[1].body


def test_score_heading_weight_and_clip():
    sec = Section(heading="## 咖啡", body="咖啡 咖啡 咖啡 咖啡 咖啡 咖啡 咖啡", char_offset=0)
    s = score_section(sec, ["咖啡"])
    # heading 命中 1 次 × 2 + body 命中 clip=5 × 1 = 7
    assert s == 2 + 5


def test_score_empty_query_zero():
    sec = Section(heading="## x", body="x", char_offset=0)
    assert score_section(sec, []) == 0


def test_recall_full_mode_returns_whole():
    md = "## A\nbody"
    r = recall_pattern(md, query="any", mode="full")
    assert r["mode"] == "full"
    assert r["content"] == md
    assert r["matched_sections"] == []


def test_recall_section_top_k_and_order():
    md = "## A\nx咖啡\n\n## B\n咖啡咖啡\n\n## C\nz"
    r = recall_pattern(md, query="咖啡", mode="section", top_k_sections=2)
    assert r["mode"] == "section"
    headings = [m["heading"] for m in r["matched_sections"]]
    # 都命中咖啡的章节是 A 和 B；C 无命中应被过滤
    assert set(headings) == {"## A", "## B"}
    # 输出按 char_offset 升序
    offsets = [m["char_offset"] for m in r["matched_sections"]]
    assert offsets == sorted(offsets)
    # 拼接分隔符存在
    assert "\n\n---\n\n" in r["content"]


def test_recall_section_no_match_returns_empty():
    md = "## A\nbody"
    r = recall_pattern(md, query="不存在的词", mode="section", top_k_sections=3)
    assert r["mode"] == "section"
    assert r["content"] == ""
    assert r["matched_sections"] == []


def test_recall_auto_switches_by_length():
    short_md = "## A\nbody"
    long_md = "## A\n" + ("x" * 3000) + "\n\n## B\n咖啡"
    # 短文档：auto + query → full
    r_short = recall_pattern(short_md, query="A", mode="auto", full_return_max_chars=2000)
    assert r_short["mode"] == "full"
    # 长文档：auto + query → section
    r_long = recall_pattern(long_md, query="咖啡", mode="auto", full_return_max_chars=2000)
    assert r_long["mode"] == "section"
    # 任意长度：query="" → full
    r_empty = recall_pattern(long_md, query="", mode="auto", full_return_max_chars=2000)
    assert r_empty["mode"] == "full"


def test_recall_english_case_insensitive():
    md = "## Coffee\nlikes coffee a lot"
    r = recall_pattern(md, query="COFFEE", mode="section")
    assert r["mode"] == "section"
    assert len(r["matched_sections"]) == 1


# ---------- HTTP 集成测试 ----------


def _root():
    return {"X-API-Key": "root-test-token"}


def _create_user_key(client, name: str, scopes: str = "r,w"):
    user = client.post("/admin/users", json={"name": name}, headers=_root()).json()
    issue = client.post(
        f"/admin/users/{user['id']}/keys",
        json={"label": "test", "scopes": scopes},
        headers=_root(),
    ).json()
    return user["id"], issue["token"], issue["key"]["id"]


def _setup(client):
    _, token, _ = _create_user_key(client, "recall_user")
    db = client.post(
        "/databases",
        json={"display_name": "Recall"},
        headers={"X-API-Key": token},
    ).json()
    db_id = db["db_id"]
    client.post(
        f"/db/{db_id}/api/entities",
        json={"entity_id": "u_r", "description": "r"},
        headers={"X-API-Key": token},
    )
    return token, db_id


def test_http_recall_when_no_pattern(pattern_client):
    token, db_id = _setup(pattern_client)
    r = pattern_client.get(
        f"/db/{db_id}/api/entities/u_r/patterns/recall?query=x",
        headers={"X-API-Key": token},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["pattern"] is None
    assert body["matched_sections"] == []


def test_http_recall_section_mode(pattern_client):
    token, db_id = _setup(pattern_client)
    headers = {"X-API-Key": token}
    pattern_client.put(
        f"/db/{db_id}/api/entities/u_r/patterns",
        json={"content": "## 偏好\n- 茶\n\n## 习惯\n- 早起"},
        headers=headers,
    )
    r = pattern_client.get(
        f"/db/{db_id}/api/entities/u_r/patterns/recall?query=茶&mode=section&top_k_sections=1",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "section"
    assert body["pattern"] is not None
    # 至少命中"偏好"章节
    headings = [m["heading"] for m in body["matched_sections"]]
    assert any("偏好" in h for h in headings)
