# -*- coding: utf-8 -*-
"""Frontend-facing memory graph operations."""

from __future__ import annotations

from typing import Any, Optional
import time
import uuid

from .core.context import Context
from .core.event import Event
from .core.pattern import Pattern
from .storage.graph_store import GraphStore


class MemoryGraphOps:
    """Provide stable CRUD-like operations for frontend and debugging flows."""

    def __init__(self, store: GraphStore, dynamic_engine: Optional[Any] = None):
        self.store = store
        self.dynamic_engine = dynamic_engine

    def write(
        self,
        item: Any,
        kind: str = "",
        evolve: bool = True,
        entity_ids: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        memory_kind = self._infer_kind(item, kind)
        if memory_kind == "event":
            event = self._coerce_event(item)
            existing = self.store.get_event(event.id) if event.id else None
            action = "updated" if existing else "created"
            if existing:
                event.created_at = event.created_at or existing.created_at
                event.embedding = event.embedding or existing.embedding
                self.store.update_event(event)
            else:
                self.store.save_event(event)
            linked_entities = self._upsert_event_entities(
                event_id=event.id,
                entity_ids=entity_ids,
                current_time=event.last_active or event.timestamp or int(time.time()),
            )
            if evolve and self.dynamic_engine:
                self.dynamic_engine.evolve_existing_events([event])
            stored = self.store.get_event(event.id) or event
            return {
                "kind": "event",
                "action": action,
                "entity_links": linked_entities,
                "item": self._serialize_event(stored),
            }

        if memory_kind == "context":
            context = self._coerce_context(item)
            existing = self.store.get_context(context.id) if context.id else None
            action = "updated" if existing else "created"
            if existing:
                context.created_at = context.created_at or existing.created_at
                context.embedding = context.embedding or existing.embedding
                self.store.update_context(context)
            else:
                self.store.save_context(context)
            stored = self.store.get_context(context.id) or context
            return {
                "kind": "context",
                "action": action,
                "item": self._serialize_context(stored),
            }

        if memory_kind == "pattern":
            pattern = self._coerce_pattern(item)
            existing = self.store.get_pattern(pattern.id) if pattern.id else None
            action = "updated" if existing else "created"
            if existing:
                pattern.created_at = pattern.created_at or existing.created_at
                pattern.embedding = pattern.embedding or existing.embedding
                self.store.update_pattern(pattern)
            else:
                self.store.save_pattern(pattern)
            stored = self.store.get_pattern(pattern.id) or pattern
            return {
                "kind": "pattern",
                "action": action,
                "item": self._serialize_pattern(stored),
            }

        raise ValueError(f"Unsupported write kind: {memory_kind}")

    def remove(
        self,
        memory_id: str,
        kind: str = "event",
        hard_delete: bool = False,
        removed_at: Optional[int] = None,
    ) -> dict[str, Any]:
        ts = int(removed_at or time.time())
        memory_kind = kind.strip().lower() or "event"
        if memory_kind == "event":
            before = self.store.get_event(memory_id)
            if hard_delete:
                self.store.delete_event(memory_id)
                after = None
                action = "deleted"
            else:
                self.store.archive_event(memory_id, ts)
                after = self.store.get_event(memory_id)
                action = "archived"
            return {
                "kind": "event",
                "action": action,
                "memory_id": memory_id,
                "before": self._serialize_event(before) if before else None,
                "after": self._serialize_event(after) if after else None,
            }

        if memory_kind == "context":
            before = self.store.get_context(memory_id)
            if hard_delete:
                self.store.delete_context(memory_id)
                after = None
                action = "deleted"
            else:
                self.store.archive_context(memory_id, ts)
                after = self.store.get_context(memory_id)
                action = "archived"
            return {
                "kind": "context",
                "action": action,
                "memory_id": memory_id,
                "before": self._serialize_context(before) if before else None,
                "after": self._serialize_context(after) if after else None,
            }

        raise ValueError(f"Unsupported remove kind: {memory_kind}")

    def merge_event(
        self,
        canonical_event_id: str,
        merged_event_id: str,
        merged_at: Optional[int] = None,
        similarity_score: float = 1.0,
        merge_reason: str = "manual_merge",
    ) -> dict[str, Any]:
        if not self.dynamic_engine:
            raise RuntimeError("Dynamic evolution engine is required for event merging")
        result = self.dynamic_engine.merge_events(
            canonical_event_id=canonical_event_id,
            merged_event_id=merged_event_id,
            merged_at=merged_at,
            similarity_score=similarity_score,
            merge_reason=merge_reason,
        )
        result["canonical_event"] = self._serialize_event(self.store.get_event(canonical_event_id))
        result["merged_event"] = self._serialize_event(self.store.get_event(merged_event_id))
        return result

    def merge_context(
        self,
        canonical_context_id: str,
        merged_context_id: str,
        merged_at: Optional[int] = None,
    ) -> dict[str, Any]:
        if not self.dynamic_engine:
            raise RuntimeError("Dynamic evolution engine is required for context merging")
        result = self.dynamic_engine.merge_contexts(
            canonical_context_id=canonical_context_id,
            merged_context_id=merged_context_id,
            merged_at=merged_at,
        )
        result["canonical_context"] = self._serialize_context(self.store.get_context(canonical_context_id))
        result["merged_context"] = self._serialize_context(self.store.get_context(merged_context_id))
        return result

    def query(
        self,
        text: str = "",
        limit: int = 20,
        include_graph: bool = True,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        statuses = None if include_inactive else ["active"]
        events = self.store.list_events(limit=limit, query=text, statuses=statuses)
        contexts = self.store.list_contexts(limit=limit, query=text, statuses=statuses)
        patterns = self.store.list_patterns(limit=limit, query=text, statuses=statuses)

        result = {
            "query": text,
            "stats": self.store.get_stats(),
            "events": [self._serialize_event(event) for event in events],
            "contexts": [self._serialize_context(context) for context in contexts],
            "patterns": [self._serialize_pattern(pattern) for pattern in patterns],
        }
        if include_graph:
            result["edges"] = self._build_edge_bundle(limit=limit * 5, statuses=statuses)
        return result

    def snapshot(
        self,
        limit: int = 20,
        include_inactive: bool = False,
        text: str = "",
    ) -> dict[str, Any]:
        return self.query(
            text=text,
            limit=limit,
            include_graph=True,
            include_inactive=include_inactive,
        )

    def auto_merge(
        self,
        scope: str = "all",
        strategy: str = "auto",
        dry_run: bool = False,
        max_pairs: int = 10,
    ) -> dict[str, Any]:
        if not self.dynamic_engine:
            raise RuntimeError("Dynamic evolution engine is required for auto merge")
        result = self.dynamic_engine.auto_merge(
            scope=scope,
            strategy=strategy,
            dry_run=dry_run,
            max_pairs=max_pairs,
        )
        result["snapshot"] = self.snapshot(limit=max(20, max_pairs * 8), include_inactive=True)
        return result

    def _build_edge_bundle(
        self,
        limit: int,
        statuses: Optional[list[str]],
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            "event_context": self.store.list_event_context_edges(
                limit=limit,
                event_statuses=statuses,
                context_statuses=statuses,
            ),
            "event_pattern": self.store.list_event_pattern_edges(
                limit=limit,
                event_statuses=statuses,
                pattern_statuses=statuses,
            ),
            "event_event": self.store.list_event_relation_edges(
                limit=limit,
                event_statuses=statuses,
            ),
            "next": [],
        }

    def _infer_kind(self, item: Any, kind: str) -> str:
        if kind:
            return kind.strip().lower()
        if isinstance(item, Event):
            return "event"
        if isinstance(item, Context):
            return "context"
        if isinstance(item, Pattern):
            return "pattern"
        if isinstance(item, dict):
            if "context_type" in item or "structured_slots" in item:
                return "context"
            if "pattern_type" in item or "prototype_features" in item:
                return "pattern"
            return "event"
        raise ValueError("Unable to infer memory kind")

    def _coerce_event(self, item: Any) -> Event:
        if isinstance(item, Event):
            event = item
        elif isinstance(item, dict):
            event = Event(
                id=str(item.get("id", "") or ""),
                summary=str(item.get("summary", "") or ""),
                event_type=str(item.get("event_type", "generic") or "generic"),
                action=str(item.get("action", "") or ""),
                causality=str(item.get("causality", "") or ""),
                time_range=dict(item.get("time_range", {}) or {}),
                timestamp=int(item.get("timestamp", 0) or 0),
                last_active=int(item.get("last_active", item.get("timestamp", 0)) or 0),
                created_at=int(item.get("created_at", item.get("timestamp", 0)) or 0),
                updated_at=int(item.get("updated_at", item.get("last_active", item.get("timestamp", 0))) or 0),
                valid_from=int(item.get("valid_from", item.get("timestamp", 0)) or 0),
                valid_to=item.get("valid_to"),
                participants=list(item.get("participants", []) or []),
                location=dict(item.get("location", {}) or {}),
                payload=dict(item.get("payload", {}) or {}),
                evidence=list(item.get("evidence", []) or []),
                consistency=str(item.get("consistency", "uncertain") or "uncertain"),
                salience=float(item.get("salience", 0.5) or 0.5),
                confidence=float(item.get("confidence", 0.7) or 0.7),
                source=str(item.get("source", "manual_write") or "manual_write"),
                status=str(item.get("status", "active") or "active"),
                support_count=int(item.get("support_count", 1) or 1),
                embedding=item.get("embedding"),
            )
        else:
            raise ValueError("Unsupported event payload")

        now = int(time.time())
        if event.timestamp <= 0:
            event.timestamp = event.last_active or now
        if event.last_active <= 0:
            event.last_active = event.timestamp or now
        if event.created_at <= 0:
            event.created_at = event.timestamp or now
        if event.updated_at <= 0:
            event.updated_at = event.last_active or event.timestamp or now
        if event.valid_from <= 0:
            event.valid_from = event.timestamp or now
        event.status = event.status or "active"
        event.source = event.source or "manual_write"
        return event

    def _coerce_context(self, item: Any) -> Context:
        if isinstance(item, Context):
            context = item
        elif isinstance(item, dict):
            context = Context(
                id=str(item.get("id", "") or ""),
                context_type=str(item.get("context_type", "situation") or "situation"),
                subtype=str(item.get("subtype", "generic") or "generic"),
                summary=str(item.get("summary", "") or ""),
                structured_slots=dict(item.get("structured_slots", {}) or {}),
                confidence=float(item.get("confidence", 0.6) or 0.6),
                support_count=int(item.get("support_count", 1) or 1),
                created_at=int(item.get("created_at", 0) or 0),
                updated_at=int(item.get("updated_at", 0) or 0),
                valid_from=int(item.get("valid_from", 0) or 0),
                valid_to=item.get("valid_to"),
                last_seen_at=int(item.get("last_seen_at", 0) or 0),
                status=str(item.get("status", "active") or "active"),
                embedding=item.get("embedding"),
            )
        else:
            raise ValueError("Unsupported context payload")

        now = int(time.time())
        if not context.id:
            context.id = f"ctx_manual_{uuid.uuid4().hex[:12]}"
        if context.created_at <= 0:
            context.created_at = now
        if context.updated_at <= 0:
            context.updated_at = context.created_at
        if context.valid_from <= 0:
            context.valid_from = context.created_at
        if context.last_seen_at <= 0:
            context.last_seen_at = context.updated_at
        context.status = context.status or "active"
        return context

    def _coerce_pattern(self, item: Any) -> Pattern:
        if isinstance(item, Pattern):
            pattern = item
        elif isinstance(item, dict):
            pattern = Pattern(
                id=str(item.get("id", "") or ""),
                pattern_type=str(item.get("pattern_type", "experience") or "experience"),
                summary=str(item.get("summary", "") or ""),
                prototype_features=dict(item.get("prototype_features", {}) or {}),
                support_count=int(item.get("support_count", 1) or 1),
                confidence=float(item.get("confidence", 0.6) or 0.6),
                stability_score=float(item.get("stability_score", 0.5) or 0.5),
                drift_score=float(item.get("drift_score", 0.0) or 0.0),
                created_at=int(item.get("created_at", 0) or 0),
                updated_at=int(item.get("updated_at", 0) or 0),
                valid_from=int(item.get("valid_from", 0) or 0),
                valid_to=item.get("valid_to"),
                last_seen_at=int(item.get("last_seen_at", 0) or 0),
                status=str(item.get("status", "active") or "active"),
                embedding=item.get("embedding"),
            )
        else:
            raise ValueError("Unsupported pattern payload")

        now = int(time.time())
        if not pattern.id:
            pattern.id = f"ptn_manual_{uuid.uuid4().hex[:12]}"
        if pattern.created_at <= 0:
            pattern.created_at = now
        if pattern.updated_at <= 0:
            pattern.updated_at = pattern.created_at
        if pattern.valid_from <= 0:
            pattern.valid_from = pattern.created_at
        if pattern.last_seen_at <= 0:
            pattern.last_seen_at = pattern.updated_at
        pattern.status = pattern.status or "active"
        return pattern

    def _upsert_event_entities(
        self,
        event_id: str,
        entity_ids: Optional[list[Any]],
        current_time: int,
    ) -> int:
        if not entity_ids:
            return 0
        linked = 0
        for entity in entity_ids:
            entity_name = ""
            entity_type = "UNKNOWN"
            if isinstance(entity, dict):
                entity_name = str(
                    entity.get("id")
                    or entity.get("name")
                    or entity.get("value")
                    or ""
                ).strip()
                entity_type = str(entity.get("type", "UNKNOWN") or "UNKNOWN")
            else:
                entity_name = str(entity or "").strip()
            if not entity_name:
                continue
            self.store.ensure_entity(entity_name, entity_type)
            relation = self.store.get_involves_relation(event_id, entity_name)
            if relation:
                relation.c_valid += 1
                relation.t_valid = current_time
                self.store.update_involves_relation(relation)
            else:
                self.store.create_involves_relation(
                    event_id=event_id,
                    entity_id=entity_name,
                    t_created=current_time,
                    t_valid=current_time,
                    c_valid=1,
                )
            linked += 1
        return linked

    def _serialize_event(self, event: Optional[Event]) -> Optional[dict[str, Any]]:
        if event is None:
            return None
        return {
            "id": event.id,
            "summary": event.summary,
            "event_type": event.event_type,
            "action": event.action,
            "causality": event.causality,
            "time_range": event.time_range,
            "timestamp": event.timestamp,
            "last_active": event.last_active,
            "created_at": event.created_at,
            "updated_at": event.updated_at,
            "valid_from": event.valid_from,
            "valid_to": event.valid_to,
            "participants": event.participants,
            "location": event.location,
            "payload": event.payload,
            "evidence": event.evidence,
            "consistency": event.consistency.value if hasattr(event.consistency, "value") else str(event.consistency),
            "salience": event.salience,
            "confidence": event.confidence,
            "source": event.source,
            "status": event.status,
            "support_count": event.support_count,
            "entity_ids": self.store.get_event_entities(event.id),
            "context_ids": [context.id for context in self.store.get_event_contexts(event.id)],
            "pattern_ids": [pattern.id for pattern in self.store.get_event_patterns(event.id)],
            "merge_traces": self.store.list_event_merge_traces(event.id),
        }

    def _serialize_context(self, context: Optional[Context]) -> Optional[dict[str, Any]]:
        if context is None:
            return None
        return {
            "id": context.id,
            "context_type": context.context_type,
            "subtype": context.subtype,
            "summary": context.summary,
            "structured_slots": context.structured_slots,
            "confidence": context.confidence,
            "support_count": context.support_count,
            "created_at": context.created_at,
            "updated_at": context.updated_at,
            "valid_from": context.valid_from,
            "valid_to": context.valid_to,
            "last_seen_at": context.last_seen_at,
            "status": context.status,
        }

    def _serialize_pattern(self, pattern: Optional[Pattern]) -> Optional[dict[str, Any]]:
        if pattern is None:
            return None
        return {
            "id": pattern.id,
            "pattern_type": pattern.pattern_type,
            "summary": pattern.summary,
            "prototype_features": pattern.prototype_features,
            "support_count": pattern.support_count,
            "confidence": pattern.confidence,
            "stability_score": pattern.stability_score,
            "drift_score": pattern.drift_score,
            "created_at": pattern.created_at,
            "updated_at": pattern.updated_at,
            "valid_from": pattern.valid_from,
            "valid_to": pattern.valid_to,
            "last_seen_at": pattern.last_seen_at,
            "status": pattern.status,
        }
