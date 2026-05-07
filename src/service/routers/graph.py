"""/db/{db_id}/api/graph/... 图操作路由：snapshot / write / update / delete / node 详情。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth.dependencies import get_ltm_context, get_ltm_context_write
from ..models import (
    DeleteNodeRequest,
    UpdateNodeRequest,
    WriteNodeRequest,
    WriteNodeResponse,
)
from ..ops import rebuild_index as _rebuild_index
from ..pool import LtmHandle


router = APIRouter(prefix="/db/{db_id}/api/graph", tags=["graph"])


@router.get("/snapshot")
def graph_snapshot(
    limit: int = Query(default=100, ge=1, le=500),
    include_inactive: bool = Query(default=False),
    text: str = Query(default=""),
    handle: LtmHandle = Depends(get_ltm_context),
) -> dict[str, Any]:
    return handle.ltm.snapshot(limit=limit, include_inactive=include_inactive, text=text)


@router.post("/delete")
def graph_delete(
    request: DeleteNodeRequest,
    handle: LtmHandle = Depends(get_ltm_context_write),
) -> dict[str, Any]:
    audit = handle.audit
    with audit.trace("graph_delete", request.model_dump()) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            result = handle.ltm.remove(
                memory_id=request.memory_id,
                kind=request.kind,
                hard_delete=request.hard_delete,
            )
            audit.write(
                trace_id,
                "graph_operation",
                "delete_completed",
                entity_type=request.kind,
                entity_id=request.memory_id,
                details={"result": result},
            )
            size = _rebuild_index(handle)
            audit.write(trace_id, "index", "bm25_rebuilt", details={"index_size": size})
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="graph_delete")
    return result


@router.post("/update")
def graph_update(
    request: UpdateNodeRequest,
    handle: LtmHandle = Depends(get_ltm_context_write),
) -> dict[str, Any]:
    audit = handle.audit
    with audit.trace("graph_update", request.model_dump()) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            if request.kind == "event":
                existing = handle.ltm.store.get_event(request.memory_id)
                if not existing:
                    raise HTTPException(status_code=404, detail="Event not found")
                item = handle.ltm.ops._serialize_event(existing)
                item.update(request.fields)
            elif request.kind == "context":
                existing = handle.ltm.store.get_context(request.memory_id)
                if not existing:
                    raise HTTPException(status_code=404, detail="Context not found")
                item = handle.ltm.ops._serialize_context(existing)
                item.update(request.fields)
            else:
                raise HTTPException(status_code=400, detail=f"Unknown kind: {request.kind}")
            result = handle.ltm.write(item=item, kind=request.kind, evolve=request.evolve)
            audit.write(
                trace_id,
                "graph_operation",
                "update_completed",
                entity_type=request.kind,
                entity_id=request.memory_id,
                details={"fields": request.fields, "result": result},
            )
            size = _rebuild_index(handle)
            audit.write(trace_id, "index", "bm25_rebuilt", details={"index_size": size})
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="graph_update")
    return result


@router.post("/write", response_model=WriteNodeResponse)
def graph_write(
    request: WriteNodeRequest,
    handle: LtmHandle = Depends(get_ltm_context_write),
) -> WriteNodeResponse:
    audit = handle.audit
    with audit.trace("graph_write", request.model_dump()) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            result = handle.ltm.write(
                item=request.item,
                kind=request.kind,
                evolve=request.evolve,
                entity_ids=request.entity_ids or None,
            )
            audit.write(
                trace_id,
                "graph_operation",
                "write_completed",
                entity_type=request.kind,
                entity_id=str(result.get("item", {}).get("id", "")),
                details={"result": result},
            )
            size = _rebuild_index(handle)
            audit.write(trace_id, "index", "bm25_rebuilt", details={"index_size": size})
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="graph_write")
    return WriteNodeResponse(
        kind=result["kind"],
        action=result["action"],
        item=result.get("item", {}),
        entity_links=result.get("entity_links", 0),
    )


@router.get("/node/{kind}/{node_id:path}")
def graph_node_detail(
    kind: str,
    node_id: str,
    handle: LtmHandle = Depends(get_ltm_context),
) -> dict[str, Any]:
    if kind == "event":
        event = handle.ltm.store.get_event(node_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        return handle.ltm.ops._serialize_event(event)
    elif kind == "context":
        context = handle.ltm.store.get_context(node_id)
        if not context:
            raise HTTPException(status_code=404, detail="Context not found")
        return handle.ltm.ops._serialize_context(context)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown kind: {kind}")
