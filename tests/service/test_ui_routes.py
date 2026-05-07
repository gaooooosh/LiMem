"""Tests for the SPA UI mount.

dev/单测环境下 src/service/static/ui/index.html 可能不存在；用 monkeypatch 在 tmp_path
里模拟一个最小 dist 目录，再让 ui_router.UI_DIR 指向它。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def app_with_ui(tmp_path, monkeypatch, app_factory):
    """伪造一个 dist 目录指过去；测试完毕自动恢复。"""
    ui_dir = tmp_path / "fake_ui_dist"
    ui_dir.mkdir(parents=True)
    (ui_dir / "index.html").write_text(
        "<!doctype html><html><body><div id='root'>SPA</div></body></html>",
        encoding="utf-8",
    )
    assets = ui_dir / "assets"
    assets.mkdir()
    (assets / "main.js").write_text("// fake js", encoding="utf-8")

    from service.routers import ui as ui_mod

    monkeypatch.setattr(ui_mod, "UI_DIR", Path(ui_dir))
    yield app_factory


@pytest.fixture()
def ui_client(app_with_ui):
    from fastapi.testclient import TestClient

    app = app_with_ui()
    with TestClient(app) as c:
        yield c


def test_ui_login_returns_html(ui_client):
    r = ui_client.get("/ui/login")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "SPA" in r.text


def test_ui_unknown_path_falls_back_to_index(ui_client):
    """SPA 兜底：未知前端路由（React Router 接管）也应返回 index.html。"""
    r = ui_client.get("/ui/console/db/some-random-id")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "SPA" in r.text


def test_ui_assets_served(ui_client):
    """实际存在的静态文件正常返回，不走兜底。"""
    r = ui_client.get("/ui/assets/main.js")
    assert r.status_code == 200
    assert "fake js" in r.text


def test_ui_skipped_when_dist_missing(client):
    """没有 dist 时（单测默认场景），/ui 路由不被挂载，访问应 404。"""
    r = client.get("/ui/login")
    assert r.status_code == 404


def test_api_routes_unaffected_by_ui(ui_client, root_headers):
    """挂载 /ui 后，业务 API 仍可正常访问，前缀不冲突。"""
    assert ui_client.get("/me", headers=root_headers).status_code == 200
    assert ui_client.get("/admin/users", headers=root_headers).status_code == 200
