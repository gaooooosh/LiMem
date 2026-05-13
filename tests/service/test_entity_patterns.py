"""Service tests for registered-entity pattern APIs."""

from __future__ import annotations

# `pattern_app_factory` / `pattern_client` 由 conftest.py 提供。


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


def _create_db_and_entity(client):
    uid, token, _ = _create_user_key(client, "alice")
    db = client.post(
        "/databases",
        json={"display_name": "Patterns"},
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
    return uid, token, db_id


def test_entity_pattern_crud_and_recall(pattern_client):
    _, token, db_id = _create_db_and_entity(pattern_client)
    headers = {"X-API-Key": token}
    base = f"/db/{db_id}/api/entities/user:alice/patterns"

    created = pattern_client.post(
        base,
        json={
            "pattern_id": "pref_music",
            "content": "用户明确表示喜欢听周杰伦的歌。",
            "pattern_type": "preference",
            "metadata": {"source": "manual"},
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["action"] == "created"
    assert body["pattern"]["id"] == "pref_music"
    assert body["pattern"]["entity_id"] == "user:alice"

    pattern_client.post(
        base,
        json={
            "pattern_id": "pref_food",
            "content": "用户不喜欢太辣的食物。",
            "pattern_type": "preference",
        },
        headers=headers,
    )

    all_patterns = pattern_client.get(base, headers=headers)
    assert all_patterns.status_code == 200
    assert all_patterns.json()["total"] == 2

    # GET 单条：必须返回严格符合 EntityPattern 的 JSON
    single = pattern_client.get(f"{base}/pref_music", headers=headers)
    assert single.status_code == 200, single.text
    single_body = single.json()
    assert single_body["id"] == "pref_music"
    assert single_body["entity_id"] == "user:alice"
    assert single_body["pattern_type"] == "preference"
    assert single_body["status"] == "active"
    assert isinstance(single_body["metadata"], dict)

    recalled = pattern_client.get(f"{base}?q=周杰伦", headers=headers)
    assert recalled.status_code == 200
    recalled_body = recalled.json()
    assert recalled_body["total"] == 1
    assert recalled_body["items"][0]["id"] == "pref_music"

    updated = pattern_client.patch(
        f"{base}/pref_music",
        json={
            "content": "用户明确表示喜欢听周杰伦和林俊杰的歌。",
            "metadata": {"source": "manual", "confidence": 1.0},
        },
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert "林俊杰" in updated.json()["pattern"]["content"]
    assert updated.json()["pattern"]["metadata"]["confidence"] == 1.0

    deleted = pattern_client.delete(f"{base}/pref_music", headers=headers)
    assert deleted.status_code == 200, deleted.text
    deleted_body = deleted.json()
    assert deleted_body["action"] == "archived"
    assert deleted_body["pattern"]["id"] == "pref_music"
    assert deleted_body["pattern"]["status"] == "archived"

    active_only = pattern_client.get(base, headers=headers).json()
    assert {item["id"] for item in active_only["items"]} == {"pref_food"}

    include_inactive = pattern_client.get(f"{base}?include_inactive=true", headers=headers).json()
    statuses = {item["id"]: item["status"] for item in include_inactive["items"]}
    assert statuses["pref_music"] == "archived"
    assert statuses["pref_food"] == "active"


def test_entity_pattern_requires_registered_entity(pattern_client):
    _, token, db_id = _create_db_and_entity(pattern_client)
    r = pattern_client.post(
        f"/db/{db_id}/api/entities/missing/patterns",
        json={"content": "偏好内容"},
        headers={"X-API-Key": token},
    )
    assert r.status_code == 404
    assert "Registered entity not found" in r.json()["detail"]


def test_read_only_key_can_read_but_not_write_patterns(pattern_client):
    uid, rw_token, db_id = _create_db_and_entity(pattern_client)
    issue_read = pattern_client.post(
        f"/admin/users/{uid}/keys",
        json={"label": "readonly", "scopes": "r"},
        headers=_root(),
    ).json()
    read_token = issue_read["token"]
    base = f"/db/{db_id}/api/entities/user:alice/patterns"

    denied = pattern_client.post(
        base,
        json={"content": "用户喜欢安静环境。"},
        headers={"X-API-Key": read_token},
    )
    assert denied.status_code == 403
    assert "write scope required" in denied.json()["detail"]

    ok = pattern_client.post(
        base,
        json={"pattern_id": "pref_env", "content": "用户喜欢安静环境。"},
        headers={"X-API-Key": rw_token},
    )
    assert ok.status_code == 200

    read = pattern_client.get(base, headers={"X-API-Key": read_token})
    assert read.status_code == 200
    assert read.json()["items"][0]["id"] == "pref_env"
