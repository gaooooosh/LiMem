"""/db/{db_id}/... 业务路由：摄入、查询、演化、健康、统计、索引重建、审计读取。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ..auth.dependencies import (
    CallerCtx,
    get_caller,
    get_ltm_context,
    get_ltm_context_write,
)
from ..json_flattener import flatten_json
from ..models import (
    EvolveResponse,
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResponse,
    RecallTaskRequest,
    RecallTaskResponse,
)
from ..ops import evolve_and_rebuild as _evolve_and_rebuild
from ..ops import rebuild_index as _rebuild_index
from ..pool import LtmHandle


router = APIRouter(prefix="/db/{db_id}", tags=["memory"])


@router.post("/ingest", response_model=IngestResponse)
def ingest(
    request: IngestRequest,
    handle: LtmHandle = Depends(get_ltm_context_write),
) -> IngestResponse:
    audit = handle.audit
    with audit.trace(
        "ingest",
        {"timestamp": request.timestamp, "payload_type": type(request.data).__name__},
    ) as trace_id:
        text = flatten_json(request.data)
        audit.write(
            trace_id,
            "request",
            "flattened",
            details={"text_preview": text[:500], "text_length": len(text)},
        )
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            result = handle.ltm.ingest_text(text, timestamp=request.timestamp)
            events = result.events or [result.event]
            metrics = dict(result.metrics or {})
            raw_event_count = int(metrics.get("raw_event_count", len(events)) or 0)
            accepted_event_count = int(metrics.get("event_count", len(events)) or 0)
            audit.write(
                trace_id,
                "algorithm_call",
                "ingest_completed",
                details={
                    "result_event_id": result.event.id,
                    "event_ids": [event.id for event in events],
                    "raw_event_count": raw_event_count,
                    "accepted_event_count": accepted_event_count,
                    "inferred_rejected_event_count": max(
                        0, raw_event_count - accepted_event_count
                    ),
                    "inline_context_count": metrics.get("inline_context_count", 0),
                    "orphan_context_count": metrics.get("orphan_context_count", 0),
                    "orphan_contexts": metrics.get("orphan_contexts", []),
                    "metrics": metrics,
                },
            )
            for event in events:
                handle.bm25.add_event(event)
                audit.write(
                    trace_id,
                    "index",
                    "bm25_event_added",
                    entity_type="event",
                    entity_id=event.id,
                    details={"summary": event.summary},
                )
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="ingest")

    return IngestResponse(
        event_id=result.event.id,
        summary=result.event.summary,
        is_new=result.is_new,
        entities_created=result.entities_created,
        event_count=len(events),
    )


@router.post("/query", response_model=QueryResponse)
def query(request: QueryRequest, handle: LtmHandle = Depends(get_ltm_context)) -> QueryResponse:
    results = handle.bm25.search(request.query, request.top_k)
    return QueryResponse(results=results, total=len(results))


@router.post("/recall", response_model=RecallTaskResponse)
def recall_for_task(
    request: RecallTaskRequest,
    handle: LtmHandle = Depends(get_ltm_context),
) -> RecallTaskResponse:
    result = handle.ltm.recall_for_task(
        task=request.task,
        limit=request.limit,
        include_debug=request.include_debug,
    )
    return RecallTaskResponse(
        prompt_text=str(result.get("prompt_text", "") or ""),
        items=list(result.get("items", []) or []),
        stats=dict(result.get("stats", {}) or {}),
    )


@router.post("/evolve", response_model=EvolveResponse)
def evolve(handle: LtmHandle = Depends(get_ltm_context_write)) -> EvolveResponse:
    details = _evolve_and_rebuild(handle, trigger="manual")
    return EvolveResponse(message="evolution completed", details=details)


@router.get("/health")
def health(handle: LtmHandle = Depends(get_ltm_context)) -> dict[str, Any]:
    stats = handle.ltm.get_stats()
    return {
        "status": "ok",
        "db_id": handle.db_id,
        "audit_log_path": handle.audit.path,
        "event_count": stats.get("event_count", 0),
        "index_size": handle.bm25.size,
    }


@router.get("/stats")
def stats(handle: LtmHandle = Depends(get_ltm_context)) -> dict[str, Any]:
    return handle.ltm.get_stats()


@router.post("/rebuild-index")
def rebuild_index(handle: LtmHandle = Depends(get_ltm_context_write)) -> dict[str, int]:
    audit = handle.audit
    with audit.trace("rebuild_index") as trace_id:
        with handle.write_lock:
            size = _rebuild_index(handle)
            audit.write(
                trace_id, "index", "bm25_rebuilt", details={"index_size": size}
            )
            return {"index_size": size}


@router.get("/api/audit/recent")
def audit_recent(
    limit: int = Query(default=200, ge=1, le=2000),
    handle: LtmHandle = Depends(get_ltm_context),
) -> dict[str, Any]:
    return {"path": handle.audit.path, "items": handle.audit.read_recent(limit=limit)}
