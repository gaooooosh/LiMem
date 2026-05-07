"""Shared per-handle business logic used by routers."""

from __future__ import annotations

from typing import Iterable

from .pool import LtmHandle


def active_events(handle: LtmHandle):
    return handle.ltm.store.list_events(limit=100000, statuses=["active"])


def rebuild_index(handle: LtmHandle) -> int:
    handle.bm25.rebuild(active_events(handle))
    return handle.bm25.size


def evolve_and_rebuild(handle: LtmHandle, trigger: str = "manual") -> dict[str, int]:
    """Run consolidation + rebuild BM25 index, audited end-to-end."""
    audit = handle.audit
    with audit.trace("evolve", {"trigger": trigger}) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            details = handle.ltm.run_consolidation()
            audit.write(
                trace_id,
                "algorithm_call",
                "run_consolidation_completed",
                details={"trigger": trigger, "report": details},
            )
            handle.bm25.rebuild(active_events(handle))
            audit.write(
                trace_id,
                "index",
                "bm25_rebuilt",
                details={"index_size": handle.bm25.size},
            )
            after = audit.graph_snapshot(handle.ltm)
            audit.write_graph_delta(trace_id, before, after, operation="evolve")
            return details
