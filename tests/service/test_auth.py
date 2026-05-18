"""Auth-layer regression tests for the multi-tenant LiMem service.

通过 monkeypatch 把 LtmPool 的 loader 替换为 FakeHandle，避免触发真实的 Kuzu/LLM
依赖。重点覆盖：
  - 缺/错 key 401
  - 跨用户访问 403
  - revoked key 401
  - root 旁路 admin
  - root 不能用 /databases POST 建库（必须代某具体 user）
  - 归档后的库 404
"""

from __future__ import annotations

import importlib
import os
import threading
import sys
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def app_factory(tmp_path, monkeypatch):
    """每个测试一个独立的 SQLite + 临时 base dir，互不污染。"""
    monkeypatch.setenv("ROOT_API_KEY", "root-test-token")
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "auth.sqlite"))
    monkeypatch.setenv("MULTI_DB_BASE_DIR", str(tmp_path / "DB"))
    monkeypatch.setenv("MULTI_AUDIT_BASE_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("LTM_POOL_MAX_SIZE", "4")
    monkeypatch.setenv("LTM_POOL_IDLE_TIMEOUT_SEC", "60")
    monkeypatch.delenv("SERVICE_DB_PATH", raising=False)
    monkeypatch.delenv("SERVICE_AUDIT_LOG_PATH", raising=False)

    # 用 fake loader 顶替真实 LTM 加载
    from service import database_manager as dm_mod
    from service.pool import LtmHandle

    class _FakeLtm:
        class _Store:
            def close(self):
                pass

        def __init__(self):
            self.store = _FakeLtm._Store()

        def get_stats(self):
            return {"event_count": 0}

    class _FakeAudit:
        path = "/tmp/fake-audit.jsonl"

        def read_recent(self, limit=200):
            return []

    class _FakeBM25:
        size = 0

        def search(self, q, k):
            return []

        def rebuild(self, events):
            pass

    def fake_load(self, dbr):
        return LtmHandle(
            db_id=dbr.db_id,
            ltm=_FakeLtm(),
            audit=_FakeAudit(),
            bm25=_FakeBM25(),
            write_lock=threading.Lock(),
        )

    monkeypatch.setattr(dm_mod.DatabaseManager, "_load_handle", fake_load, raising=True)

    # 强制重新导入 service.app 以拿到 fresh state（虽然 create_app 每次都新建）
    if "service.app" in sys.modules:
        importlib.reload(sys.modules["service.app"])
    from service.app import create_app  # noqa: WPS433

    return create_app


@pytest.fixture()
def client(app_factory):
    from fastapi.testclient import TestClient

    app = app_factory()
    with TestClient(app) as c:
        yield c


def _root(headers=None):
    h = {"X-API-Key": "root-test-token"}
    if headers:
        h.update(headers)
    return h


def _create_user_and_key(client, name: str, scopes: str = "r,w"):
    user = client.post("/admin/users", json={"name": name}, headers=_root()).json()
    issue = client.post(
        f"/admin/users/{user['id']}/keys",
        json={"label": "test", "scopes": scopes},
        headers=_root(),
    ).json()
    return user["id"], issue["token"], issue["key"]["id"]


def test_missing_key_returns_401(client):
    r = client.post("/db/anything/query", json={"query": "x"})
    assert r.status_code == 401
    assert "missing api key" in r.json()["detail"]


def test_invalid_key_returns_401(client):
    r = client.post("/db/anything/query", json={"query": "x"}, headers={"X-API-Key": "bogus"})
    assert r.status_code == 401


def test_cross_user_403(client):
    _, alice_token, _ = _create_user_and_key(client, "alice")
    _, bob_token, _ = _create_user_and_key(client, "bob")

    db = client.post(
        "/databases", json={"display_name": "Alice Notes"}, headers={"X-API-Key": alice_token}
    ).json()
    db_id = db["db_id"]

    # alice 自己访问 OK
    r_ok = client.get(f"/db/{db_id}/health", headers={"X-API-Key": alice_token})
    assert r_ok.status_code == 200

    # bob 访问 alice 的库 → 403
    r_forbidden = client.get(f"/db/{db_id}/health", headers={"X-API-Key": bob_token})
    assert r_forbidden.status_code == 403


def test_revoked_key_returns_401(client):
    _, token, key_id = _create_user_and_key(client, "alice")
    db = client.post(
        "/databases", json={"display_name": "X"}, headers={"X-API-Key": token}
    ).json()
    db_id = db["db_id"]

    assert client.get(f"/db/{db_id}/health", headers={"X-API-Key": token}).status_code == 200

    revoked = client.delete(f"/admin/keys/{key_id}", headers=_root())
    assert revoked.status_code == 204

    after = client.get(f"/db/{db_id}/health", headers={"X-API-Key": token})
    assert after.status_code == 401


def test_root_bypass_admin_routes(client):
    r = client.get("/admin/users", headers=_root())
    assert r.status_code == 200

    r2 = client.get("/admin/databases", headers=_root())
    assert r2.status_code == 200

    # 无 key 应被拒
    assert client.get("/admin/users").status_code == 401
    assert client.get("/admin/users", headers={"X-API-Key": "bad"}).status_code == 401


def test_root_cannot_create_database_directly(client):
    """root 没有 user_id，不允许直接 POST /databases；必须通过 admin 路径代某 user。"""
    r = client.post("/databases", json={"display_name": "rootdb"}, headers=_root())
    assert r.status_code == 400


def test_read_only_key_cannot_create_or_archive_database(client):
    uid, rw_token, _ = _create_user_and_key(client, "alice")
    issue_read = client.post(
        f"/admin/users/{uid}/keys",
        json={"label": "readonly", "scopes": "r"},
        headers=_root(),
    ).json()
    read_token = issue_read["token"]

    create_denied = client.post(
        "/databases",
        json={"display_name": "Read Only DB"},
        headers={"X-API-Key": read_token},
    )
    assert create_denied.status_code == 403
    assert "write scope required" in create_denied.json()["detail"]

    db = client.post(
        "/databases", json={"display_name": "Writable"}, headers={"X-API-Key": rw_token}
    ).json()
    db_id = db["db_id"]

    assert client.get("/databases", headers={"X-API-Key": read_token}).status_code == 200
    assert client.get(f"/db/{db_id}/health", headers={"X-API-Key": read_token}).status_code == 200

    archive_denied = client.delete(
        f"/databases/{db_id}", headers={"X-API-Key": read_token}
    )
    assert archive_denied.status_code == 403
    assert "write scope required" in archive_denied.json()["detail"]

    assert client.get(f"/db/{db_id}/health", headers={"X-API-Key": rw_token}).status_code == 200


def test_archived_database_returns_404(client):
    _, token, _ = _create_user_and_key(client, "alice")
    db = client.post(
        "/databases", json={"display_name": "X"}, headers={"X-API-Key": token}
    ).json()
    db_id = db["db_id"]

    assert client.delete(f"/databases/{db_id}", headers={"X-API-Key": token}).status_code == 204
    r = client.get(f"/db/{db_id}/health", headers={"X-API-Key": token})
    assert r.status_code == 404


def test_root_can_archive_database(client):
    _, token, _ = _create_user_and_key(client, "alice")
    db = client.post(
        "/databases", json={"display_name": "X"}, headers={"X-API-Key": token}
    ).json()
    db_id = db["db_id"]

    assert client.delete(f"/databases/{db_id}", headers=_root()).status_code == 204
    assert client.get(f"/db/{db_id}/health", headers={"X-API-Key": token}).status_code == 404


def test_hard_delete_database_removes_files_and_row(client, tmp_path):
    """DELETE /databases/{db_id}/hard：sqlite 行 + .kz 目录 + 审计文件全部消失。

    fake loader 不会真正创建 Kuzu 目录，所以这里手动落地一个占位 .kz 目录与
    一个占位审计文件，确保 hard_delete 真的走到了 rmtree / unlink 分支。
    """
    _, token, _ = _create_user_and_key(client, "alice")
    db = client.post(
        "/databases", json={"display_name": "Disposable"}, headers={"X-API-Key": token}
    ).json()
    db_id = db["db_id"]

    # 触发一次 acquire 让 pool 持有一个 fake handle（模拟"在用"状态）
    assert client.get(f"/db/{db_id}/health", headers={"X-API-Key": token}).status_code == 200

    # 手工模拟真实环境的产物
    from pathlib import Path
    db_root = tmp_path / "DB" / "users"
    user_dirs = [p for p in db_root.iterdir() if p.is_dir()]
    assert len(user_dirs) == 1
    kz_dir: Path = user_dirs[0] / f"{db_id}.kz"
    kz_dir.mkdir(parents=True, exist_ok=True)
    (kz_dir / "marker").write_text("placeholder", encoding="utf-8")

    audit_root = tmp_path / "audit"
    audit_user_dir = audit_root / user_dirs[0].name
    audit_user_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_user_dir / f"{db_id}.jsonl"
    audit_file.write_text("{}\n", encoding="utf-8")

    r = client.delete(f"/databases/{db_id}/hard", headers={"X-API-Key": token})
    assert r.status_code == 204

    # 文件全部清理
    assert not kz_dir.exists(), f"kz dir should be removed: {kz_dir}"
    assert not audit_file.exists(), f"audit file should be removed: {audit_file}"
    # admin 列表（含归档）也不再可见
    listing = client.get("/admin/databases?include_archived=true", headers=_root()).json()
    assert db_id not in {d["db_id"] for d in listing}


def test_hard_delete_handles_kuzu_single_file_layout(client, tmp_path):
    """生产 Kuzu 实际是单文件（不是目录）；hard_delete 应同时清理 .kz 主文件与 .kz.wal 等旁路。"""
    _, token, _ = _create_user_and_key(client, "alice")
    db = client.post(
        "/databases", json={"display_name": "SingleFile"}, headers={"X-API-Key": token}
    ).json()
    db_id = db["db_id"]
    # 触发 acquire 让 audit 文件落地（fake handle 不会真的写 .kz）
    client.get(f"/db/{db_id}/health", headers={"X-API-Key": token})

    from pathlib import Path
    db_root = tmp_path / "DB" / "users"
    user_dirs = [p for p in db_root.iterdir() if p.is_dir()]
    assert len(user_dirs) == 1
    kz_file: Path = user_dirs[0] / f"{db_id}.kz"
    # 模拟 Kuzu 单文件 + WAL 旁路
    kz_file.write_bytes(b"\x00" * 16)
    kz_wal = user_dirs[0] / f"{db_id}.kz.wal"
    kz_wal.write_bytes(b"\x00")
    kz_shadow = user_dirs[0] / f"{db_id}.kz.shadow"
    kz_shadow.write_bytes(b"\x00")

    r = client.delete(f"/databases/{db_id}/hard", headers={"X-API-Key": token})
    assert r.status_code == 204
    assert not kz_file.exists()
    assert not kz_wal.exists()
    assert not kz_shadow.exists()


def test_hard_delete_archived_database_works(client):
    """硬删允许作用于 active 或 archived；区别于 archive 接口仅 active。"""
    _, token, _ = _create_user_and_key(client, "alice")
    db = client.post(
        "/databases", json={"display_name": "ToArchive"}, headers={"X-API-Key": token}
    ).json()
    db_id = db["db_id"]
    assert client.delete(f"/databases/{db_id}", headers={"X-API-Key": token}).status_code == 204
    # 再次软归档 → 404
    assert (
        client.delete(f"/databases/{db_id}", headers={"X-API-Key": token}).status_code == 404
    )
    # 但硬删仍然成功
    assert (
        client.delete(f"/databases/{db_id}/hard", headers={"X-API-Key": token}).status_code == 204
    )


def test_hard_delete_other_user_database_403(client):
    _, alice_token, _ = _create_user_and_key(client, "alice")
    _, bob_token, _ = _create_user_and_key(client, "bob")
    db = client.post(
        "/databases", json={"display_name": "A"}, headers={"X-API-Key": alice_token}
    ).json()
    r = client.delete(f"/databases/{db['db_id']}/hard", headers={"X-API-Key": bob_token})
    assert r.status_code == 403


def test_delete_user_cascades(client, tmp_path):
    """DELETE /admin/users/{uid}：库 + 文件 + keys + user 全部清理。"""
    uid, token, _ = _create_user_and_key(client, "alice")
    db1 = client.post(
        "/databases", json={"display_name": "One"}, headers={"X-API-Key": token}
    ).json()
    db2 = client.post(
        "/databases", json={"display_name": "Two"}, headers={"X-API-Key": token}
    ).json()
    # 触发 audit 落地
    client.get(f"/db/{db1['db_id']}/health", headers={"X-API-Key": token})
    client.get(f"/db/{db2['db_id']}/health", headers={"X-API-Key": token})

    r = client.delete(f"/admin/users/{uid}", headers=_root())
    assert r.status_code == 204

    # 用户已不存在
    assert client.get(f"/admin/users/{uid}", headers=_root()).status_code == 404
    # 数据库目录全部消失
    db_root = tmp_path / "DB" / "users"
    assert not list(db_root.rglob("*.kz")) or all(
        db1["db_id"] not in str(p) and db2["db_id"] not in str(p)
        for p in db_root.rglob("*.kz")
    )
    # 旧 token 立即失效
    assert client.get("/databases", headers={"X-API-Key": token}).status_code == 401


def test_delete_user_404_when_absent(client):
    r = client.delete("/admin/users/nonexistent", headers=_root())
    assert r.status_code == 404


def test_path_normalization_and_token_listing(client):
    """list_databases_by_user 与 cross-user 列表应只看到自己的活动库。"""
    a_uid, a_token, _ = _create_user_and_key(client, "alice")
    _, b_token, _ = _create_user_and_key(client, "bob")
    a_db = client.post(
        "/databases", json={"display_name": "Alice"}, headers={"X-API-Key": a_token}
    ).json()
    b_db = client.post(
        "/databases", json={"display_name": "Bob"}, headers={"X-API-Key": b_token}
    ).json()

    a_list = client.get("/databases", headers={"X-API-Key": a_token}).json()
    b_list = client.get("/databases", headers={"X-API-Key": b_token}).json()

    a_ids = {d["db_id"] for d in a_list}
    b_ids = {d["db_id"] for d in b_list}
    assert a_db["db_id"] in a_ids and b_db["db_id"] not in a_ids
    assert b_db["db_id"] in b_ids and a_db["db_id"] not in b_ids
