"""/ui SPA 静态托管：用 FastAPI 直接服务前端构建产物，未命中文件兜底回 index.html。

- 生产期：前端构建产物拷到 src/service/static/ui/，由本模块挂载
- 开发期：UI 目录可能不存在或为空，此时挂载会被跳过；前端开发用 Vite dev server 走 proxy

所有 /ui/** 请求都不走鉴权——HTML 是公开的，鉴权在 fetch 调 /admin /databases /db /me 时由
后端 API 拦截。这与 /graph /logs 现有做法一致。
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parent.parent / "static" / "ui"
MOUNT_PATH = "/ui"


class SPAStaticFiles(StaticFiles):
    """SPA 兜底：未命中静态文件时返回 index.html，让 React Router 接管前端路由。"""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as ex:
            if ex.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def mount(app: FastAPI) -> None:
    """在 app 上挂载 /ui 静态目录。目录不存在或缺 index.html 时跳过并打 warning。"""
    index_html = UI_DIR / "index.html"
    if not UI_DIR.exists() or not index_html.exists():
        logger.warning(
            "UI directory not built yet (expected %s). Skipping /ui mount; "
            "build the front-end (cd web && npm run build) or run vite dev server.",
            UI_DIR,
        )
        return
    app.mount(MOUNT_PATH, SPAStaticFiles(directory=str(UI_DIR), html=True), name="ui")
    logger.info("Mounted SPA UI at %s -> %s", MOUNT_PATH, UI_DIR)
