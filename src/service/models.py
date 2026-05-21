"""Pydantic models for the LiMem HTTP service."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class RecallTaskRequest(BaseModel):
    task: str
    limit: int = Field(default=5, ge=1, le=20)
    include_debug: bool = False


class RecallTaskResponse(BaseModel):
    prompt_text: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


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
    # 可选：注册实体的同时一并写入 pattern（v2：单文档 markdown）。
    # 失败时回滚：删除该 pattern；本次新建的实体节点一并 unregister。
    pattern: Optional["PutEntityPatternRequest"] = None

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_patterns_field(cls, data: Any) -> Any:
        # v1 兼容性 hard break：旧客户端传 patterns:[...] → 422 + 可读提示。
        if isinstance(data, dict) and "patterns" in data:
            raise ValueError(
                "'patterns' field is deprecated; use 'pattern' (single object). "
                "See API_DOC.md §11.6 Breaking Change."
            )
        return data


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
    # 内联注册时回填的 pattern 单对象；未内联 / 全部回滚 / 实体已存在且未覆盖时为 None。
    pattern: Optional["EntityPattern"] = None


class ListEntitiesResponse(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0


# ---------- 注册实体 Pattern（v2：单文档 markdown） ----------


class EntityPattern(BaseModel):
    """绑定到 Entity 的唯一 markdown pattern 文档。"""
    id: str
    entity_id: str
    content: str
    status: str = "active"  # 当前恒为 active；保留字段以备未来扩展
    created_at: Optional[int] = None
    updated_at: Optional[int] = None


class PutEntityPatternRequest(BaseModel):
    """Upsert 请求。仅允许 content 字段；其它字段（pattern_type/pattern_id/metadata）一律 422。"""
    model_config = ConfigDict(extra="forbid")
    content: str

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("content must not be blank")
        return v


class PutEntityPatternResponse(BaseModel):
    action: Literal["created", "updated"]
    pattern: EntityPattern


class DeleteEntityPatternResponse(BaseModel):
    pattern: EntityPattern


class MatchedSection(BaseModel):
    heading: str
    score: int
    char_offset: int


class RecallEntityPatternResponse(BaseModel):
    """Pattern 召回响应。无 pattern 时 mode/content 仍合法但 pattern=None、sections=[]。"""
    mode: Literal["full", "section"]
    content: str
    total_chars: int
    matched_sections: list[MatchedSection] = Field(default_factory=list)
    pattern: Optional[EntityPattern] = None


# 解析 RegisterEntityRequest/Response 对 PutEntityPatternRequest / EntityPattern 的前向引用。
RegisterEntityRequest.model_rebuild()
RegisterEntityResponse.model_rebuild()
