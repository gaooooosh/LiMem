"""/me 路由：用当前 API Key 反查自身信息与自助管理 Key。

与 /admin 不同，这里只允许 caller 操作"自己"的资源；scope 由后端强制收敛
（不能签发超出 caller 自身 scope 的 Key），避免自我提权。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth.dependencies import CallerCtx, get_caller
from ..models import ApiKeyView, IssueKeyRequest, IssueKeyResponse


router = APIRouter(tags=["me"])


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


@router.get("/me")
def whoami(request: Request, caller: CallerCtx = Depends(get_caller)) -> dict:
    """返回当前 caller 身份与权限。前端登录页用此端点探测 Key 有效性与 scope。"""
    scopes_sorted = sorted(caller.scopes)
    if caller.is_root:
        return {
            "is_root": True,
            "user_id": caller.user_id,
            "user_name": "ROOT",
            "key_id": caller.key_id,
            "key_label": "root",
            "scopes": scopes_sorted,
            "created_at": None,
            "last_used_at": None,
        }
    repo = request.app.state.auth_repo
    user = repo.get_user(caller.user_id)
    key = repo.get_key(caller.key_id)
    if not user or not key:
        # 理论上不会发生：能通过 get_caller 说明 key 有效
        raise HTTPException(status_code=401, detail="caller user/key not found")
    return {
        "is_root": False,
        "user_id": user.id,
        "user_name": user.name,
        "key_id": key.id,
        "key_label": key.label,
        "scopes": scopes_sorted,
        "created_at": user.created_at,
        "last_used_at": key.last_used_at,
    }


@router.get("/me/keys", response_model=list[ApiKeyView])
def list_my_keys(
    request: Request, caller: CallerCtx = Depends(get_caller)
) -> list[ApiKeyView]:
    """列出当前 user 的所有 Key。root 没有 SQL 落库的 Key，返回空列表让前端少分支。"""
    if caller.is_root:
        return []
    repo = request.app.state.auth_repo
    return [_to_key_view(k) for k in repo.list_keys_by_user(caller.user_id)]


@router.post("/me/keys", response_model=IssueKeyResponse, status_code=201)
def issue_my_key(
    body: IssueKeyRequest,
    request: Request,
    caller: CallerCtx = Depends(get_caller),
) -> IssueKeyResponse:
    """自助签发新 Key。强制 requested_scopes ⊆ caller.scopes 防止自我提权。"""
    if caller.is_root:
        raise HTTPException(
            status_code=400,
            detail="root key is loaded from env, cannot be issued via /me",
        )
    requested = {s.strip().lower() for s in (body.scopes or "").split(",") if s.strip()}
    if not requested:
        requested = {"r"}
    invalid = requested - {"r", "w", "admin"}
    if invalid:
        raise HTTPException(status_code=400, detail=f"unknown scope: {sorted(invalid)}")
    if not requested.issubset(caller.scopes):
        raise HTTPException(
            status_code=403,
            detail=(
                f"cannot escalate scopes: requested={sorted(requested)}, "
                f"allowed={sorted(caller.scopes)}"
            ),
        )
    repo = request.app.state.auth_repo
    token, key = repo.issue_key(
        caller.user_id, label=body.label, scopes=",".join(sorted(requested))
    )
    return IssueKeyResponse(key=_to_key_view(key), token=token)


@router.delete("/me/keys/{key_id}", status_code=204)
def revoke_my_key(
    key_id: str, request: Request, caller: CallerCtx = Depends(get_caller)
) -> None:
    """撤销自己的 Key；不允许跨 user 删除。root 例外可删任何 Key。"""
    repo = request.app.state.auth_repo
    key = repo.get_key(key_id)
    if not key:
        raise HTTPException(status_code=404, detail="key not found")
    if not caller.is_root and key.user_id != caller.user_id:
        # 故意用 404 而不是 403，避免泄漏 key_id 是否存在
        raise HTTPException(status_code=404, detail="key not found")
    if key.is_revoked:
        # 幂等：已撤销不报错
        return None
    repo.revoke_key(key_id)
    return None
