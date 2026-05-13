"""Service-side graph audit logging.

This module belongs to the deployment layer. It observes public LiMem service
operations from the outside and records graph deltas without coupling logging
logic into the algorithm package.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
import json
import os
import threading
import time
import uuid


_CURRENT_TRACE_ID: ContextVar[str] = ContextVar("service_audit_trace_id", default="")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class ServiceAuditLogger:
    """Append-only JSONL audit stream for deployed graph operations.

    多库版本：path 必须由调用方按库显式传入，不再从环境变量读取。
    SERVICE_AUDIT_LOG_ENABLED / SERVICE_INSTANCE_ID 仍作全局开关与实例标识保留。
    """

    def __init__(self, path: str, enabled: bool | None = None) -> None:
        if not path:
            raise ValueError("ServiceAuditLogger requires an explicit path")
        self.path = path
        self.enabled = _env_bool("SERVICE_AUDIT_LOG_ENABLED", True) if enabled is None else enabled
        self.instance_id = os.getenv("SERVICE_INSTANCE_ID", f"svc_{uuid.uuid4().hex[:10]}")
        self._lock = threading.Lock()

    @contextmanager
    def trace(self, operation: str, request: dict[str, Any] | None = None) -> Iterator[str]:
        trace_id = f"{operation}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        started_at = time.perf_counter()
        token = _CURRENT_TRACE_ID.set(trace_id)
        self.write(trace_id, "request", "received", details={"operation": operation, "request": request or {}})
        try:
            yield trace_id
        except Exception as exc:
            self.write(
                trace_id,
                "request",
                "failed",
                status="failed",
                details={"operation": operation, "error": repr(exc), "elapsed_ms": _elapsed_ms(started_at)},
            )
            raise
        else:
            self.write(
                trace_id,
                "request",
                "completed",
                details={"operation": operation, "elapsed_ms": _elapsed_ms(started_at)},
            )
        finally:
            _CURRENT_TRACE_ID.reset(token)

    def write(
        self,
        trace_id: str,
        stage: str,
        action: str,
        *,
        status: str = "ok",
        entity_type: str = "",
        entity_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        payload = {
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "instance_id": self.instance_id,
            "trace_id": trace_id,
            "stage": stage,
            "action": action,
            "status": status,
            "entity_type": entity_type,
            "entity_id": str(entity_id or ""),
            "details": _safe(details or {}),
        }
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")

    def graph_snapshot(self, ltm: Any, limit: int = 100000) -> dict[str, Any]:
        snapshot = ltm.snapshot(limit=limit, include_inactive=True)
        edges = snapshot.get("edges", {}) if isinstance(snapshot.get("edges"), dict) else {}
        return {
            "stats": snapshot.get("stats", {}),
            "events": {str(item.get("id", "")): item for item in snapshot.get("events", []) if item.get("id")},
            "contexts": {str(item.get("id", "")): item for item in snapshot.get("contexts", []) if item.get("id")},
            "edges": {
                "event_event": _edge_map(edges.get("event_event", [])),
                "event_context": _edge_map(edges.get("event_context", [])),
                "next": _edge_map(edges.get("next", [])),
            },
        }

    def write_graph_delta(
        self,
        trace_id: str,
        before: dict[str, Any],
        after: dict[str, Any],
        *,
        operation: str,
    ) -> dict[str, int]:
        counts = {
            "events_created": 0,
            "events_updated": 0,
            "events_merged": 0,
            "events_archived": 0,
            "contexts_created": 0,
            "contexts_updated": 0,
            "contexts_merged": 0,
            "contexts_deprecated": 0,
            "edges_created": 0,
            "edges_removed": 0,
            "edges_updated": 0,
        }
        counts.update(self._write_node_delta(trace_id, before, after, "event"))
        counts.update(self._write_node_delta(trace_id, before, after, "context"))
        edge_counts = self._write_edge_delta(trace_id, before, after)
        counts.update(edge_counts)
        self.write(
            trace_id,
            "graph_delta",
            "summary",
            details={"operation": operation, "counts": counts, "before_stats": before.get("stats", {}), "after_stats": after.get("stats", {})},
        )
        return counts

    def _write_node_delta(
        self,
        trace_id: str,
        before: dict[str, Any],
        after: dict[str, Any],
        kind: str,
    ) -> dict[str, int]:
        collection = "events" if kind == "event" else "contexts"
        created_key = f"{collection}_created"
        updated_key = f"{collection}_updated"
        merged_key = f"{collection}_merged"
        archived_key = "events_archived" if kind == "event" else "contexts_deprecated"
        counts = {created_key: 0, updated_key: 0, merged_key: 0, archived_key: 0}
        before_items = before.get(collection, {})
        after_items = after.get(collection, {})
        for node_id, item in after_items.items():
            previous = before_items.get(node_id)
            if previous is None:
                counts[created_key] += 1
                self.write(trace_id, "graph_delta", "node_created", entity_type=kind, entity_id=node_id, details={"after": item})
                continue
            if _stable_json(previous) == _stable_json(item):
                continue
            status = str(item.get("status", "") or "")
            if status == "merged":
                counts[merged_key] += 1
                action = "node_merged"
            elif status in {"archived", "deprecated"}:
                counts[archived_key] += 1
                action = "node_archived" if kind == "event" else "node_deprecated"
            else:
                counts[updated_key] += 1
                action = "node_updated"
            self.write(
                trace_id,
                "graph_delta",
                action,
                entity_type=kind,
                entity_id=node_id,
                details={"before": previous, "after": item},
            )
        for node_id, item in before_items.items():
            if node_id not in after_items:
                self.write(trace_id, "graph_delta", "node_removed", entity_type=kind, entity_id=node_id, details={"before": item})
        return counts

    def _write_edge_delta(
        self,
        trace_id: str,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> dict[str, int]:
        counts = {"edges_created": 0, "edges_removed": 0, "edges_updated": 0}
        before_edges = before.get("edges", {})
        after_edges = after.get("edges", {})
        for edge_type in sorted(set(before_edges) | set(after_edges)):
            before_map = before_edges.get(edge_type, {})
            after_map = after_edges.get(edge_type, {})
            for edge_id, edge in after_map.items():
                previous = before_map.get(edge_id)
                if previous is None:
                    counts["edges_created"] += 1
                    self.write(trace_id, "graph_delta", "edge_created", entity_type=edge_type, entity_id=edge_id, details={"after": edge})
                elif _stable_json(previous) != _stable_json(edge):
                    counts["edges_updated"] += 1
                    self.write(trace_id, "graph_delta", "edge_updated", entity_type=edge_type, entity_id=edge_id, details={"before": previous, "after": edge})
            for edge_id, edge in before_map.items():
                if edge_id not in after_map:
                    counts["edges_removed"] += 1
                    self.write(trace_id, "graph_delta", "edge_removed", entity_type=edge_type, entity_id=edge_id, details={"before": edge})
        return counts

    def read_recent(self, limit: int = 200) -> list[dict[str, Any]]:
        path = Path(self.path)
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        result = []
        for line in lines[-max(1, limit):]:
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result


class AuditedStoreProxy:
    """Service-layer proxy that records write calls made by the algorithm."""

    WRITE_METHODS = {
        "save_episode",
        "save_event",
        "save_events_batch",
        "update_event",
        "delete_event",
        "archive_event",
        "save_context",
        "update_context",
        "delete_context",
        "archive_context",
        "link_event_to_episode",
        "link_events_to_episode_batch",
        "link_event_to_context",
        "upsert_event_relation",
        "ensure_entity",
        "create_involves_relation",
        "update_involves_relation",
        "relink_event_references",
        "relink_context_edges",
        "save_event_merge_trace",
        "create_entity_pattern",
        "update_entity_pattern",
        "delete_entity_pattern",
        "unregister_entity_node",
    }

    def __init__(self, store: Any, audit: ServiceAuditLogger) -> None:
        object.__setattr__(self, "_store", store)
        object.__setattr__(self, "_audit", audit)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._store, name)
        if name not in self.WRITE_METHODS or not callable(attr):
            return attr

        def audited_call(*args: Any, **kwargs: Any) -> Any:
            trace_id = _CURRENT_TRACE_ID.get()
            if not trace_id:
                return attr(*args, **kwargs)
            started_at = time.perf_counter()
            self._audit.write(
                trace_id,
                "store_write",
                f"{name}_started",
                details={"args": _safe(args), "kwargs": _safe(kwargs)},
            )
            try:
                result = attr(*args, **kwargs)
            except Exception as exc:
                self._audit.write(
                    trace_id,
                    "store_write",
                    f"{name}_failed",
                    status="failed",
                    details={"error": repr(exc), "elapsed_ms": _elapsed_ms(started_at)},
                )
                raise
            self._audit.write(
                trace_id,
                "store_write",
                name,
                details={
                    "args": _safe(args),
                    "kwargs": _safe(kwargs),
                    "result": _safe(result),
                    "elapsed_ms": _elapsed_ms(started_at),
                },
            )
            return result

        return audited_call

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._store, name, value)


def install_store_audit_proxy(ltm: Any, audit: ServiceAuditLogger) -> Any:
    """Install a service-side proxy across components that already hold store refs."""

    original_store = getattr(ltm, "store", None)
    if isinstance(original_store, AuditedStoreProxy):
        return original_store

    proxy = AuditedStoreProxy(original_store, audit)
    ltm.store = proxy
    if getattr(ltm, "builder", None) is not None:
        ltm.builder.store = proxy
    if getattr(ltm, "ops", None) is not None:
        ltm.ops.store = proxy
    engine = getattr(ltm, "dynamic_engine", None)
    if engine is not None:
        engine.store = proxy
        if getattr(engine, "recall_pipeline", None) is not None:
            engine.recall_pipeline.store = proxy
        if getattr(engine, "relation_processor", None) is not None:
            engine.relation_processor.store = proxy
    return proxy


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000.0, 3)


def _edge_map(edges: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for edge in edges or []:
        if not isinstance(edge, dict):
            continue
        result[_edge_key(edge)] = edge
    return result


def _edge_key(edge: dict[str, Any]) -> str:
    preferred = [
        "id",
        "from_event_id",
        "to_event_id",
        "event_id",
        "context_id",
        "from",
        "to",
        "source",
        "target",
        "relation_type",
        "operation",
    ]
    parts = {key: edge.get(key) for key in preferred if key in edge}
    return _stable_json(parts or edge)


def _stable_json(value: Any) -> str:
    return json.dumps(_safe(value), ensure_ascii=False, sort_keys=True)


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _safe_value(key, item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(item) for item in value]
    if is_dataclass(value):
        return _safe(asdict(value))
    if hasattr(value, "to_db_fields"):
        try:
            return _safe(value.to_db_fields())
        except Exception:
            return repr(value)
    if hasattr(value, "model_dump"):
        return _safe(value.model_dump())
    return repr(value)


def _safe_value(key: Any, value: Any) -> Any:
    if str(key or "").lower() == "embedding":
        if not value:
            return None
        try:
            return {"present": True, "dimensions": len(value)}
        except Exception:
            return {"present": True}
    return _safe(value)
