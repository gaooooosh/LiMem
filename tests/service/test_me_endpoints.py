"""Tests for /me, /me/keys 端点。

覆盖：
  - root key 调 /me → is_root: true
  - user key 调 /me → 正确 user_id + scopes + 不含别人信息
  - revoked key 调 /me → 401
  - user 自助签发 scope 子集成功（201）
  - user 试图签 admin → 403 cannot escalate
  - user 列自己的 keys 正确返回，不会看到别 user 的
  - user 删自己的 key OK；删别人的 → 404（不泄漏存在性）
  - root 调 /me/keys 返回空数组（root 无 SQL 落库 key）
  - root 调 /me/keys POST 拒绝（400）
"""

from __future__ import annotations


def test_me_root(client, root_headers):
    r = client.get("/me", headers=root_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["is_root"] is True
    assert body["user_id"] == "__root__"
    assert "admin" in body["scopes"] and "r" in body["scopes"] and "w" in body["scopes"]


def test_me_missing_key_401(client):
    assert client.get("/me").status_code == 401


def test_me_invalid_key_401(client):
    assert client.get("/me", headers={"X-API-Key": "bogus"}).status_code == 401


def test_me_user_key(client, make_user_key):
    uid, token, key_id = make_user_key("alice", scopes="r,w")
    r = client.get("/me", headers={"X-API-Key": token})
    assert r.status_code == 200
    body = r.json()
    assert body["is_root"] is False
    assert body["user_id"] == uid
    assert body["user_name"] == "alice"
    assert body["key_id"] == key_id
    assert sorted(body["scopes"]) == ["r", "w"]


def test_me_revoked_401(client, make_user_key, root_headers):
    _, token, key_id = make_user_key("alice")
    assert client.get("/me", headers={"X-API-Key": token}).status_code == 200
    client.delete(f"/admin/keys/{key_id}", headers=root_headers)
    assert client.get("/me", headers={"X-API-Key": token}).status_code == 401


def test_me_keys_root_empty(client, root_headers):
    r = client.get("/me/keys", headers=root_headers)
    assert r.status_code == 200
    assert r.json() == []


def test_me_keys_root_post_rejected(client, root_headers):
    r = client.post(
        "/me/keys", json={"label": "x", "scopes": "r"}, headers=root_headers
    )
    assert r.status_code == 400


def test_me_keys_self_listing(client, make_user_key):
    _, alice_token, alice_key = make_user_key("alice")
    _, bob_token, _ = make_user_key("bob")

    a_keys = client.get("/me/keys", headers={"X-API-Key": alice_token}).json()
    b_keys = client.get("/me/keys", headers={"X-API-Key": bob_token}).json()
    a_ids = {k["id"] for k in a_keys}
    b_ids = {k["id"] for k in b_keys}
    assert alice_key in a_ids
    assert alice_key not in b_ids


def test_me_issue_subset_ok(client, make_user_key):
    _, rw_token, _ = make_user_key("alice", scopes="r,w")
    r = client.post(
        "/me/keys",
        json={"label": "readonly", "scopes": "r"},
        headers={"X-API-Key": rw_token},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["key"]["scopes"] == "r"
    assert body["token"]  # 明文 token 仅本次返回
    # 用新 key 调 /me 应返回 r scope
    r2 = client.get("/me", headers={"X-API-Key": body["token"]})
    assert r2.status_code == 200
    assert r2.json()["scopes"] == ["r"]


def test_me_issue_escalation_403(client, make_user_key):
    _, rw_token, _ = make_user_key("alice", scopes="r,w")
    r = client.post(
        "/me/keys",
        json={"label": "evil", "scopes": "admin"},
        headers={"X-API-Key": rw_token},
    )
    assert r.status_code == 403
    assert "cannot escalate" in r.json()["detail"]


def test_me_issue_unknown_scope_400(client, make_user_key):
    _, token, _ = make_user_key("alice")
    r = client.post(
        "/me/keys",
        json={"label": "x", "scopes": "rwx"},
        headers={"X-API-Key": token},
    )
    assert r.status_code == 400


def test_me_issue_default_when_empty(client, make_user_key):
    """空 scope 输入按服务端默认收敛到 r（不会借此空跑出超大 scope）。"""
    _, token, _ = make_user_key("alice")
    r = client.post(
        "/me/keys",
        json={"label": "x", "scopes": ""},
        headers={"X-API-Key": token},
    )
    assert r.status_code == 201
    assert r.json()["key"]["scopes"] == "r"


def test_me_revoke_self_204(client, make_user_key):
    _, token, _ = make_user_key("alice")
    issue = client.post(
        "/me/keys",
        json={"label": "to-revoke", "scopes": "r"},
        headers={"X-API-Key": token},
    ).json()
    new_id = issue["key"]["id"]
    r = client.delete(
        f"/me/keys/{new_id}", headers={"X-API-Key": token}
    )
    assert r.status_code == 204
    # 撤销后再用旧明文 → 401
    assert client.get("/me", headers={"X-API-Key": issue["token"]}).status_code == 401


def test_me_revoke_idempotent(client, make_user_key):
    """已撤销的 key 再次撤销不报错。"""
    _, token, _ = make_user_key("alice")
    issue = client.post(
        "/me/keys",
        json={"label": "x", "scopes": "r"},
        headers={"X-API-Key": token},
    ).json()
    new_id = issue["key"]["id"]
    assert client.delete(f"/me/keys/{new_id}", headers={"X-API-Key": token}).status_code == 204
    assert client.delete(f"/me/keys/{new_id}", headers={"X-API-Key": token}).status_code == 204


def test_me_revoke_others_404(client, make_user_key):
    """跨 user 撤销返回 404，避免暴露 key_id 是否存在。"""
    _, alice_token, _ = make_user_key("alice")
    _, bob_token, bob_key = make_user_key("bob")
    r = client.delete(
        f"/me/keys/{bob_key}", headers={"X-API-Key": alice_token}
    )
    assert r.status_code == 404
    # bob 的 key 仍可用
    assert client.get("/me", headers={"X-API-Key": bob_token}).status_code == 200


def test_me_revoke_nonexistent_404(client, make_user_key):
    _, token, _ = make_user_key("alice")
    r = client.delete("/me/keys/does-not-exist", headers={"X-API-Key": token})
    assert r.status_code == 404


def test_me_root_revoke_any_key(client, make_user_key, root_headers):
    """root 是 /me 路由的特例：可以删任意 key。"""
    _, _, key_id = make_user_key("alice")
    # 但 /me/keys 删除路径走的是 me.py 的逻辑：root.is_root → 跳过 user_id 检查
    r = client.delete(f"/me/keys/{key_id}", headers=root_headers)
    assert r.status_code == 204
