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

from .json_flattener import flatten_json
from .models import (
    DeleteNodeRequest,
    EvolveResponse,
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResponse,
    UpdateNodeRequest,
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
        self.evolve_timer: threading.Timer | None = None
        self.shutting_down = False

    def startup(self) -> None:
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self.ltm = create_ltm(db_path=self.db_path)
        jieba.initialize()
        self.rebuild_index()
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
            self.evolve_and_rebuild()
        finally:
            self.schedule_evolution()

    def active_events(self):
        return self.ltm.store.list_events(limit=100000, statuses=["active"])

    def rebuild_index(self) -> None:
        self.bm25_index.rebuild(self.active_events())

    def evolve_and_rebuild(self) -> dict[str, int]:
        with self.write_lock:
            details = self.ltm.run_consolidation()
            self.rebuild_index()
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
        text = flatten_json(request.data)
        with state.write_lock:
            result = state.ltm.ingest_text(text, timestamp=request.timestamp)
            events = result.events or [result.event]
            for event in events:
                state.bm25_index.add_event(event)

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
        details = state.evolve_and_rebuild()
        return EvolveResponse(message="evolution completed", details=details)

    @app.get("/health")
    def health() -> dict[str, Any]:
        stats = state.ltm.get_stats()
        return {
            "status": "ok",
            "db_path": state.db_path,
            "event_count": stats.get("event_count", 0),
            "index_size": state.bm25_index.size,
        }

    @app.get("/stats")
    def stats() -> dict[str, Any]:
        return state.ltm.get_stats()

    @app.post("/rebuild-index")
    def rebuild_index() -> dict[str, int]:
        with state.write_lock:
            state.rebuild_index()
            return {"index_size": state.bm25_index.size}

    # ── Graph visualization endpoints ──

    @app.get("/graph", response_class=HTMLResponse)
    def graph_page() -> str:
        html_path = Path(__file__).parent / "static" / "graph.html"
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
        with state.write_lock:
            result = state.ltm.remove(
                memory_id=request.memory_id,
                kind=request.kind,
                hard_delete=request.hard_delete,
            )
            state.rebuild_index()
        return result

    @app.post("/api/graph/update")
    def graph_update(request: UpdateNodeRequest) -> dict[str, Any]:
        with state.write_lock:
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
            state.rebuild_index()
        return result

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
