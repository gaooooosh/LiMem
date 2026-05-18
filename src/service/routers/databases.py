"""/databases 用户自助路由：建库、列库、归档自己的库。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth.dependencies import CallerCtx, get_caller
from ..models import CreateDatabaseRequest, DatabaseView


router = APIRouter(prefix="/databases", tags=["databases"])


def _to_view(d) -> DatabaseView:
    return DatabaseView(
        db_id=d.db_id,
        owner_user_id=d.owner_user_id,
        display_name=d.display_name,
        created_at=d.created_at,
        last_accessed_at=d.last_accessed_at,
        status=d.status,
    )


@router.post("", response_model=DatabaseView, status_code=201)
def create_my_database(
    body: CreateDatabaseRequest,
    request: Request,
    caller: CallerCtx = Depends(get_caller),
) -> DatabaseView:
    if not caller.can_write:
        raise HTTPException(status_code=403, detail="write scope required")
    if caller.is_root:
        raise HTTPException(
            status_code=400,
            detail="root key must create databases on behalf of a real user via /admin",
        )
    mgr = request.app.state.dbmgr
    db = mgr.create_for_user(caller.user_id, body.display_name)
    return _to_view(db)


@router.get("", response_model=list[DatabaseView])
def list_my_databases(
    request: Request, caller: CallerCtx = Depends(get_caller)
) -> list[DatabaseView]:
    repo = request.app.state.auth_repo
    if caller.is_root:
        return [_to_view(d) for d in repo.list_all_databases(include_archived=False)]
    return [
        _to_view(d) for d in repo.list_databases_by_user(caller.user_id, include_archived=False)
    ]


@router.delete("/{db_id}", status_code=204)
def archive_my_database(
    db_id: str, request: Request, caller: CallerCtx = Depends(get_caller)
) -> None:
    if not caller.can_write:
        raise HTTPException(status_code=403, detail="write scope required")
    repo = request.app.state.auth_repo
    mgr = request.app.state.dbmgr
    db = repo.get_database(db_id)
    if not db or db.status != "active":
        raise HTTPException(status_code=404, detail="database not found")
    if not (caller.is_root or db.owner_user_id == caller.user_id):
        raise HTTPException(status_code=403, detail="not your database")
    mgr.archive(db_id)
    return None


@router.delete("/{db_id}/hard", status_code=204)
def hard_delete_my_database(
    db_id: str, request: Request, caller: CallerCtx = Depends(get_caller)
) -> None:
    """彻底销毁一个库：池驱逐 + 文件系统清理 + sqlite 行删除。

    与 archive 不同：本接口对 active / archived 两种状态都允许；不可逆。
    审计：DatabaseManager.hard_delete 内部已记录 logger.exception 错误路径。
    """
    if not caller.can_write:
        raise HTTPException(status_code=403, detail="write scope required")
    repo = request.app.state.auth_repo
    mgr = request.app.state.dbmgr
    db = repo.get_database(db_id)
    if not db:
        raise HTTPException(status_code=404, detail="database not found")
    if not (caller.is_root or db.owner_user_id == caller.user_id):
        raise HTTPException(status_code=403, detail="not your database")
    mgr.hard_delete(db_id)
    return None
