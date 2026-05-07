"""/admin/... 管理路由：仅 ROOT_API_KEY 或 admin scope 可调用。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth.dependencies import CallerCtx, require_admin
from ..models import (
    ApiKeyView,
    CreateUserRequest,
    DatabaseView,
    IssueKeyRequest,
    IssueKeyResponse,
    UserDetailResponse,
    UserView,
)


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _to_user_view(u) -> UserView:
    return UserView(id=u.id, name=u.name, created_at=u.created_at)


def _to_key_view(k) -> ApiKeyView:
    return ApiKeyView(
        id=k.id,
        user_id=k.user_id,
        label=k.label,
        scopes=k.scopes,
        created_at=k.created_at,
        last_used_at=k.last_used_at,
        revoked_at=k.revoked_at,
    )


def _to_db_view(d) -> DatabaseView:
    return DatabaseView(
        db_id=d.db_id,
        owner_user_id=d.owner_user_id,
        display_name=d.display_name,
        created_at=d.created_at,
        last_accessed_at=d.last_accessed_at,
        status=d.status,
    )


@router.post("/users", response_model=UserView, status_code=201)
def create_user(body: CreateUserRequest, request: Request) -> UserView:
    repo = request.app.state.auth_repo
    user = repo.create_user(body.name)
    return _to_user_view(user)


@router.get("/users", response_model=list[UserView])
def list_users(request: Request) -> list[UserView]:
    repo = request.app.state.auth_repo
    return [_to_user_view(u) for u in repo.list_users()]


@router.get("/users/{user_id}", response_model=UserDetailResponse)
def get_user_detail(user_id: str, request: Request) -> UserDetailResponse:
    repo = request.app.state.auth_repo
    user = repo.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    keys = [_to_key_view(k) for k in repo.list_keys_by_user(user_id)]
    dbs = [_to_db_view(d) for d in repo.list_databases_by_user(user_id, include_archived=True)]
    return UserDetailResponse(user=_to_user_view(user), keys=keys, databases=dbs)


@router.post("/users/{user_id}/keys", response_model=IssueKeyResponse, status_code=201)
def issue_key(user_id: str, body: IssueKeyRequest, request: Request) -> IssueKeyResponse:
    repo = request.app.state.auth_repo
    token, key = repo.issue_key(user_id, label=body.label, scopes=body.scopes)
    return IssueKeyResponse(key=_to_key_view(key), token=token)


@router.delete("/keys/{key_id}", status_code=204)
def revoke_key(key_id: str, request: Request) -> None:
    repo = request.app.state.auth_repo
    repo.revoke_key(key_id)
    return None


@router.get("/databases", response_model=list[DatabaseView])
def list_all_databases(request: Request, include_archived: bool = True) -> list[DatabaseView]:
    repo = request.app.state.auth_repo
    return [_to_db_view(d) for d in repo.list_all_databases(include_archived=include_archived)]


@router.get("/health")
def admin_health(request: Request, _: CallerCtx = Depends(require_admin)) -> dict:
    """不依赖具体库的全局健康检查；用于 docker healthcheck。"""
    pool_stats = request.app.state.dbmgr.pool.stats()
    return {"status": "ok", "pool": pool_stats}
