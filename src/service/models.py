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


# ---------- 注册实体 ----------


class RegisteredEntity(BaseModel):
    id: str
    type: str = "UNKNOWN"
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    registered: bool = True
    status: str = "active"
    canonical_id: Optional[str] = None
    merged_from: list[str] = Field(default_factory=list)
    created_at: Optional[int] = None
    updated_at: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterEntityRequest(BaseModel):
    entity_id: str
    description: str
    entity_type: str = "UNKNOWN"
    aliases: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None
    # 可选：注册实体的同时一并写入 pattern；任何一条失败则整体回滚（包含本次新建的实体）。
    patterns: Optional[list["CreateEntityPatternRequest"]] = None


class UpdateEntityRequest(BaseModel):
    description: Optional[str] = None
    entity_type: Optional[str] = None
    add_aliases: Optional[list[str]] = None
    remove_aliases: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None


class RegisterEntityResponse(BaseModel):
    action: str  # created | promoted | updated
    existed_as_extracted: bool = False
    entity: dict[str, Any] = Field(default_factory=dict)
    # 内联注册时回填的 pattern 列表；未内联或全部失败回滚时为空。
    patterns: list["EntityPattern"] = Field(default_factory=list)


class ListEntitiesResponse(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0


# ---------- 注册实体 Pattern ----------


class EntityPattern(BaseModel):
    id: str
    entity_id: str
    content: str
    pattern_type: str = "preference"
    status: str = "active"
    created_at: Optional[int] = None
    updated_at: Optional[int] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateEntityPatternRequest(BaseModel):
    content: str
    pattern_type: str = "preference"
    metadata: Optional[dict[str, Any]] = None
    pattern_id: Optional[str] = None


class UpdateEntityPatternRequest(BaseModel):
    content: Optional[str] = None
    pattern_type: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class EntityPatternResponse(BaseModel):
    action: str
    pattern: EntityPattern


class DeleteEntityPatternResponse(BaseModel):
    action: str  # "archived" | "deleted"
    pattern: EntityPattern


class ListEntityPatternsResponse(BaseModel):
    items: list[EntityPattern] = Field(default_factory=list)
    total: int = 0
    query: str = ""


class BatchCreateEntityPatternsRequest(BaseModel):
    patterns: list[CreateEntityPatternRequest]
    atomic: bool = True


class BatchCreateEntityPatternFailure(BaseModel):
    index: int
    content: str = ""
    error: str


class BatchCreateEntityPatternsResponse(BaseModel):
    created: list[EntityPattern] = Field(default_factory=list)
    failed: list[BatchCreateEntityPatternFailure] = Field(default_factory=list)
    atomic: bool = True


# 解析 RegisterEntityRequest/Response 中对 CreateEntityPatternRequest / EntityPattern 的前向引用。
RegisterEntityRequest.model_rebuild()
RegisterEntityResponse.model_rebuild()
