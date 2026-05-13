"""Tests for the batch entity-pattern creation endpoint (`POST .../patterns/:batch`)."""

from __future__ import annotations

# `pattern_app_factory` / `pattern_client` 来自 conftest.py
from test_entity_patterns import _create_user_key  # noqa: F401


def _create_db_and_entity(client):
    _, token, _ = _create_user_key(client, "alice-batch")
    db = client.post(
        "/databases",
        json={"display_name": "PatternsBatch"},
        headers={"X-API-Key": token},
    ).json()
    db_id = db["db_id"]
    entity = client.post(
        f"/db/{db_id}/api/entities",
        json={
            "entity_id": "user:alice",
            "entity_type": "user",
            "description": "Alice registered user",
        },
        headers={"X-API-Key": token},
    )
    assert entity.status_code == 200, entity.text
    return token, db_id


def test_batch_create_patterns_atomic_success(pattern_client):
    token, db_id = _create_db_and_entity(pattern_client)
    headers = {"X-API-Key": token}
    base = f"/db/{db_id}/api/entities/user:alice/patterns"

    resp = pattern_client.post(
        f"{base}/:batch",
        json={
            "patterns": [
                {"pattern_id": "p1", "content": "偏好A"},
                {"pattern_id": "p2", "content": "偏好B", "pattern_type": "rule"},
                {"pattern_id": "p3", "content": "偏好C"},
            ],
            "atomic": True,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["atomic"] is True
    assert [p["id"] for p in body["created"]] == ["p1", "p2", "p3"]
    assert body["failed"] == []

    listed = pattern_client.get(base, headers=headers).json()
    assert {p["id"] for p in listed["items"]} == {"p1", "p2", "p3"}


def test_batch_create_patterns_atomic_rollback_on_dup(pattern_client):
    """原子模式下，遇到重复 pattern_id 应回滚所有已写入项。"""
    token, db_id = _create_db_and_entity(pattern_client)
    headers = {"X-API-Key": token}
    base = f"/db/{db_id}/api/entities/user:alice/patterns"

    # 先种入一个 pattern，使后续批量中的 dup 必然失败
    seed = pattern_client.post(
        base,
        json={"pattern_id": "dup", "content": "已经存在"},
        headers=headers,
    )
    assert seed.status_code == 200, seed.text

    resp = pattern_client.post(
        f"{base}/:batch",
        json={
            "patterns": [
                {"pattern_id": "ok1", "content": "X"},
                {"pattern_id": "dup", "content": "重复"},  # 这条会失败
                {"pattern_id": "ok2", "content": "Y"},
            ],
            "atomic": True,
        },
        headers=headers,
    )
    assert resp.status_code in (400, 404), resp.text

    listed = pattern_client.get(base, headers=headers).json()
    # 回滚后只应剩种入的 dup
    assert {p["id"] for p in listed["items"]} == {"dup"}


def test_batch_create_patterns_best_effort(pattern_client):
    """atomic=False 时部分失败应返回 failed 列表而非回滚。"""
    token, db_id = _create_db_and_entity(pattern_client)
    headers = {"X-API-Key": token}
    base = f"/db/{db_id}/api/entities/user:alice/patterns"

    pattern_client.post(base, json={"pattern_id": "dup", "content": "X"}, headers=headers)

    resp = pattern_client.post(
        f"{base}/:batch",
        json={
            "patterns": [
                {"pattern_id": "ok1", "content": "A"},
                {"pattern_id": "dup", "content": "B"},
                {"pattern_id": "ok2", "content": "C"},
            ],
            "atomic": False,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["atomic"] is False
    assert {p["id"] for p in body["created"]} == {"ok1", "ok2"}
    assert len(body["failed"]) == 1
    assert body["failed"][0]["index"] == 1

    listed = pattern_client.get(base, headers=headers).json()
    assert {p["id"] for p in listed["items"]} == {"dup", "ok1", "ok2"}


def test_batch_create_patterns_empty_rejected(pattern_client):
    token, db_id = _create_db_and_entity(pattern_client)
    headers = {"X-API-Key": token}
    base = f"/db/{db_id}/api/entities/user:alice/patterns"
    resp = pattern_client.post(
        f"{base}/:batch",
        json={"patterns": [], "atomic": True},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text


def test_batch_create_patterns_missing_entity_returns_404(pattern_client):
    token, db_id = _create_db_and_entity(pattern_client)
    headers = {"X-API-Key": token}
    resp = pattern_client.post(
        f"/db/{db_id}/api/entities/no-such-entity/patterns/:batch",
        json={"patterns": [{"content": "x"}], "atomic": True},
        headers=headers,
    )
    assert resp.status_code == 404, resp.text
