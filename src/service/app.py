"""FastAPI application for the LiMem service layer."""

from __future__ import annotations

import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import jieba
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from limem.factory import create_ltm
from limem.retrieval import BM25Index

from .audit import ServiceAuditLogger, install_store_audit_proxy
from .json_flattener import flatten_json
from .models import (
    DeleteNodeRequest,
    EvolveResponse,
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResponse,
    UpdateNodeRequest,
    WriteNodeRequest,
    WriteNodeResponse,
)


DEFAULT_DB_PATH = "./DB/service.kz"
DEFAULT_EVOLVE_INTERVAL_SECONDS = 3600


class ServiceState:
    def __init__(self) -> None:
        self.db_path = os.getenv("SERVICE_DB_PATH", DEFAULT_DB_PATH)
        self.evolve_interval_seconds = int(
            os.getenv("EVOLVE_INTERVAL_SECONDS", str(DEFAULT_EVOLVE_INTERVAL_SECONDS))
        )
        self.write_lock = threading.Lock()
        self.ltm: Any | None = None
        self.bm25_index = BM25Index()
        self.audit = ServiceAuditLogger()
        self.evolve_timer: threading.Timer | None = None
        self.shutting_down = False

    def startup(self) -> None:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self.ltm = create_ltm(db_path=self.db_path)
        install_store_audit_proxy(self.ltm, self.audit)
        jieba.initialize()
        self.rebuild_index()
        self.audit.write(
            "startup",
            "service",
            "started",
            details={
                "db_path": self.db_path,
                "audit_log_path": self.audit.path,
                "index_size": self.bm25_index.size,
            },
        )
        self.schedule_evolution()

    def shutdown(self) -> None:
        self.shutting_down = True
        if self.evolve_timer is not None:
            self.evolve_timer.cancel()
            self.evolve_timer = None

    def schedule_evolution(self) -> None:
        if self.shutting_down or self.evolve_interval_seconds <= 0:
            return
        self.evolve_timer = threading.Timer(self.evolve_interval_seconds, self.run_scheduled_evolution)
        self.evolve_timer.daemon = True
        self.evolve_timer.start()

    def run_scheduled_evolution(self) -> None:
        try:
            self.evolve_and_rebuild(trigger="scheduled")
        finally:
            self.schedule_evolution()

    def active_events(self):
        return self.ltm.store.list_events(limit=100000, statuses=["active"])

    def rebuild_index(self) -> None:
        self.bm25_index.rebuild(self.active_events())

    def evolve_and_rebuild(self, trigger: str = "manual") -> dict[str, int]:
        with self.audit.trace("evolve", {"trigger": trigger}) as trace_id:
            with self.write_lock:
                before = self.audit.graph_snapshot(self.ltm)
                details = self.ltm.run_consolidation()
                self.audit.write(
                    trace_id,
                    "algorithm_call",
                    "run_consolidation_completed",
                    details={"trigger": trigger, "report": details},
                )
                self.rebuild_index()
                self.audit.write(
                    trace_id,
                    "index",
                    "bm25_rebuilt",
                    details={"index_size": self.bm25_index.size},
                )
                after = self.audit.graph_snapshot(self.ltm)
                self.audit.write_graph_delta(trace_id, before, after, operation="evolve")
                return details


