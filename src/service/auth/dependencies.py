"""FastAPI 依赖项：API key 提取与库上下文注入。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional

from fastapi import Depends, Header, HTTPException, Request, status

from ..pool import LtmHandle


@dataclass
class CallerCtx:
    user_id: str
    key_id: str
    is_root: bool
    scopes: set[str]

    @property
    def can_admin(self) -> bool:
        return self.is_root or "admin" in self.scopes

    @property
    def can_write(self) -> bool:
        return self.is_root or "w" in self.scopes

    @property
    def can_read(self) -> bool:
        return self.is_root or "r" in self.scopes or "w" in self.scopes


def _extract_token(authorization: Optional[str], x_api_key: Optional[str], query_key: Optional[str]) -> str:
    if x_api_key:
        return x_api_key.strip()
    if authorization:
        v = authorization.strip()
        if v.lower().startswith("bearer "):
            return v[7:].strip()
        return v
    if query_key:
        return query_key.strip()
    return ""


def get_caller(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    key: Optional[str] = None,  # 仅供调试 UI HTML 在 query string 上传 ?key=
) -> CallerCtx:
    token = _extract_token(authorization, x_api_key, key)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing api key")

    root_token: str = request.app.state.root_api_key
    if root_token and token == root_token:
        return CallerCtx(
            user_id="__root__", key_id="__root__", is_root=True, scopes={"r", "w", "admin"}
        )

    repo = request.app.state.auth_repo
    api_key = repo.lookup_by_token(token)
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    repo.touch_last_used(api_key.id)
    return CallerCtx(
        user_id=api_key.user_id,
        key_id=api_key.id,
        is_root=False,
        scopes=api_key.scope_set or {"r", "w"},
    )


def require_admin(caller: CallerCtx = Depends(get_caller)) -> CallerCtx:
    if not caller.can_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin scope required")
    return caller


def require_write(caller: CallerCtx = Depends(get_caller)) -> CallerCtx:
    if not caller.can_write:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="write scope required")
    return caller


def get_ltm_context(
    db_id: str,
    request: Request,
    caller: CallerCtx = Depends(get_caller),
) -> Iterator[LtmHandle]:
    """通用业务路由依赖：定位库 + 校验归属 + 从池里 acquire handle。"""
    mgr = request.app.state.dbmgr
    dbr = mgr.repo.get_database(db_id)
    if not dbr or dbr.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="database not found")
    if not (caller.is_root or dbr.owner_user_id == caller.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="no access to this database")
    with mgr.pool.acquire(dbr) as handle:
        yield handle


def get_ltm_context_write(
    handle: LtmHandle = Depends(get_ltm_context),
    caller: CallerCtx = Depends(get_caller),
) -> LtmHandle:
    if not caller.can_write:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="write scope required")
    return handle
