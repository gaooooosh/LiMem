"""Tests for inline pattern creation during `POST /db/{db_id}/api/entities`."""

from __future__ import annotations

# `pattern_app_factory` / `pattern_client` 来自 conftest.py
from test_entity_patterns import _create_user_key  # noqa: F401


def _new_db(client):
    _, token, _ = _create_user_key(client, "inline-user")
    db = client.post(
        "/databases",
        json={"display_name": "InlinePatterns"},
        headers={"X-API-Key": token},
    ).json()
    return token, db["db_id"]


def test_register_entity_without_patterns_is_backward_compatible(pattern_client):
    token, db_id = _new_db(pattern_client)
    headers = {"X-API-Key": token}
    resp = pattern_client.post(
        f"/db/{db_id}/api/entities",
        json={
            "entity_id": "user:noinline",
            "entity_type": "user",
            "description": "no inline patterns",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "created"
    assert body["patterns"] == []


def test_register_entity_with_inline_patterns(pattern_client):
    token, db_id = _new_db(pattern_client)
    headers = {"X-API-Key": token}
    resp = pattern_client.post(
        f"/db/{db_id}/api/entities",
        json={
            "entity_id": "user:inline-ok",
            "entity_type": "user",
            "description": "inline patterns success",
            "patterns": [
                {"pattern_id": "p1", "content": "偏好早会"},
                {"pattern_id": "p2", "content": "周五不发布", "pattern_type": "rule"},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["action"] == "created"
    assert [p["id"] for p in body["patterns"]] == ["p1", "p2"]
    assert body["patterns"][1]["pattern_type"] == "rule"

    listed = pattern_client.get(
        f"/db/{db_id}/api/entities/user:inline-ok/patterns",
        headers=headers,
    ).json()
    assert {p["id"] for p in listed["items"]} == {"p1", "p2"}


def test_inline_patterns_rollback_on_dup_creates_no_entity(pattern_client):
    """注册新实体时若内联 pattern 出现 dup，整个事务回滚，实体也不应存在。"""
    token, db_id = _new_db(pattern_client)
    headers = {"X-API-Key": token}
    resp = pattern_client.post(
        f"/db/{db_id}/api/entities",
        json={
            "entity_id": "user:rollback",
            "entity_type": "user",
            "description": "should be rolled back",
            "patterns": [
                {"pattern_id": "shared", "content": "A"},
                {"pattern_id": "shared", "content": "B"},  # 同请求内重复 → 失败
            ],
        },
        headers=headers,
    )
    assert resp.status_code in (400, 404), resp.text

    # 实体不应存在
    get_resp = pattern_client.get(
        f"/db/{db_id}/api/entities/user:rollback",
        headers=headers,
    )
    assert get_resp.status_code == 404


def test_inline_patterns_failure_on_existing_entity_keeps_entity(pattern_client):
    """如果是 promoted/updated（实体已存在），失败时应只回滚 pattern，不删实体。"""
    token, db_id = _new_db(pattern_client)
    headers = {"X-API-Key": token}

    # 先创建实体
    first = pattern_client.post(
        f"/db/{db_id}/api/entities",
        json={
            "entity_id": "user:keep",
            "entity_type": "user",
            "description": "preexisting",
        },
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["action"] == "created"

    # 再次注册同实体并附带 dup pattern：触发回滚，但实体不应被删
    second = pattern_client.post(
        f"/db/{db_id}/api/entities",
        json={
            "entity_id": "user:keep",
            "entity_type": "user",
            "description": "preexisting",
            "patterns": [
                {"pattern_id": "k1", "content": "A"},
                {"pattern_id": "k1", "content": "B"},
            ],
        },
        headers=headers,
    )
    assert second.status_code in (400, 404), second.text

    # 实体仍在
    get_resp = pattern_client.get(
        f"/db/{db_id}/api/entities/user:keep",
        headers=headers,
    )
    assert get_resp.status_code == 200, get_resp.text
    # 但 pattern 应已回滚
    listed = pattern_client.get(
        f"/db/{db_id}/api/entities/user:keep/patterns",
        headers=headers,
    ).json()
    assert listed["items"] == []
