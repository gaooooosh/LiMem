"""Pydantic models for the LiMem HTTP service."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------- 业务请求 / 响应 ----------


class IngestRequest(BaseModel):
    data: Any
    timestamp: int | None = None


class IngestResponse(BaseModel):
    event_id: str
    summary: str
    is_new: bool
    entities_created: int
    event_count: int


class QueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1)


class QueryResult(BaseModel):
    event_id: str
    summary: str
    action: str
    causality: str
    timestamp: int
    score: float


class QueryResponse(BaseModel):
    results: list[QueryResult]
    total: int


class EvolveResponse(BaseModel):
    message: str
    details: dict[str, int] = Field(default_factory=dict)


class DeleteNodeRequest(BaseModel):
    memory_id: str
    kind: str = "event"
    hard_delete: bool = False


class UpdateNodeRequest(BaseModel):
    memory_id: str
    kind: str = "event"
    fields: dict[str, Any]
    evolve: bool = False


class WriteNodeRequest(BaseModel):
    kind: str = "event"
    item: dict[str, Any]
    entity_ids: list[Any] = Field(default_factory=list)
    evolve: bool = True


class WriteNodeResponse(BaseModel):
    kind: str
    action: str
    item: dict[str, Any] = Field(default_factory=dict)
    entity_links: int = 0


# ---------- 鉴权 / 管理 ----------


class CreateUserRequest(BaseModel):
    name: str


class UserView(BaseModel):
    id: str
    name: str
    created_at: str


class IssueKeyRequest(BaseModel):
    label: str = ""
    scopes: str = "r,w"  # csv: r,w,admin


class ApiKeyView(BaseModel):
    id: str
    user_id: str
    label: str
    scopes: str
    created_at: str
    last_used_at: Optional[str] = None
    revoked_at: Optional[str] = None


class IssueKeyResponse(BaseModel):
    key: ApiKeyView
    token: str = Field(description="The plaintext token. Only returned once on issue; store it securely.")


class UserDetailResponse(BaseModel):
    user: UserView
    keys: list[ApiKeyView] = Field(default_factory=list)
    databases: list["DatabaseView"] = Field(default_factory=list)


class DatabaseView(BaseModel):
    db_id: str
    owner_user_id: str
    display_name: str
    created_at: str
    last_accessed_at: Optional[str] = None
    status: str = "active"


class CreateDatabaseRequest(BaseModel):
    display_name: str


UserDetailResponse.model_rebuild()
