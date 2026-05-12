"""/db/{db_id}/api/entities 注册实体管理路由。

提供 4 个端点：
- GET    /db/{db_id}/api/entities                   列出所有注册实体（不含 embedding）
- GET    /db/{db_id}/api/entities/{entity_id}       获取单个注册实体详情
- POST   /db/{db_id}/api/entities                   注册新实体（创建/晋升/更新三态）
- PATCH  /db/{db_id}/api/entities/{entity_id}       更新已注册实体属性

写操作使用与 graph.py 一致的审计模板：trace + write_lock + graph_delta。
错误处理：未找到走显式 HTTPException(404)，其它 ValueError 由 install_error_handlers 统一映射 400。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth.dependencies import get_ltm_context, get_ltm_context_write
from ..models import (
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
    with audit.trace("entity_register", request.model_dump()) as trace_id:
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
            size = _rebuild_index(handle)
            audit.write(trace_id, "index", "bm25_rebuilt", details={"index_size": size})
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="entity_register")
    return RegisterEntityResponse(**result)


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
