# -*- coding: utf-8 -*-
"""Interactive trips debugger web app."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Optional
import gc
import json
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import create_ltm
from .core.episode import Episode
from script.trips_loader import load_trips_episodes


def _episode_preview(episode: Episode, index: int, written: bool) -> dict[str, Any]:
    ts = int(episode.timestamp or 0)
    return {
        "index": index,
        "written": written,
        "id": episode.id,
        "timestamp": ts,
        "timestamp_text": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts > 0 else "",
        "content": episode.content,
        "content_preview": episode.content[:160],
        "metadata": episode.metadata,
    }


def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


class ResetRequest(BaseModel):
    clear_db: bool = True


class WriteSelectedRequest(BaseModel):
    episode_indexes: list[int] = Field(default_factory=list)
    allow_replay: bool = False
    auto_merge: bool = False
    merge_strategy: str = "auto"


class WriteNextRequest(BaseModel):
    count: int = 1
    allow_replay: bool = False
    auto_merge: bool = False
    merge_strategy: str = "auto"


class ManualWriteRequest(BaseModel):
    kind: str = "event"
    payload: dict[str, Any] = Field(default_factory=dict)
    entity_ids: list[Any] = Field(default_factory=list)
    evolve: bool = True
    auto_merge: bool = False
    merge_strategy: str = "auto"


class MergeEventRequest(BaseModel):
    canonical_event_id: str
    merged_event_id: str
    similarity_score: float = 1.0
    merge_reason: str = "manual_merge"


class MergeContextRequest(BaseModel):
    canonical_context_id: str
    merged_context_id: str
    rewrite_strategy: str = "rewrite"


class AutoMergeRequest(BaseModel):
    scope: str = "all"
    strategy: str = "auto"
    dry_run: bool = False
    max_pairs: int = 10


@dataclass
class TripsDebuggerConfig:
    trips_path: str
    db_path: str
    offline_mode: bool = False
    append_first_mode: bool = True
    snapshot_limit: int = 80
    include_buckets: Optional[set[str]] = None
    max_items: int = 0
    sort_by_time: bool = True
    enable_auto_consolidation: bool = False
    default_merge_strategy: str = "llm"
    auto_merge_after_write: bool = True


class TripsDebuggerSession:
    """Holds interactive demo state for one local operator."""

    def __init__(self, config: TripsDebuggerConfig):
        self.config = config
        self._lock = Lock()
        self._episodes = load_trips_episodes(
            path=config.trips_path,
            max_items=config.max_items,
            include_buckets=config.include_buckets,
            sort_by_time=config.sort_by_time,
        )
        self._written_indices: set[int] = set()
        self._operation_log: list[dict[str, Any]] = []
        self._latest_auto_merge: dict[str, Any] | None = None
        self._ltm = None
        self.reset(clear_db=True)

    def reset(self, clear_db: bool = True) -> dict[str, Any]:
        with self._lock:
            self._dispose_ltm(clear_db=clear_db)
            self._written_indices = set()
            self._operation_log = []
            self._latest_auto_merge = None
            self._ltm = create_ltm(
                db_path=self.config.db_path,
                config={
                    "offline_mode": self.config.offline_mode,
                    "enable_dynamic_evolution": True,
                    "append_first_mode": self.config.append_first_mode,
                    "enable_auto_consolidation": self.config.enable_auto_consolidation,
                    "merge_decision_strategy": self.config.default_merge_strategy,
                    "generate_answer": False,
                    "search_top_k": 5,
                },
            )
            self._append_log(
                action="reset",
                detail={
                    "clear_db": clear_db,
                    "db_path": self.config.db_path,
                    "trips_path": self.config.trips_path,
                },
            )
            return self._build_state()

    def state(self) -> dict[str, Any]:
        with self._lock:
            return self._build_state()

    def list_episodes(
        self,
        search: str = "",
        bucket: str = "",
        pending_only: bool = False,
        written_only: bool = False,
        limit: int = 2000,
    ) -> dict[str, Any]:
        with self._lock:
            query = search.strip().lower()
            items = []
            for index, episode in enumerate(self._episodes):
                written = index in self._written_indices
                if pending_only and written:
                    continue
                if written_only and not written:
                    continue
                if bucket and str(episode.metadata.get("bucket_name", "")) != bucket:
                    continue
                haystack = f"{episode.content} {json.dumps(episode.metadata, ensure_ascii=False)}".lower()
                if query and query not in haystack:
                    continue
                items.append(_episode_preview(episode, index, written))
                if len(items) >= limit:
                    break
            return {
                "items": items,
                "total": len(self._episodes),
                "written_count": len(self._written_indices),
                "pending_count": len(self._episodes) - len(self._written_indices),
                "bucket_options": sorted(
                    {
                        str(episode.metadata.get("bucket_name", "") or "")
                        for episode in self._episodes
                        if str(episode.metadata.get("bucket_name", "") or "")
                    }
                ),
            }

    def write_selected(
        self,
        episode_indexes: list[int],
        allow_replay: bool = False,
        auto_merge: Optional[bool] = None,
        merge_strategy: str = "auto",
    ) -> dict[str, Any]:
        with self._lock:
            if self._ltm is None:
                raise RuntimeError("Session is not initialized")
            results = []
            for index in episode_indexes:
                if index < 0 or index >= len(self._episodes):
                    raise ValueError(f"Episode index out of range: {index}")
                already_written = index in self._written_indices
                if already_written and not allow_replay:
                    results.append(
                        {
                            "index": index,
                            "status": "skipped",
                            "reason": "already_written",
                        }
                    )
                    continue
                episode = self._episodes[index]
                ingest_result = self._ltm.ingest(episode)
                self._written_indices.add(index)
                result = {
                    "index": index,
                    "status": "written" if not already_written else "replayed",
                    "episode": _episode_preview(episode, index, written=True),
                    "ingest_result": {
                        "event_id": ingest_result.event.id,
                        "summary": ingest_result.event.summary,
                        "is_new": bool(ingest_result.is_new),
                        "merged_with": ingest_result.merged_with,
                        "entities_created": int(ingest_result.entities_created),
                        "event_count": len(ingest_result.events or [ingest_result.event]),
                        "event_ids": [
                            event.id
                            for event in (ingest_result.events or [ingest_result.event])
                        ],
                        "event_items": [
                            {
                                "id": event.id,
                                "summary": event.summary,
                                "status": event.status,
                            }
                            for event in (ingest_result.events or [ingest_result.event])
                        ],
                    },
                }
                results.append(result)
                self._append_log(action="write_episode", detail=result)
            merge_report = self._run_auto_merge_locked(
                enabled=self.config.auto_merge_after_write if auto_merge is None else bool(auto_merge),
                strategy=merge_strategy,
                trigger="write_selected",
            )
            return {
                "results": results,
                "auto_merge": merge_report,
                "state": self._build_state(),
            }

    def write_next(
        self,
        count: int = 1,
        allow_replay: bool = False,
        auto_merge: Optional[bool] = None,
        merge_strategy: str = "auto",
    ) -> dict[str, Any]:
        with self._lock:
            pending = [index for index in range(len(self._episodes)) if index not in self._written_indices]
            target = pending[: max(int(count), 0)]
        return self.write_selected(
            target,
            allow_replay=allow_replay,
            auto_merge=auto_merge,
            merge_strategy=merge_strategy,
        )

    def write_manual(
        self,
        kind: str,
        payload: dict[str, Any],
        entity_ids: Optional[list[Any]] = None,
        evolve: bool = True,
        auto_merge: Optional[bool] = None,
        merge_strategy: str = "auto",
    ) -> dict[str, Any]:
        with self._lock:
            if self._ltm is None:
                raise RuntimeError("Session is not initialized")
            result = self._ltm.write(
                item=payload,
                kind=kind,
                entity_ids=entity_ids or [],
                evolve=evolve,
            )
            self._append_log(
                action="manual_write",
                detail={
                    "kind": kind,
                    "payload": payload,
                    "result": result,
                },
            )
            merge_report = self._run_auto_merge_locked(
                enabled=self.config.auto_merge_after_write if auto_merge is None else bool(auto_merge),
                strategy=merge_strategy,
                trigger="write_manual",
            )
            return {
                "result": result,
                "auto_merge": merge_report,
                "state": self._build_state(),
            }

    def auto_merge(
        self,
        scope: str = "all",
        strategy: str = "auto",
        dry_run: bool = False,
        max_pairs: int = 10,
    ) -> dict[str, Any]:
        with self._lock:
            if self._ltm is None:
                raise RuntimeError("Session is not initialized")
            result = self._ltm.auto_merge(
                scope=scope,
                strategy=strategy,
                dry_run=dry_run,
                max_pairs=max_pairs,
            )
            result["trigger"] = "manual_auto_merge"
            self._latest_auto_merge = result
            self._append_log(action="auto_merge", detail=result)
            return {
                "result": result,
                "state": self._build_state(),
            }

    def merge_event(
        self,
        canonical_event_id: str,
        merged_event_id: str,
        similarity_score: float = 1.0,
        merge_reason: str = "manual_merge",
    ) -> dict[str, Any]:
        with self._lock:
            if self._ltm is None:
                raise RuntimeError("Session is not initialized")
            result = self._ltm.merge_event(
                canonical_event_id=canonical_event_id,
                merged_event_id=merged_event_id,
                similarity_score=similarity_score,
                merge_reason=merge_reason,
            )
            self._append_log(
                action="merge_event",
                detail=result,
            )
            return {
                "result": result,
                "state": self._build_state(),
            }

    def merge_context(
        self,
        canonical_context_id: str,
        merged_context_id: str,
        rewrite_strategy: str = "rewrite",
    ) -> dict[str, Any]:
        with self._lock:
            if self._ltm is None:
                raise RuntimeError("Session is not initialized")
            result = self._ltm.merge_context(
                canonical_context_id=canonical_context_id,
                merged_context_id=merged_context_id,
                rewrite_strategy=rewrite_strategy,
            )
            self._append_log(
                action="merge_context",
                detail=result,
            )
            return {
                "result": result,
                "state": self._build_state(),
            }

    def _append_log(self, action: str, detail: dict[str, Any]) -> None:
        self._operation_log.append(
            {
                "id": len(self._operation_log) + 1,
                "action": action,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "detail": detail,
            }
        )
        self._operation_log = self._operation_log[-60:]

    def _run_auto_merge_locked(
        self,
        enabled: bool,
        strategy: str,
        trigger: str,
    ) -> Optional[dict[str, Any]]:
        if not enabled or self._ltm is None:
            return None
        result = self._ltm.auto_merge(
            scope="all",
            strategy=strategy,
            dry_run=False,
            max_pairs=12,
        )
        result["trigger"] = trigger
        self._latest_auto_merge = result
        self._append_log(action="auto_merge", detail=result)
        return result

    def _build_state(self) -> dict[str, Any]:
        if self._ltm is None:
            raise RuntimeError("Session is not initialized")
        context_llm_available = False
        try:
            extractor = self._ltm.dynamic_engine.context_extractor if self._ltm.dynamic_engine else None
            if extractor is not None and hasattr(extractor, "_llm_available"):
                context_llm_available = bool(extractor._llm_available())
        except Exception:
            context_llm_available = False
        snapshot = self._ltm.snapshot(limit=self.config.snapshot_limit, include_inactive=True)
        return {
            "config": {
                "trips_path": self.config.trips_path,
                "db_path": self.config.db_path,
                "offline_mode": self.config.offline_mode,
                "context_llm_available": context_llm_available,
                "append_first_mode": self.config.append_first_mode,
                "snapshot_limit": self.config.snapshot_limit,
                "episodes_total": len(self._episodes),
                "default_merge_strategy": self.config.default_merge_strategy,
                "auto_merge_after_write": self.config.auto_merge_after_write,
            },
            "progress": {
                "written_count": len(self._written_indices),
                "pending_count": len(self._episodes) - len(self._written_indices),
                "written_indexes": sorted(self._written_indices),
                "next_pending_indexes": [
                    index for index in range(len(self._episodes)) if index not in self._written_indices
                ][:20],
            },
            "stats": self._ltm.get_stats(),
            "snapshot": snapshot,
            "latest_auto_merge": self._latest_auto_merge,
            "operation_log": list(reversed(self._operation_log[-20:])),
        }

    def _dispose_ltm(self, clear_db: bool) -> None:
        self._ltm = None
        gc.collect()
        if clear_db and os.path.exists(self.config.db_path):
            try:
                os.remove(self.config.db_path)
            except OSError:
                pass


def create_trips_debugger_app(config: TripsDebuggerConfig) -> FastAPI:
    session = TripsDebuggerSession(config)
    app = FastAPI(title="LiMem Trips Debugger")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html_path = _static_dir() / "trips_debugger.html"
        return html_path.read_text(encoding="utf-8")

    @app.get("/api/trips-debug/state")
    def get_state() -> dict[str, Any]:
        return session.state()

    @app.get("/api/trips-debug/episodes")
    def get_episodes(
        search: str = Query(default=""),
        bucket: str = Query(default=""),
        pending_only: bool = Query(default=False),
        written_only: bool = Query(default=False),
        limit: int = Query(default=2000, ge=1, le=5000),
    ) -> dict[str, Any]:
        return session.list_episodes(
            search=search,
            bucket=bucket,
            pending_only=pending_only,
            written_only=written_only,
            limit=limit,
        )

    @app.post("/api/trips-debug/reset")
    def post_reset(request: ResetRequest) -> dict[str, Any]:
        return session.reset(clear_db=request.clear_db)

    @app.post("/api/trips-debug/write-selected")
    def post_write_selected(request: WriteSelectedRequest) -> dict[str, Any]:
        try:
            return session.write_selected(
                request.episode_indexes,
                allow_replay=request.allow_replay,
                auto_merge=request.auto_merge,
                merge_strategy=request.merge_strategy,
            )
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex

    @app.post("/api/trips-debug/write-next")
    def post_write_next(request: WriteNextRequest) -> dict[str, Any]:
        return session.write_next(
            count=request.count,
            allow_replay=request.allow_replay,
            auto_merge=request.auto_merge,
            merge_strategy=request.merge_strategy,
        )

    @app.post("/api/trips-debug/write-manual")
    def post_write_manual(request: ManualWriteRequest) -> dict[str, Any]:
        try:
            return session.write_manual(
                kind=request.kind,
                payload=request.payload,
                entity_ids=request.entity_ids,
                evolve=request.evolve,
                auto_merge=request.auto_merge,
                merge_strategy=request.merge_strategy,
            )
        except ValueError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex
        except RuntimeError as ex:
            raise HTTPException(status_code=409, detail=str(ex)) from ex

    @app.post("/api/trips-debug/auto-merge")
    def post_auto_merge(request: AutoMergeRequest) -> dict[str, Any]:
        try:
            return session.auto_merge(
                scope=request.scope,
                strategy=request.strategy,
                dry_run=request.dry_run,
                max_pairs=request.max_pairs,
            )
        except (ValueError, RuntimeError) as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex

    @app.post("/api/trips-debug/merge-event")
    def post_merge_event(request: MergeEventRequest) -> dict[str, Any]:
        try:
            return session.merge_event(
                canonical_event_id=request.canonical_event_id,
                merged_event_id=request.merged_event_id,
                similarity_score=request.similarity_score,
                merge_reason=request.merge_reason,
            )
        except (ValueError, RuntimeError) as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex

    @app.post("/api/trips-debug/merge-context")
    def post_merge_context(request: MergeContextRequest) -> dict[str, Any]:
        try:
            return session.merge_context(
                canonical_context_id=request.canonical_context_id,
                merged_context_id=request.merged_context_id,
                rewrite_strategy=request.rewrite_strategy,
            )
        except (ValueError, RuntimeError) as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex

    return app
