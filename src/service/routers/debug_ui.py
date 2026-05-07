"""调试 UI：保留 /graph 与 /logs 顶层 HTML 页面，前端 JS 通过 ?db=&key= 选库并鉴权。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["debug-ui"])

_STATIC_DIR = Path(__file__).parent.parent / "static"


def _read(name: str) -> str:
    return (_STATIC_DIR / name).read_text(encoding="utf-8")


@router.get("/graph", response_class=HTMLResponse)
def graph_page() -> str:
    return _read("graph.html")


@router.get("/logs", response_class=HTMLResponse)
def logs_page() -> str:
    return _read("logs.html")
