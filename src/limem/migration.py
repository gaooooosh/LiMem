# -*- coding: utf-8 -*-
"""Migration helpers for dynamic evolution memory graph."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Optional
import time

from .core.context import Context
from .core.pattern import Pattern
from .utils import hash_summary


@dataclass
class MigrationReport:
    dry_run: bool
    scanned_involves: int = 0
    migrated_in_to_context: int = 0
    scanned_traits: int = 0
    migrated_abstract_to_pattern: int = 0
    created_contexts: int = 0
    created_patterns: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LegacyEdgeAdapter:
    """Compatibility adapter for old edge semantics.

    Entity/INVOLVES remains available as an indexing layer for legacy queries.
    It is not the semantic core of the dynamic graph.
    """

    def __init__(self, store: Any):
        self.store = store

    def get_in_edges(self, event_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []

        # New dynamic IN edges
        try:
            resp = self.store.conn.execute(
                """
                MATCH (:Event {id: $event_id})-[r:IN_REL]->(c:Context)
                RETURN c.id, c.summary, r.confidence, r.weight, r.original_signal
                """,
                {"event_id": event_id},
            )
            while resp.has_next():
                row = resp.get_next()
                rows.append(
                    {
                        "type": "IN",
                        "target_id": row[0],
                        "target_summary": row[1],
                        "confidence": row[2],
                        "weight": row[3],
                        "original_signal": row[4],
                    }
                )
        except Exception:
            pass

        # Legacy INVOLVES as virtual IN
        try:
            resp = self.store.conn.execute(
                """
                MATCH (:Event {id: $event_id})-[r:INVOLVES]->(en:Entity)
                RETURN en.id, r.c_valid, r.t_valid
                """,
                {"event_id": event_id},
            )
            while resp.has_next():
                row = resp.get_next()
                rows.append(
                    {
                        "type": "IN",
                        "target_id": f"legacy_entity::{row[0]}",
                        "target_summary": str(row[0]),
                        "confidence": 1.0,
                        "weight": row[1] or 1,
                        "original_signal": "INVOLVES",
                    }
                )
        except Exception:
            pass
        return rows


def migrate_to_dynamic_graph(
    store: Any,
    dry_run: bool = True,
) -> MigrationReport:
    """Compatibility migration to dynamic minimal edge design.

    Mapping strategy (current project schema):
    - `INVOLVES` -> `IN_REL` with Context nodes (entity context)
    - `PERMANENT_TRAIT` -> `ABSTRACT_TO` with Pattern nodes

    Existing Entity/INVOLVES tables are preserved to keep old queries working.
    They remain compatibility/indexing layer, not semantic core.
    """

    report = MigrationReport(dry_run=dry_run)
    now = int(time.time())

    # 1) INVOLVES -> IN_REL(Event->Context)
    try:
        resp = store.conn.execute(
            """
            MATCH (e:Event)-[r:INVOLVES]->(en:Entity)
            RETURN e.id, en.id, r.c_valid, r.t_valid
            """
        )
        rows = []
        while resp.has_next():
            rows.append(resp.get_next())
        report.scanned_involves = len(rows)

        for event_id, entity_id, c_valid, t_valid in rows:
            ctx_id = f"ctx_entity_{hash_summary(str(entity_id))[:20]}"
            if dry_run:
                report.migrated_in_to_context += 1
                continue

            ctx = store.get_context(ctx_id)
            if not ctx:
                ctx = Context(
                    id=ctx_id,
                    context_type="entity",
                    subtype="legacy_involves",
                    summary=f"实体上下文:{entity_id}",
                    structured_slots={"entity_id": str(entity_id)},
                    confidence=1.0,
                    support_count=1,
                    created_at=t_valid or now,
                    updated_at=t_valid or now,
                    valid_from=t_valid or now,
                    last_seen_at=t_valid or now,
                    status="active",
                )
                store.save_context(ctx)
                report.created_contexts += 1

            store.link_event_to_context(
                event_id=event_id,
                context_id=ctx_id,
                confidence=1.0,
                weight=float(c_valid or 1),
                original_signal="INVOLVES",
                evidence_span=str(entity_id),
                timestamp=int(t_valid or now),
            )
            report.migrated_in_to_context += 1
    except Exception:
        report.skipped += 1

    # 2) PERMANENT_TRAIT -> ABSTRACT_TO(Event->Pattern)
    try:
        resp = store.conn.execute(
            """
            MATCH (u:User)-[r:PERMANENT_TRAIT]->(e:Event)
            RETURN u.id, e.id, e.summary, r.t_created
            """
        )
        rows = []
        while resp.has_next():
            rows.append(resp.get_next())
        report.scanned_traits = len(rows)
        for user_id, event_id, summary, t_created in rows:
            ptn_id = f"ptn_trait_{hash_summary(str(user_id) + str(event_id))[:20]}"
            if dry_run:
                report.migrated_abstract_to_pattern += 1
                continue
            pattern = store.get_pattern(ptn_id)
            if not pattern:
                pattern = Pattern(
                    id=ptn_id,
                    pattern_type="trait",
                    summary=summary or f"{user_id} trait",
                    prototype_features={"user_id": user_id, "original_type": "PERMANENT_TRAIT"},
                    support_count=1,
                    confidence=1.0,
                    stability_score=0.9,
                    drift_score=0.0,
                    created_at=t_created or now,
                    updated_at=t_created or now,
                    valid_from=t_created or now,
                    last_seen_at=t_created or now,
                    status="active",
                )
                store.save_pattern(pattern)
                report.created_patterns += 1

            store.link_event_to_pattern(
                event_id=event_id,
                pattern_id=ptn_id,
                confidence=1.0,
                contribution_weight=1.0,
                timestamp=int(t_created or now),
            )
            report.migrated_abstract_to_pattern += 1
    except Exception:
        report.skipped += 1

    return report
