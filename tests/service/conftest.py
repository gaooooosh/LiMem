"""共享 fixture：每个测试一个独立的 SQLite + 临时 base dir + fake Ltm loader。"""

from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path

import pytest


SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture()
def app_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("ROOT_API_KEY", "root-test-token")
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "auth.sqlite"))
    monkeypatch.setenv("MULTI_DB_BASE_DIR", str(tmp_path / "DB"))
    monkeypatch.setenv("MULTI_AUDIT_BASE_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("LTM_POOL_MAX_SIZE", "4")
    monkeypatch.setenv("LTM_POOL_IDLE_TIMEOUT_SEC", "60")
    monkeypatch.delenv("SERVICE_DB_PATH", raising=False)
    monkeypatch.delenv("SERVICE_AUDIT_LOG_PATH", raising=False)

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

    if "service.app" in sys.modules:
        importlib.reload(sys.modules["service.app"])
    from service.app import create_app

    return create_app


@pytest.fixture()
def client(app_factory):
    from fastapi.testclient import TestClient

    app = app_factory()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def root_headers():
    return {"X-API-Key": "root-test-token"}


@pytest.fixture()
def make_user_key(client, root_headers):
    """返回 helper：创建 user 并签发 key，返回 (user_id, token, key_id)。"""

    def _make(name: str, scopes: str = "r,w"):
        u = client.post("/admin/users", json={"name": name}, headers=root_headers).json()
        issue = client.post(
            f"/admin/users/{u['id']}/keys",
            json={"label": "test", "scopes": scopes},
            headers=root_headers,
        ).json()
        return u["id"], issue["token"], issue["key"]["id"]

    return _make


# ---------- Pattern 路由专用 fixture：真实 LTM 后端（基于 Kuzu） ----------


@pytest.fixture()
def pattern_app_factory(tmp_path, monkeypatch):
    """Pattern 套件需要真实 Kuzu 后端（普通 app_factory 提供 _FakeLtm 不够用）。"""
    monkeypatch.setenv("ROOT_API_KEY", "root-test-token")
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "auth.sqlite"))
    monkeypatch.setenv("MULTI_DB_BASE_DIR", str(tmp_path / "DB"))
    monkeypatch.setenv("MULTI_AUDIT_BASE_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("LTM_POOL_MAX_SIZE", "4")
    monkeypatch.setenv("LTM_POOL_IDLE_TIMEOUT_SEC", "60")
    monkeypatch.delenv("SERVICE_DB_PATH", raising=False)
    monkeypatch.delenv("SERVICE_AUDIT_LOG_PATH", raising=False)

    from limem.factory import create_ltm
    from service import database_manager as dm_mod
    from service.audit import ServiceAuditLogger, install_store_audit_proxy
    from service.pool import LtmHandle

    class _NoopClient:
        def get_embedding(self, text):
            return None

        def generate(self, *args, **kwargs):
            return ""

    class _FakeBM25:
        size = 0

        def search(self, q, k):
            return []

        def rebuild(self, events):
            pass

    def fake_load(self, dbr):
        ltm = create_ltm(
            db_path=dbr.db_path,
            config={
                "embedding_client": _NoopClient(),
                "llm_client": _NoopClient(),
                "enable_dynamic_evolution": False,
            },
        )
        audit_path = tmp_path / "audit" / dbr.owner_user_id / f"{dbr.db_id}.jsonl"
        audit = ServiceAuditLogger(str(audit_path))
        install_store_audit_proxy(ltm, audit)
        return LtmHandle(
            db_id=dbr.db_id,
            ltm=ltm,
            audit=audit,
            bm25=_FakeBM25(),
            write_lock=threading.Lock(),
        )

    monkeypatch.setattr(dm_mod.DatabaseManager, "_load_handle", fake_load, raising=True)

    if "service.app" in sys.modules:
        importlib.reload(sys.modules["service.app"])
    from service.app import create_app

    return create_app


@pytest.fixture()
def pattern_client(pattern_app_factory):
    from fastapi.testclient import TestClient

    app = pattern_app_factory()
    with TestClient(app) as c:
        yield c
