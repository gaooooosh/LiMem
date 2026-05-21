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
    """Run event evolution + consolidation + rebuild BM25 index, audited end-to-end."""
    audit = handle.audit
    with audit.trace("evolve", {"trigger": trigger}) as trace_id:
        with handle.write_lock:
            before = audit.graph_snapshot(handle.ltm)
            events = list(active_events(handle))
            evolution_report = handle.ltm.evolve_events(events)
            consolidation_report = handle.ltm.run_consolidation()
            details = {
                **(evolution_report or {}),
                "active_event_count": len(events),
                **{
                    f"consolidation_{key}": value
                    for key, value in (consolidation_report or {}).items()
                    if isinstance(value, int)
                },
            }
            audit.write(
                trace_id,
                "algorithm_call",
                "evolve_existing_events_completed",
                details={"trigger": trigger, "event_count": len(events), "report": evolution_report},
            )
            audit.write(
                trace_id,
                "algorithm_call",
                "run_consolidation_completed",
                details={"trigger": trigger, "report": consolidation_report},
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