def create_app() -> FastAPI:
    state = ServiceState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.startup()
        app.state.service = state
        try:
            yield
        finally:
            state.shutdown()

    app = FastAPI(title="LiMem Service", lifespan=lifespan)

    @app.post("/ingest", response_model=IngestResponse)
    def ingest(request: IngestRequest) -> IngestResponse:
        with state.audit.trace(
            "ingest",
            {
                "timestamp": request.timestamp,
                "payload_type": type(request.data).__name__,
            },
        ) as trace_id:
            text = flatten_json(request.data)
            state.audit.write(
                trace_id,
                "request",
                "flattened",
                details={"text_preview": text[:500], "text_length": len(text)},
            )
            with state.write_lock:
                before = state.audit.graph_snapshot(state.ltm)
                result = state.ltm.ingest_text(text, timestamp=request.timestamp)
                events = result.events or [result.event]
                metrics = dict(result.metrics or {})
                raw_event_count = int(metrics.get("raw_event_count", len(events)) or 0)
                accepted_event_count = int(metrics.get("event_count", len(events)) or 0)
                state.audit.write(
                    trace_id,
                    "algorithm_call",
                    "ingest_completed",
                    details={
                        "result_event_id": result.event.id,
                        "event_ids": [event.id for event in events],
                        "raw_event_count": raw_event_count,
                        "accepted_event_count": accepted_event_count,
                        "inferred_rejected_event_count": max(0, raw_event_count - accepted_event_count),
                        "inline_context_count": metrics.get("inline_context_count", 0),
                        "orphan_context_count": metrics.get("orphan_context_count", 0),
                        "orphan_contexts": metrics.get("orphan_contexts", []),
                        "metrics": metrics,
                    },
                )
                for event in events:
                    state.bm25_index.add_event(event)
                    state.audit.write(
                        trace_id,
                        "index",
                        "bm25_event_added",
                        entity_type="event",
                        entity_id=event.id,
                        details={"summary": event.summary},
                    )
                after = state.audit.graph_snapshot(state.ltm)
                state.audit.write_graph_delta(trace_id, before, after, operation="ingest")

        return IngestResponse(
            event_id=result.event.id,
            summary=result.event.summary,
            is_new=result.is_new,
            entities_created=result.entities_created,
            event_count=len(events),
        )

    @app.post("/query", response_model=QueryResponse)
    def query(request: QueryRequest) -> QueryResponse:
        results = state.bm25_index.search(request.query, request.top_k)
        return QueryResponse(results=results, total=len(results))

    @app.post("/evolve", response_model=EvolveResponse)
    def evolve() -> EvolveResponse:
        details = state.evolve_and_rebuild(trigger="manual")
        return EvolveResponse(message="evolution completed", details=details)

    @app.get("/health")
    def health() -> dict[str, Any]:
        stats = state.ltm.get_stats()
        return {
            "status": "ok",
            "db_path": state.db_path,
            "audit_log_path": state.audit.path,
            "event_count": stats.get("event_count", 0),
            "index_size": state.bm25_index.size,
        }

    @app.get("/stats")
    def stats() -> dict[str, Any]:
        return state.ltm.get_stats()

    @app.post("/rebuild-index")
    def rebuild_index() -> dict[str, int]:
        with state.audit.trace("rebuild_index") as trace_id:
            with state.write_lock:
                state.rebuild_index()
                state.audit.write(
                    trace_id,
                    "index",
                    "bm25_rebuilt",
                    details={"index_size": state.bm25_index.size},
                )
                return {"index_size": state.bm25_index.size}

    @app.get("/api/audit/recent")
    def audit_recent(limit: int = Query(default=200, ge=1, le=2000)) -> dict[str, Any]:
        return {
            "path": state.audit.path,
            "items": state.audit.read_recent(limit=limit),
        }

    # ── Graph visualization endpoints ──

    @app.get("/graph", response_class=HTMLResponse)
    def graph_page() -> str:
        html_path = Path(__file__).parent / "static" / "graph.html"
        return html_path.read_text(encoding="utf-8")

    @app.get("/logs", response_class=HTMLResponse)
    def logs_page() -> str:
        html_path = Path(__file__).parent / "static" / "logs.html"
        return html_path.read_text(encoding="utf-8")

    @app.get("/api/graph/snapshot")
    def graph_snapshot(
        limit: int = Query(default=100, ge=1, le=500),
        include_inactive: bool = Query(default=False),
        text: str = Query(default=""),
    ) -> dict[str, Any]:
        return state.ltm.snapshot(
            limit=limit,
            include_inactive=include_inactive,
            text=text,
        )

    @app.post("/api/graph/delete")
    def graph_delete(request: DeleteNodeRequest) -> dict[str, Any]:
        with state.audit.trace("graph_delete", request.model_dump()) as trace_id:
            with state.write_lock:
                before = state.audit.graph_snapshot(state.ltm)
                result = state.ltm.remove(
                    memory_id=request.memory_id,
                    kind=request.kind,
                    hard_delete=request.hard_delete,
                )
                state.audit.write(
                    trace_id,
                    "graph_operation",
                    "delete_completed",
                    entity_type=request.kind,
                    entity_id=request.memory_id,
                    details={"result": result},
                )
                state.rebuild_index()
                state.audit.write(trace_id, "index", "bm25_rebuilt", details={"index_size": state.bm25_index.size})
                after = state.audit.graph_snapshot(state.ltm)
                state.audit.write_graph_delta(trace_id, before, after, operation="graph_delete")
        return result

    @app.post("/api/graph/update")
    def graph_update(request: UpdateNodeRequest) -> dict[str, Any]:
        with state.audit.trace("graph_update", request.model_dump()) as trace_id:
            with state.write_lock:
                before = state.audit.graph_snapshot(state.ltm)
                if request.kind == "event":
                    existing = state.ltm.store.get_event(request.memory_id)
                    if not existing:
                        raise HTTPException(status_code=404, detail="Event not found")
                    item = state.ltm.ops._serialize_event(existing)
                    item.update(request.fields)
                elif request.kind == "context":
                    existing = state.ltm.store.get_context(request.memory_id)
                    if not existing:
                        raise HTTPException(status_code=404, detail="Context not found")
                    item = state.ltm.ops._serialize_context(existing)
                    item.update(request.fields)
                else:
                    raise HTTPException(status_code=400, detail=f"Unknown kind: {request.kind}")
                result = state.ltm.write(item=item, kind=request.kind, evolve=request.evolve)
                state.audit.write(
                    trace_id,
                    "graph_operation",
                    "update_completed",
                    entity_type=request.kind,
                    entity_id=request.memory_id,
                    details={"fields": request.fields, "result": result},
                )
                state.rebuild_index()
                state.audit.write(trace_id, "index", "bm25_rebuilt", details={"index_size": state.bm25_index.size})
                after = state.audit.graph_snapshot(state.ltm)
                state.audit.write_graph_delta(trace_id, before, after, operation="graph_update")
        return result

    @app.post("/api/graph/write", response_model=WriteNodeResponse)
    def graph_write(request: WriteNodeRequest) -> WriteNodeResponse:
        with state.audit.trace("graph_write", request.model_dump()) as trace_id:
            with state.write_lock:
                before = state.audit.graph_snapshot(state.ltm)
                result = state.ltm.write(
                    item=request.item,
                    kind=request.kind,
                    evolve=request.evolve,
                    entity_ids=request.entity_ids or None,
                )
                state.audit.write(
                    trace_id,
                    "graph_operation",
                    "write_completed",
                    entity_type=request.kind,
                    entity_id=str(result.get("item", {}).get("id", "")),
                    details={"result": result},
                )
                state.rebuild_index()
                state.audit.write(trace_id, "index", "bm25_rebuilt", details={"index_size": state.bm25_index.size})
                after = state.audit.graph_snapshot(state.ltm)
                state.audit.write_graph_delta(trace_id, before, after, operation="graph_write")
        return WriteNodeResponse(
            kind=result["kind"],
            action=result["action"],
            item=result.get("item", {}),
            entity_links=result.get("entity_links", 0),
        )

    @app.get("/api/graph/node/{kind}/{node_id:path}")
    def graph_node_detail(kind: str, node_id: str) -> dict[str, Any]:
        if kind == "event":
            event = state.ltm.store.get_event(node_id)
            if not event:
                raise HTTPException(status_code=404, detail="Event not found")
            return state.ltm.ops._serialize_event(event)
        elif kind == "context":
            context = state.ltm.store.get_context(node_id)
            if not context:
                raise HTTPException(status_code=404, detail="Context not found")
            return state.ltm.ops._serialize_context(context)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown kind: {kind}")

    return app
