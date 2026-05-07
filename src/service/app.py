"""FastAPI application for the multi-tenant LiMem service."""

from __future__ import annotations

import logging
import os
import sys
import warnings
from contextlib import asynccontextmanager
from typing import Any

import jieba
from fastapi import FastAPI

from .auth import AuthRepository
from .database_manager import DatabaseManager
from .errors import install_error_handlers
from .routers import admin as admin_router
from .routers import databases as databases_router
from .routers import debug_ui as debug_router
from .routers import graph as graph_router
from .routers import me as me_router
from .routers import memory as memory_router
from .routers import ui as ui_router

logger = logging.getLogger(__name__)


DEFAULTS = {
    "AUTH_DB_PATH": "./DB/auth.sqlite",
    "MULTI_DB_BASE_DIR": "./DB",
    "MULTI_AUDIT_BASE_DIR": "./outputs/audit",
    "LTM_POOL_MAX_SIZE": "16",
    "LTM_POOL_IDLE_TIMEOUT_SEC": "1800",
}


def _read_env(name: str) -> str:
    return os.getenv(name, DEFAULTS.get(name, ""))


def _warn_deprecated() -> None:
    for legacy in ("SERVICE_DB_PATH", "SERVICE_AUDIT_LOG_PATH"):
        if os.getenv(legacy):
            warnings.warn(
                f"{legacy} is ignored in multi-tenant mode; use MULTI_DB_BASE_DIR / "
                f"MULTI_AUDIT_BASE_DIR + per-database configuration instead.",
                DeprecationWarning,
                stacklevel=2,
            )


def create_app() -> FastAPI:
    _warn_deprecated()

    root_api_key = (os.getenv("ROOT_API_KEY") or "").strip()
    if not root_api_key:
        # 启动期 fail-fast：避免裸跑无管理员的服务
        sys.stderr.write(
            "FATAL: ROOT_API_KEY is required for multi-tenant LiMem service.\n"
            "Set it in your .env or environment before starting.\n"
        )
        raise SystemExit(2)

    auth_db_path = _read_env("AUTH_DB_PATH")
    base_db_dir = _read_env("MULTI_DB_BASE_DIR")
    base_audit_dir = _read_env("MULTI_AUDIT_BASE_DIR")
    pool_max_size = int(_read_env("LTM_POOL_MAX_SIZE"))
    pool_idle_timeout = float(_read_env("LTM_POOL_IDLE_TIMEOUT_SEC"))

    repo = AuthRepository(auth_db_path)
    dbmgr = DatabaseManager(
        repo=repo,
        base_db_dir=base_db_dir,
        base_audit_dir=base_audit_dir,
        pool_max_size=pool_max_size,
        pool_idle_timeout=pool_idle_timeout,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.auth_repo = repo
        app.state.dbmgr = dbmgr
        app.state.root_api_key = root_api_key
        dbmgr.start()
        # 提前 warm up 中文分词，避免首次 BM25 ingest 卡顿
        try:
            jieba.initialize()
        except Exception:
            logger.exception("jieba initialize failed")
        try:
            yield
        finally:
            dbmgr.shutdown()
            repo.close()

    app = FastAPI(title="LiMem Service (multi-tenant)", lifespan=lifespan)
    install_error_handlers(app)

    app.include_router(admin_router.router)
    app.include_router(databases_router.router)
    app.include_router(memory_router.router)
    app.include_router(graph_router.router)
    app.include_router(debug_router.router)
    app.include_router(me_router.router)
    # SPA 静态托管：必须最后挂载，避免 /ui 前缀影响 API 路由匹配
    ui_router.mount(app)

    @app.get("/")
    def index() -> dict[str, Any]:
        return {
            "service": "LiMem multi-tenant",
            "status": "ok",
            "endpoints": {
                "admin": "/admin/*",
                "databases": "/databases (POST/GET/DELETE)",
                "per_db": "/db/{db_id}/{ingest,query,evolve,health,stats,...}",
                "me": "/me, /me/keys (GET/POST/DELETE)",
                "debug_ui": "/graph?db=...&key=..., /logs?db=...&key=...",
                "console_ui": "/ui/login, /ui/console, /ui/admin",
            },
        }

    return app
