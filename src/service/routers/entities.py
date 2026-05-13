"""/db/{db_id}/api/entities 注册实体管理路由。

提供 4 个端点：
- GET    /db/{db_id}/api/entities                   列出所有注册实体（不含 embedding）
- GET    /db/{db_id}/api/entities/{entity_id}       获取单个注册实体详情
- POST   /db/{db_id}/api/entities                   注册新实体（创建/晋升/更新三态）
                                                    可选 `patterns` 字段：内联同时注册 pattern，
                                                    任何一条失败则整体回滚（已写入的 pattern 硬删；
                                                    若本次新建了实体节点还会一并 unregister）。
- PATCH  /db/{db_id}/api/entities/{entity_id}       更新已注册实体属性

写操作使用与 graph.py 一致的审计模板：trace + write_lock + graph_delta。
错误处理：未找到走显式 HTTPException(404)，其它 ValueError 由 install_error_handlers 统一映射 400。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth.dependencies import get_ltm_context, get_ltm_context_write
from ..models import (
    EntityPattern,
    ListEntitiesResponse,
    RegisterEntityRequest,
    RegisterEntityResponse,
    RegisteredEntity,
    UpdateEntityRequest,
)
from ..ops import rebuild_index as _rebuild_index
from ..pool import LtmHandle


router = APIRouter(prefix="/db/{db_id}/api/entities", tags=["entities"])


@router.get("", response_model=ListEntitiesResponse)
def list_entities(handle: LtmHandle = Depends(get_ltm_context)) -> ListEntitiesResponse:
    items = handle.ltm.list_registered_entities()
    return ListEntitiesResponse(items=items, total=len(items))


@router.get("/{entity_id:path}", response_model=RegisteredEntity)
def get_entity(
    entity_id: str,
    handle: LtmHandle = Depends(get_ltm_context),
) -> dict[str, Any]:
    ent = handle.ltm.get_registered_entity(entity_id)
    if ent is None:
        raise HTTPException(status_code=404, detail=f"Registered entity not found: {entity_id}")
    return ent


@router.post("", response_model=RegisterEntityResponse)
def register_entity(
    request: RegisterEntityRequest,
    handle: LtmHandle = Depends(get_ltm_context_write),
) -> RegisterEntityResponse:
    audit = handle.audit
    inline_patterns = list(request.patterns or [])
    payload_for_trace = request.model_dump()
    with audit.trace("entity_register", payload_for_trace) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            result = handle.ltm.register_entity(
                entity_id=request.entity_id,
                description=request.description,
                entity_type=request.entity_type,
                aliases=request.aliases,
                metadata=request.metadata,
            )
            audit.write(
                trace_id,
                "entity_operation",
                "register_completed",
                entity_type="entity",
                entity_id=request.entity_id,
                details={"result": result},
            )

            created_patterns: list[dict[str, Any]] = []
            if inline_patterns:
                created_patterns = _create_inline_patterns(
                    handle=handle,
                    trace_id=trace_id,
                    entity_id=request.entity_id,
                    patterns=inline_patterns,
                    entity_was_created=result.get("action") == "created",
                )

            size = _rebuild_index(handle)
            audit.write(trace_id, "index", "bm25_rebuilt", details={"index_size": size})
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="entity_register")

    return RegisterEntityResponse(
        action=result["action"],
        existed_as_extracted=bool(result.get("existed_as_extracted", False)),
        entity=result.get("entity") or {},
        patterns=[EntityPattern(**p) for p in created_patterns],
    )


@router.patch("/{entity_id:path}", response_model=RegisterEntityResponse)
def update_entity(
    entity_id: str,
    request: UpdateEntityRequest,
    handle: LtmHandle = Depends(get_ltm_context_write),
) -> RegisterEntityResponse:
    audit = handle.audit
    payload = request.model_dump(exclude_none=True)
    with audit.trace("entity_update", {"entity_id": entity_id, **payload}) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            try:
                result = handle.ltm.update_entity(entity_id, **payload)
            except ValueError as e:
                msg = str(e)
                # "Registered entity not found: ..." → 404；其他 ValueError 走默认 400 handler
                if "not found" in msg.lower():
                    raise HTTPException(status_code=404, detail=msg)
                raise
            audit.write(
                trace_id,
                "entity_operation",
                "update_completed",
                entity_type="entity",
                entity_id=entity_id,
                details={"result": result},
            )
            size = _rebuild_index(handle)
            audit.write(trace_id, "index", "bm25_rebuilt", details={"index_size": size})
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="entity_update")
    return RegisterEntityResponse(**result)


# ---------- 内部工具 ----------


def _create_inline_patterns(
    *,
    handle: LtmHandle,
    trace_id: Any,
    entity_id: str,
    patterns: list[Any],
    entity_was_created: bool,
) -> list[dict[str, Any]]:
    """注册流程内的"附带 pattern 写入"，原子语义：失败回滚已写 pattern；
    若本次注册新建了实体节点，连带 unregister_entity。
    """
    audit = handle.audit
    created: list[dict[str, Any]] = []
    try:
        for idx, p in enumerate(patterns):
            payload = p.model_dump()
            try:
                result = handle.ltm.create_entity_pattern(
                    entity_id=entity_id,
                    content=payload["content"],
                    pattern_type=payload.get("pattern_type") or "preference",
                    metadata=payload.get("metadata"),
                    pattern_id=payload.get("pattern_id"),
                )
            except Exception as exc:
                # 回滚：先删已写 pattern，再视情况 unregister 实体
                _rollback_inline_patterns(handle, entity_id, created)
                if entity_was_created:
                    try:
                        handle.ltm.unregister_entity(entity_id)
                    except Exception:
                        # 回滚阶段尽力而为，已记录审计
                        pass
                audit.write(
                    trace_id,
                    "entity_pattern",
                    "inline_create_failed",
                    entity_type="entity",
                    entity_id=entity_id,
                    details={
                        "failed_index": idx,
                        "error": str(exc),
                        "rolled_back_patterns": [p["id"] for p in created],
                        "rolled_back_entity": entity_was_created,
                    },
                )
                # 透传错误类型：ValueError → 400/404，其它 → 500
                if isinstance(exc, ValueError):
                    msg = str(exc)
                    if "not found" in msg.lower():
                        raise HTTPException(status_code=404, detail=msg) from exc
                    raise HTTPException(status_code=400, detail=msg) from exc
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            created.append(result["pattern"])
        audit.write(
            trace_id,
            "entity_pattern",
            "inline_create_completed",
            entity_type="entity",
            entity_id=entity_id,
            details={"pattern_ids": [p["id"] for p in created]},
        )
        return created
    except HTTPException:
        raise


def _rollback_inline_patterns(
    handle: LtmHandle,
    entity_id: str,
    created: list[dict[str, Any]],
) -> None:
    for p in created:
        try:
            handle.ltm.delete_entity_pattern(
                entity_id=entity_id,
                pattern_id=p["id"],
                hard_delete=True,
            )
        except Exception:
            continue
