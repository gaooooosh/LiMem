# -*- coding: utf-8 -*-
"""Dynamic evolution engine for long-term memory graph.

This module implements incremental, local, and evolution-aware algorithms:
1) Incremental Event Ingestion
2) Dynamic Context Resolution
3) Local NEXT Evolution
4) Incremental Pattern Induction
5) Evolution-aware Retrieval
6) Consolidation and Forgetting
7) Conflict and Drift Management
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import json
import math
import os
import re
import time
import uuid

try:
    import dashscope
    from dashscope import Generation
except Exception:  # pragma: no cover - optional dependency for offline mode
    dashscope = None
    Generation = None

from ..config import (
    APPEND_FIRST_MODE,
    ARCHIVE_EVENT_SECONDS,
    CONSOLIDATION_LOG_PATH,
    CONSOLIDATION_MIN_INTERVAL_SECONDS,
    CONTEXT_CANDIDATE_LIMIT,
    CONTEXT_CONFLICT_THRESHOLD,
    CONTEXT_CORE_SLOT_WEIGHT,
    CONTEXT_AUX_SLOT_WEIGHT,
    CONTEXT_QUERY_CANDIDATE_LIMIT,
    CONTEXT_REUSE_THRESHOLD,
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    DECAY_RATE,
    DECAY_STEP,
    ENABLE_AUTO_CONSOLIDATION,
    ENABLE_EVENT_RELATIONS,
    EVENT_CONSOLIDATION_CANDIDATE_LIMIT,
    EVENT_CONSOLIDATION_PAYLOAD_WEIGHT,
    EVENT_CONSOLIDATION_PATTERN_WEIGHT,
    EVENT_CONSOLIDATION_TEXT_WEIGHT,
    EVENT_CONSOLIDATION_CONTEXT_WEIGHT,
    EVENT_CONSOLIDATION_THRESHOLD,
    EVENT_CONSOLIDATION_TIME_WEIGHT,
    EVENT_CONSOLIDATION_WINDOW_SECONDS,
    EVENT_RELATION_CANDIDATE_LIMIT,
    EVENT_RELATION_CONFIDENCE_THRESHOLD,
    EVENT_RELATION_MAX_LINKS_PER_EVENT,
    EVENT_RELATION_PRUNE_THRESHOLD,
    EVENT_RELATION_WINDOW_SECONDS,
    EVENT_MERGE_TRACE_LOG_PATH,
    EVENT_MERGE_TRACE_STRATEGY_VERSION,
    GENERATION_MODEL,
    NEXT_MAX_PREDECESSORS,
    NEXT_MIN_SCORE,
    NEXT_RECENT_WINDOW_SECONDS,
    OFFLINE_MODE,
    PATTERN_ASSIGN_THRESHOLD,
    PATTERN_CANDIDATE_LIMIT,
    PATTERN_DRIFT_THRESHOLD,
    PATTERN_MERGE_THRESHOLD,
    PATTERN_QUERY_CANDIDATE_LIMIT,
    PATTERN_SPLIT_DRIFT_THRESHOLD,
    REINFORCEMENT_STEP,
    RETRIEVAL_DEFAULT_CANDIDATE_LIMIT,
    RETRIEVAL_WEIGHT_CONTEXT,
    RETRIEVAL_WEIGHT_EVENT_SIM,
    RETRIEVAL_WEIGHT_PATTERN,
    RETRIEVAL_WEIGHT_RECENCY,
    RETRIEVAL_WEIGHT_SUPPORT,
    RETRIEVAL_WEIGHT_VALIDITY,
    STALE_SECONDS,
    WEAK_EDGE_PRUNE_THRESHOLD,
)
from ..core.context import Context
from ..core.event import Event
from ..core.pattern import Pattern
from ..utils import hash_summary, robust_json_loads, safe_json_dumps, safe_json_loads


@dataclass
class DynamicEvolutionConfig:
    append_first_mode: bool = APPEND_FIRST_MODE
    offline_mode: bool = OFFLINE_MODE
    merge_decision_strategy: str = "auto"
    llm_api_key: str = DASHSCOPE_API_KEY
    llm_base_url: str = DASHSCOPE_BASE_URL
    llm_model: str = GENERATION_MODEL
    context_reuse_threshold: float = CONTEXT_REUSE_THRESHOLD
    context_conflict_threshold: float = CONTEXT_CONFLICT_THRESHOLD
    context_candidate_limit: int = CONTEXT_CANDIDATE_LIMIT
    context_query_candidate_limit: int = CONTEXT_QUERY_CANDIDATE_LIMIT
    context_core_slot_weight: float = CONTEXT_CORE_SLOT_WEIGHT
    context_aux_slot_weight: float = CONTEXT_AUX_SLOT_WEIGHT

    next_recent_window_seconds: int = NEXT_RECENT_WINDOW_SECONDS
    next_max_predecessors: int = NEXT_MAX_PREDECESSORS
    next_min_score: float = NEXT_MIN_SCORE

    pattern_assign_threshold: float = PATTERN_ASSIGN_THRESHOLD
    pattern_drift_threshold: float = PATTERN_DRIFT_THRESHOLD
    pattern_split_drift_threshold: float = PATTERN_SPLIT_DRIFT_THRESHOLD
    pattern_merge_threshold: float = PATTERN_MERGE_THRESHOLD
    pattern_candidate_limit: int = PATTERN_CANDIDATE_LIMIT
    pattern_query_candidate_limit: int = PATTERN_QUERY_CANDIDATE_LIMIT

    reinforcement_step: float = REINFORCEMENT_STEP
    decay_step: float = DECAY_STEP
    stale_seconds: int = STALE_SECONDS
    archive_event_seconds: int = ARCHIVE_EVENT_SECONDS

    retrieval_weight_event_sim: float = RETRIEVAL_WEIGHT_EVENT_SIM
    retrieval_weight_context: float = RETRIEVAL_WEIGHT_CONTEXT
    retrieval_weight_pattern: float = RETRIEVAL_WEIGHT_PATTERN
    retrieval_weight_recency: float = RETRIEVAL_WEIGHT_RECENCY
    retrieval_weight_validity: float = RETRIEVAL_WEIGHT_VALIDITY
    retrieval_weight_support: float = RETRIEVAL_WEIGHT_SUPPORT
    retrieval_default_candidate_limit: int = RETRIEVAL_DEFAULT_CANDIDATE_LIMIT

    enable_auto_consolidation: bool = ENABLE_AUTO_CONSOLIDATION
    enable_event_relations: bool = ENABLE_EVENT_RELATIONS
    consolidation_min_interval_seconds: int = CONSOLIDATION_MIN_INTERVAL_SECONDS
    weak_edge_prune_threshold: float = WEAK_EDGE_PRUNE_THRESHOLD
    consolidation_log_path: str = CONSOLIDATION_LOG_PATH
    event_consolidation_window_seconds: int = EVENT_CONSOLIDATION_WINDOW_SECONDS
    event_consolidation_candidate_limit: int = EVENT_CONSOLIDATION_CANDIDATE_LIMIT
    event_consolidation_threshold: float = EVENT_CONSOLIDATION_THRESHOLD
    event_consolidation_text_weight: float = EVENT_CONSOLIDATION_TEXT_WEIGHT
    event_consolidation_context_weight: float = EVENT_CONSOLIDATION_CONTEXT_WEIGHT
    event_consolidation_pattern_weight: float = EVENT_CONSOLIDATION_PATTERN_WEIGHT
    event_consolidation_payload_weight: float = EVENT_CONSOLIDATION_PAYLOAD_WEIGHT
    event_consolidation_time_weight: float = EVENT_CONSOLIDATION_TIME_WEIGHT
    event_relation_window_seconds: int = EVENT_RELATION_WINDOW_SECONDS
    event_relation_candidate_limit: int = EVENT_RELATION_CANDIDATE_LIMIT
    event_relation_max_links_per_event: int = EVENT_RELATION_MAX_LINKS_PER_EVENT
    event_relation_confidence_threshold: float = EVENT_RELATION_CONFIDENCE_THRESHOLD
    event_relation_prune_threshold: float = EVENT_RELATION_PRUNE_THRESHOLD
    event_merge_trace_strategy_version: str = EVENT_MERGE_TRACE_STRATEGY_VERSION
    event_merge_trace_log_path: str = EVENT_MERGE_TRACE_LOG_PATH


class DynamicEvolutionEngine:
    """Incremental dynamic graph update and retrieval engine."""

    def __init__(self, store: Any, config: Optional[DynamicEvolutionConfig] = None):
        self.store = store
        self.config = config or DynamicEvolutionConfig()
        self._last_consolidation_at = 0

    # -------------------------------------------------------------------------
    # Algorithm 1: Incremental Event Ingestion
    # -------------------------------------------------------------------------
    def ingest_record(self, record: Any) -> dict[str, Any]:
        events = self.extract_events(record)
        return self.write_event_batch(events, record=record)

    def extract_events(self, record: Any) -> list[Event]:
        if isinstance(record, Event):
            return [record]
        if not isinstance(record, dict):
            return []
        raw_events = record.get("events")
        if isinstance(raw_events, list):
            result = []
            for raw in raw_events:
                if not isinstance(raw, dict):
                    continue
                now = int(raw.get("timestamp", int(time.time())) or int(time.time()))
                result.append(Event.from_extraction(raw, now))
            return result
        raw = record.get("event")
        if isinstance(raw, dict):
            now = int(raw.get("timestamp", int(time.time())) or int(time.time()))
            return [Event.from_extraction(raw, now)]
        return []

    def write_event_batch(
        self,
        events: list[Event],
        record: Optional[Any] = None,
        entities_by_event: Optional[dict[str, list[str]]] = None,
    ) -> dict[str, Any]:
        if not events:
            return {
                "event_count": 0,
                "context_links": 0,
                "event_relation_links": 0,
                "next_links": 0,
                "pattern_links": 0,
            }

        new_events: list[Event] = []
        in_links = 0
        pattern_links = 0
        now = int(time.time())

        # Append-first: create event nodes without global rescoring.
        for event in events:
            event.id = self._ensure_append_first_event_id(event)
            if event.created_at <= 0:
                event.created_at = event.timestamp or event.last_active or now
            event.updated_at = event.last_active or event.timestamp or now
            event.valid_from = event.valid_from or event.timestamp or now
            event.status = event.status or "active"
            self.store.save_event(event)
            new_events.append(event)

        for event in new_events:
            contexts = self.resolve_contexts(event)
            for context in contexts:
                self.store.link_event_to_context(
                    event_id=event.id,
                    context_id=context.id,
                    confidence=max(0.3, min(1.0, context.confidence)),
                    weight=max(0.1, min(5.0, 1.0 + math.log1p(context.support_count))),
                    original_type="dynamic_context",
                    timestamp=event.last_active or event.timestamp or now,
                )
                in_links += 1

            pattern_id = self.update_patterns_for_event(event)
            if pattern_id:
                pattern_links += 1

        event_relation_links = self.extract_event_relations_for_new_events(new_events, recent_candidates=None)

        if self.config.enable_auto_consolidation:
            ts = int(time.time())
            if ts - self._last_consolidation_at >= self.config.consolidation_min_interval_seconds:
                self.run_consolidation(current_time=ts)

        return {
            "event_count": len(new_events),
            "context_links": in_links,
            "event_relation_links": event_relation_links,
            "next_links": 0,
            "pattern_links": pattern_links,
        }

    def evolve_existing_events(self, events: list[Event]) -> dict[str, int]:
        """Apply local dynamic updates for already-persisted events."""
        if not events:
            return {"context_links": 0, "event_relation_links": 0, "next_links": 0, "pattern_links": 0}

        context_links = 0
        pattern_links = 0
        for event in events:
            contexts = self.resolve_contexts(event)
            for context in contexts:
                self.store.link_event_to_context(
                    event_id=event.id,
                    context_id=context.id,
                    confidence=max(0.3, min(1.0, context.confidence)),
                    weight=max(0.1, min(5.0, 1.0 + math.log1p(context.support_count))),
                    original_type="dynamic_context",
                    timestamp=event.last_active or event.timestamp or int(time.time()),
                )
                context_links += 1

            pattern_id = self.update_patterns_for_event(event)
            if pattern_id:
                pattern_links += 1

        event_relation_links = self.extract_event_relations_for_new_events(events, recent_candidates=None)
        if self.config.enable_auto_consolidation:
            ts = int(time.time())
            if ts - self._last_consolidation_at >= self.config.consolidation_min_interval_seconds:
                self.run_consolidation(current_time=ts)

        return {
            "context_links": context_links,
            "event_relation_links": event_relation_links,
            "next_links": 0,
            "pattern_links": pattern_links,
        }

    # -------------------------------------------------------------------------
    # Algorithm 2: Dynamic Context Resolution
    # -------------------------------------------------------------------------
    def resolve_contexts(self, event: Event) -> list[Context]:
        draft = self._build_context_draft(event)
        match = self.match_existing_context(draft)
        if match is None:
            return [self.create_context(draft)]

        if self.detect_conflict(match, draft):
            return [self.handle_context_conflict(match, draft)]

        updated = self.update_context(match, draft)
        return [updated]

    def match_existing_context(self, context_draft: Context) -> Optional[Context]:
        candidates = self.store.find_context_candidates(
            context_type=context_draft.context_type,
            subtype=context_draft.subtype,
            limit=self.config.context_candidate_limit,
            only_active=True,
        )
        best: Optional[Context] = None
        best_score = -1.0
        for candidate in candidates:
            score = self._context_similarity(candidate, context_draft)
            if score > best_score:
                best = candidate
                best_score = score
        if best is None:
            return None
        return best if best_score >= self.config.context_reuse_threshold else None

    def update_context(self, context_node: Context, evidence: Context) -> Context:
        now = max(evidence.updated_at, int(time.time()))
        merged_slots = dict(context_node.structured_slots)
        for key, value in evidence.structured_slots.items():
            if value not in (None, "", [], {}):
                merged_slots[key] = value

        context_node.structured_slots = merged_slots
        context_node.summary = context_node.summary or evidence.summary
        context_node.support_count += 1
        context_node.updated_at = now
        context_node.last_seen_at = now
        context_node.confidence = min(
            1.0,
            (context_node.confidence * 0.8) + (evidence.confidence * 0.2) + self.config.reinforcement_step * 0.2,
        )
        context_node.status = "active"
        self.store.update_context(context_node)
        return context_node

    def create_context(self, context_draft: Context) -> Context:
        now = context_draft.updated_at or int(time.time())
        context_draft.id = context_draft.id or self._new_context_id(context_draft)
        context_draft.created_at = context_draft.created_at or now
        context_draft.updated_at = now
        context_draft.valid_from = context_draft.valid_from or now
        context_draft.last_seen_at = now
        context_draft.status = context_draft.status or "active"
        self.store.save_context(context_draft)
        return context_draft

    # -------------------------------------------------------------------------
    # Algorithm 3: Explicit Event-Event Semantic Relations
    # -------------------------------------------------------------------------
    def extract_event_relations_for_new_events(
        self,
        new_events: list[Event],
        recent_candidates: Optional[list[Event]],
    ) -> int:
        if (
            not new_events
            or not self.config.enable_event_relations
            or not self._llm_relation_available()
        ):
            return 0

        count = 0
        visited_pairs: set[tuple[str, str]] = set()
        ordered = sorted(new_events, key=lambda e: e.last_active or e.timestamp or 0)
        history = recent_candidates
        if history is None:
            now = max(e.last_active or e.timestamp or 0 for e in new_events)
            history = self.store.get_recent_events(
                current_time=now,
                window_seconds=self.config.event_relation_window_seconds,
                limit=200,
            )

        new_ids = {event.id for event in ordered}
        for event in ordered:
            candidates: dict[str, tuple[float, Event]] = {}
            accepted = 0
            for candidate in ordered + list(history or []):
                if candidate.id == event.id:
                    continue
                if candidate.id in new_ids and (candidate.last_active or candidate.timestamp or 0) > (event.last_active or event.timestamp or 0):
                    continue
                pair_key = tuple(sorted([event.id, candidate.id]))
                if pair_key in visited_pairs:
                    continue
                score = self._event_relation_candidate_score(event, candidate)
                if score <= 0.0:
                    continue
                existing = candidates.get(candidate.id)
                if existing is None or score > existing[0]:
                    candidates[candidate.id] = (score, candidate)

            ranked_candidates = sorted(candidates.values(), key=lambda item: item[0], reverse=True)
            for _, candidate in ranked_candidates[: self.config.event_relation_candidate_limit]:
                pair_key = tuple(sorted([event.id, candidate.id]))
                visited_pairs.add(pair_key)
                decision = self._llm_event_relation_decision(event, candidate)
                if decision is None or not decision.get("should_link", False):
                    continue
                confidence = float(decision.get("confidence", 0.0) or 0.0)
                if confidence < self.config.event_relation_confidence_threshold:
                    continue
                from_id = str(decision.get("from_id", "") or "").strip()
                to_id = str(decision.get("to_id", "") or "").strip()
                valid_ids = {event.id, candidate.id}
                if from_id not in valid_ids or to_id not in valid_ids or from_id == to_id:
                    ordered_pair = sorted(
                        [event, candidate],
                        key=lambda item: (item.last_active or item.timestamp or 0, item.id),
                    )
                    from_id, to_id = ordered_pair[0].id, ordered_pair[1].id
                self.store.link_event_relation(
                    from_event_id=from_id,
                    to_event_id=to_id,
                    relation_type=str(decision.get("relation_type", "") or "related_to"),
                    confidence=confidence,
                    reason=str(decision.get("reason", "") or ""),
                    source="llm_event_relation",
                    timestamp=max(
                        event.last_active or event.timestamp or 0,
                        candidate.last_active or candidate.timestamp or 0,
                        int(time.time()),
                    ),
                )
                accepted += 1
                count += 1
                if accepted >= self.config.event_relation_max_links_per_event:
                    break
        return count

    # -------------------------------------------------------------------------
    # Algorithm 4: Incremental Pattern Induction
    # -------------------------------------------------------------------------
    def update_patterns_for_event(self, event: Event) -> Optional[str]:
        candidates = self.retrieve_candidate_patterns(event)
        event_features = self._event_features(event)
        best: Optional[Pattern] = None
        best_score = -1.0
        for pattern in candidates:
            score = self._pattern_similarity(event_features, pattern.prototype_features)
            if score > best_score:
                best = pattern
                best_score = score

        now = event.last_active or event.timestamp or int(time.time())
        if best and best_score >= self.config.pattern_assign_threshold:
            self.assign_event_to_pattern(event, best)
            best.support_count += 1
            best.updated_at = now
            best.last_seen_at = now
            best.confidence = min(1.0, best.confidence + self.config.reinforcement_step)
            best.stability_score = min(1.0, best.stability_score + self.config.reinforcement_step * 0.5)
            best.drift_score = max(0.0, best.drift_score - self.config.reinforcement_step * 0.3)
            best.prototype_features = self._merge_pattern_features(best.prototype_features, event_features)
            self.store.update_pattern(best)
            self.maybe_split_pattern(best)
            return best.id

        if best and best_score < self.config.pattern_drift_threshold:
            self.handle_pattern_drift(best, event)

        pattern = self.create_pattern_from_event(event)
        self.assign_event_to_pattern(event, pattern)
        return pattern.id

    def retrieve_candidate_patterns(self, event: Event) -> list[Pattern]:
        pattern_type = self._infer_pattern_type(event)
        return self.store.find_pattern_candidates(
            pattern_type=pattern_type,
            limit=self.config.pattern_candidate_limit,
            only_active=True,
        )

    def assign_event_to_pattern(self, event: Event, pattern: Pattern) -> None:
        timestamp = event.last_active or event.timestamp or int(time.time())
        confidence = min(1.0, max(0.1, event.confidence))
        contribution = min(2.0, max(0.1, 1.0 + math.log1p(event.support_count)))
        self.store.link_event_to_pattern(
            event_id=event.id,
            pattern_id=pattern.id,
            confidence=confidence,
            contribution_weight=contribution,
            timestamp=timestamp,
        )

    def create_pattern_from_event(self, event: Event) -> Pattern:
        now = event.last_active or event.timestamp or int(time.time())
        pattern_type = self._infer_pattern_type(event)
        features = self._event_features(event)
        signature = f"{pattern_type}:{event.summary}:{features.get('action', '')}"
        pattern = Pattern(
            id=f"ptn_{hash_summary(signature)[:16]}_{uuid.uuid4().hex[:6]}",
            pattern_type=pattern_type,
            summary=event.summary[:160],
            prototype_features=features,
            support_count=1,
            confidence=max(0.45, event.confidence),
            stability_score=0.45,
            drift_score=0.0,
            created_at=now,
            updated_at=now,
            valid_from=now,
            last_seen_at=now,
            status="active",
        )
        self.store.save_pattern(pattern)
        return pattern

    def maybe_split_pattern(self, pattern: Pattern) -> Optional[str]:
        if (
            pattern.drift_score < self.config.pattern_split_drift_threshold
            or pattern.support_count < 3
        ):
            return None
        now = int(time.time())
        pattern.status = "weakened"
        pattern.valid_to = now
        pattern.updated_at = now
        self.store.update_pattern(pattern)

        new_pattern = Pattern(
            id=f"{pattern.id}_split_{uuid.uuid4().hex[:6]}",
            pattern_type=pattern.pattern_type,
            summary=pattern.summary,
            prototype_features=pattern.prototype_features,
            support_count=1,
            confidence=max(0.4, pattern.confidence * 0.8),
            stability_score=max(0.3, pattern.stability_score * 0.7),
            drift_score=0.0,
            created_at=now,
            updated_at=now,
            valid_from=now,
            last_seen_at=now,
            status="active",
        )
        self.store.save_pattern(new_pattern)
        return new_pattern.id

    def maybe_merge_patterns(self, pattern_a: Pattern, pattern_b: Pattern) -> bool:
        if pattern_a.id == pattern_b.id or pattern_a.pattern_type != pattern_b.pattern_type:
            return False
        score = self._pattern_similarity(pattern_a.prototype_features, pattern_b.prototype_features)
        if score < self.config.pattern_merge_threshold:
            return False
        now = int(time.time())
        pattern_a.support_count += pattern_b.support_count
        pattern_a.confidence = min(1.0, (pattern_a.confidence + pattern_b.confidence) / 2.0 + 0.05)
        pattern_a.stability_score = min(1.0, (pattern_a.stability_score + pattern_b.stability_score) / 2.0)
        pattern_a.updated_at = now
        pattern_a.last_seen_at = now
        pattern_a.prototype_features = self._merge_pattern_features(
            pattern_a.prototype_features, pattern_b.prototype_features
        )
        self.store.update_pattern(pattern_a)
        self.store.relink_pattern_edges(
            source_pattern_id=pattern_b.id,
            target_pattern_id=pattern_a.id,
            timestamp=now,
        )

        pattern_b.status = "merged"
        pattern_b.valid_to = now
        pattern_b.updated_at = now
        self.store.update_pattern(pattern_b)
        return True

    # -------------------------------------------------------------------------
    # Algorithm 5: Evolution-aware Retrieval
    # -------------------------------------------------------------------------
    def retrieve_memories(
        self,
        query: str,
        top_k: int,
        query_entities: Optional[list[str]] = None,
        events: Optional[list[Event]] = None,
    ) -> list[dict[str, Any]]:
        query_entities = query_entities or []
        if events is None:
            route_bundle = self.retrieve_candidate_events_for_query(
                query=query,
                query_entities=query_entities,
                limit=max(top_k * 4, self.config.retrieval_default_candidate_limit),
            )
            events = route_bundle["events"]
        rows = []
        for event in events:
            row = self._score_event_for_retrieval(
                query=query,
                query_entities=query_entities,
                event_data={
                    "id": event.id,
                    "summary": event.summary,
                    "last_active": event.last_active,
                    "status": event.status,
                    "support_count": event.support_count,
                    "confidence": event.confidence,
                },
            )
            rows.append(row)
        rows.sort(key=lambda x: x["evolution_score"], reverse=True)
        return rows[:top_k]

    def enrich_raw_events_for_retrieval(
        self,
        query: str,
        raw_events: list[dict[str, Any]],
        query_entities: list[str],
    ) -> list[dict[str, Any]]:
        enriched = []
        for row in raw_events:
            scored = self._score_event_for_retrieval(query, query_entities, row)
            merged = dict(row)
            merged.update(scored)
            enriched.append(merged)
        return enriched

    def retrieve_candidate_events_for_query(
        self,
        query: str,
        query_entities: Optional[list[str]] = None,
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        query_entities = query_entities or []
        route_limit = int(limit or self.config.retrieval_default_candidate_limit)

        # Entity remains an indexing/compatibility route, not the semantic core.
        entity_events = self.store.get_events_by_entities(query_entities) if query_entities else []

        contexts = self.store.retrieve_candidate_contexts_for_query(
            query=query,
            query_entities=query_entities,
            limit=self.config.context_query_candidate_limit,
        )
        patterns = self.store.retrieve_candidate_patterns_for_query(
            query=query,
            query_entities=query_entities,
            limit=self.config.pattern_query_candidate_limit,
        )
        context_events = self.store.retrieve_events_by_contexts(
            [context.id for context in contexts],
            limit=route_limit,
        )
        pattern_events = self.store.retrieve_events_by_patterns(
            [pattern.id for pattern in patterns],
            limit=route_limit,
        )

        merged: dict[str, Event] = {}
        for route_events in (entity_events, context_events, pattern_events):
            for event in route_events:
                merged[event.id] = event

        return {
            "events": list(merged.values()),
            "entity_events": entity_events,
            "context_events": context_events,
            "pattern_events": pattern_events,
            "contexts": contexts,
            "patterns": patterns,
        }

    # -------------------------------------------------------------------------
    # Algorithm 6: Consolidation and Forgetting
    # -------------------------------------------------------------------------
    def run_consolidation(
        self,
        current_time: Optional[int] = None,
        dry_run: bool = False,
        strategy: str = "auto",
    ) -> dict[str, int]:
        now = int(current_time or time.time())
        event_report = self.consolidate_events(now, dry_run=dry_run, strategy=strategy)
        if dry_run:
            report = {
                "dry_run": 1,
                "scanned_events": event_report["scanned_events"],
                "candidate_pairs": event_report["candidate_pairs"],
                "merged_events": event_report["merged_events"],
                "skipped_events": event_report["skipped_events"],
                "merged_contexts": 0,
                "merged_patterns": 0,
                "decayed_nodes": 0,
                "pruned_edges": 0,
                "archived_events": event_report["archived_events"] + self._count_archivable_events(now),
            }
            self._append_consolidation_log(now, report)
            return report
        report = {
            "dry_run": 0,
            "scanned_events": event_report["scanned_events"],
            "candidate_pairs": event_report["candidate_pairs"],
            "merged_events": event_report["merged_events"],
            "skipped_events": event_report["skipped_events"],
            "merged_contexts": self.consolidate_contexts(now, strategy=strategy),
            "merged_patterns": self.consolidate_patterns(now),
            "decayed_nodes": self.decay_stale_nodes(now),
            "pruned_edges": self.prune_weak_edges(now),
            "archived_events": event_report["archived_events"] + self._archive_stale_events(now),
        }
        self._last_consolidation_at = now
        self._append_consolidation_log(now, report)
        return report

    def auto_merge(
        self,
        scope: str = "all",
        strategy: str = "auto",
        dry_run: bool = False,
        current_time: Optional[int] = None,
        max_pairs: int = 10,
    ) -> dict[str, Any]:
        now = int(current_time or time.time())
        normalized_scope = (scope or "all").strip().lower()
        if normalized_scope not in {"all", "event", "events", "context", "contexts"}:
            raise ValueError(f"Unsupported auto merge scope: {scope}")

        include_events = normalized_scope in {"all", "event", "events"}
        include_contexts = normalized_scope in {"all", "context", "contexts"}
        event_plans = self.detect_event_merges(
            current_time=now,
            strategy=strategy,
            max_pairs=max_pairs,
        ) if include_events else []
        context_plans = self.detect_context_merges(
            current_time=now,
            strategy=strategy,
            max_pairs=max_pairs,
        ) if include_contexts else []

        applied_events = 0
        applied_contexts = 0
        if not dry_run:
            for plan in event_plans:
                if self._apply_event_merge_plan(plan, merged_at=now):
                    applied_events += 1
            for plan in context_plans:
                if self._apply_context_merge_plan(plan, merged_at=now):
                    applied_contexts += 1

        return {
            "scope": normalized_scope,
            "requested_strategy": (strategy or "auto").strip().lower(),
            "resolved_strategy": self._resolve_merge_strategy(strategy),
            "dry_run": bool(dry_run),
            "event_candidates": len(event_plans),
            "context_candidates": len(context_plans),
            "merged_events": applied_events,
            "merged_contexts": applied_contexts,
            "event_plans": event_plans,
            "context_plans": context_plans,
        }

    def detect_event_merges(
        self,
        current_time: Optional[int] = None,
        strategy: str = "auto",
        max_pairs: int = 10,
    ) -> list[dict[str, Any]]:
        now = int(current_time or time.time())
        resolved_strategy = self._resolve_merge_strategy(strategy)
        events = self.store.get_recent_events(
            current_time=now,
            window_seconds=self.config.event_consolidation_window_seconds,
            limit=300,
        )
        if not events:
            return []

        event_map = {event.id: event for event in events}
        merged_sources: set[str] = set()
        visited_pairs: set[tuple[str, str]] = set()
        plans: list[dict[str, Any]] = []
        llm_gate = self.config.event_consolidation_threshold * 0.72

        for event in events:
            if event.id in merged_sources or event.status in {"merged", "archived"}:
                continue

            candidates = self._retrieve_event_consolidation_candidates(event, event_map, now)
            for candidate in candidates:
                if candidate.id == event.id or candidate.id in merged_sources:
                    continue
                pair_key = tuple(sorted([event.id, candidate.id]))
                if pair_key in visited_pairs:
                    continue
                visited_pairs.add(pair_key)

                score, reason = self._event_merge_similarity(event, candidate, now)
                gate = (
                    self.config.event_consolidation_threshold
                    if resolved_strategy == "heuristic"
                    else llm_gate
                )
                if score < gate:
                    continue

                canonical, merged = self._pick_canonical_event(event, candidate)
                plan_reason = reason
                confidence = score
                strategy_used = resolved_strategy

                if resolved_strategy == "llm":
                    decision = self._llm_event_merge_decision(
                        left=event,
                        right=candidate,
                        heuristic_score=score,
                        heuristic_reason=reason,
                    )
                    if decision is None:
                        strategy_used = "heuristic"
                    else:
                        strategy_used = "llm"
                        if not decision.get("should_merge", False):
                            continue
                        canonical_id = str(decision.get("canonical_id", "") or "").strip()
                        if canonical_id == event.id:
                            canonical, merged = event, candidate
                        elif canonical_id == candidate.id:
                            canonical, merged = candidate, event
                        plan_reason = str(decision.get("reason", "") or reason or "llm_merge")
                        confidence = max(score, float(decision.get("confidence", score) or score))

                plans.append(
                    {
                        "kind": "event",
                        "strategy": strategy_used,
                        "canonical_event_id": canonical.id,
                        "merged_event_id": merged.id,
                        "canonical_summary": canonical.summary,
                        "merged_summary": merged.summary,
                        "score": round(float(confidence), 4),
                        "reason": plan_reason,
                    }
                )
                merged_sources.add(merged.id)
                break

            if len(plans) >= max_pairs:
                break
        return plans[:max_pairs]

    def detect_context_merges(
        self,
        current_time: Optional[int] = None,
        strategy: str = "auto",
        max_pairs: int = 10,
    ) -> list[dict[str, Any]]:
        _ = int(current_time or time.time())
        resolved_strategy = self._resolve_merge_strategy(strategy)
        contexts = self._list_all_contexts(only_active=True)
        if not contexts:
            return []

        threshold = 0.72 if resolved_strategy == "heuristic" else 0.56
        candidates: list[tuple[float, dict[str, Any]]] = []
        for i in range(len(contexts)):
            for j in range(i + 1, len(contexts)):
                left = contexts[i]
                right = contexts[j]
                if left.status != "active" or right.status != "active":
                    continue
                if left.context_type != right.context_type or left.subtype != right.subtype:
                    continue

                score = self._context_merge_score(left, right)
                if score < threshold:
                    continue

                canonical, merged = self._pick_canonical_context(left, right)
                reason = "context_similarity"
                strategy_used = resolved_strategy
                confidence = score

                if resolved_strategy == "llm":
                    decision = self._llm_context_merge_decision(
                        left=left,
                        right=right,
                        heuristic_score=score,
                    )
                    if decision is None:
                        strategy_used = "heuristic"
                    else:
                        strategy_used = "llm"
                        if not decision.get("should_merge", False):
                            continue
                        canonical_id = str(decision.get("canonical_id", "") or "").strip()
                        if canonical_id == left.id:
                            canonical, merged = left, right
                        elif canonical_id == right.id:
                            canonical, merged = right, left
                        reason = str(decision.get("reason", "") or reason or "llm_merge")
                        confidence = max(score, float(decision.get("confidence", score) or score))

                candidates.append(
                    (
                        confidence,
                        {
                            "kind": "context",
                            "strategy": strategy_used,
                            "canonical_context_id": canonical.id,
                            "merged_context_id": merged.id,
                            "canonical_summary": canonical.summary,
                            "merged_summary": merged.summary,
                            "score": round(float(confidence), 4),
                            "reason": reason,
                        },
                    )
                )

        selected_ids: set[str] = set()
        plans: list[dict[str, Any]] = []
        for _, plan in sorted(candidates, key=lambda item: item[0], reverse=True):
            canonical_id = plan["canonical_context_id"]
            merged_id = plan["merged_context_id"]
            if canonical_id in selected_ids or merged_id in selected_ids:
                continue
            selected_ids.add(canonical_id)
            selected_ids.add(merged_id)
            plans.append(plan)
            if len(plans) >= max_pairs:
                break
        return plans

    def consolidate_contexts(self, now: int, strategy: str = "auto") -> int:
        contexts = self._list_all_contexts(only_active=True)
        resolved_strategy = self._resolve_merge_strategy(strategy)
        llm_gate = 0.56
        merged = 0
        for i in range(len(contexts)):
            for j in range(i + 1, len(contexts)):
                a = contexts[i]
                b = contexts[j]
                if a.status != "active" or b.status != "active":
                    continue
                if a.context_type != b.context_type:
                    continue
                score = self._context_similarity(a, b)
                gate = max(
                    self.config.context_reuse_threshold,
                    0.82 if resolved_strategy == "heuristic" else llm_gate,
                )
                if score < gate:
                    continue

                canonical, merged_context = self._pick_canonical_context(a, b)
                if resolved_strategy == "llm":
                    decision = self._llm_context_merge_decision(
                        left=a,
                        right=b,
                        heuristic_score=score,
                    )
                    if decision is not None:
                        if not decision.get("should_merge", False):
                            continue
                        canonical_id = str(decision.get("canonical_id", "") or "").strip()
                        if canonical_id == a.id:
                            canonical, merged_context = a, b
                        elif canonical_id == b.id:
                            canonical, merged_context = b, a

                self.merge_contexts(
                    canonical_context_id=canonical.id,
                    merged_context_id=merged_context.id,
                    merged_at=now,
                )
                merged += 1
        return merged

    def consolidate_patterns(self, now: int) -> int:
        patterns = self._list_all_patterns(only_active=True)
        merged = 0
        for i in range(len(patterns)):
            for j in range(i + 1, len(patterns)):
                if self.maybe_merge_patterns(patterns[i], patterns[j]):
                    merged += 1
        return merged

    def consolidate_events(
        self,
        now: int,
        dry_run: bool = False,
        strategy: str = "auto",
    ) -> dict[str, int]:
        resolved_strategy = self._resolve_merge_strategy(strategy)
        llm_gate = self.config.event_consolidation_threshold * 0.72
        events = self.store.get_recent_events(
            current_time=now,
            window_seconds=self.config.event_consolidation_window_seconds,
            limit=300,
        )
        report = {
            "scanned_events": len(events),
            "candidate_pairs": 0,
            "merged_events": 0,
            "archived_events": 0,
            "skipped_events": 0,
        }
        if not events:
            return report

        event_map = {event.id: event for event in events}
        merged_sources: set[str] = set()
        visited_pairs: set[tuple[str, str]] = set()

        for event in events:
            if event.id in merged_sources or event.status in {"merged", "archived"}:
                report["skipped_events"] += 1
                continue

            candidates = self._retrieve_event_consolidation_candidates(event, event_map, now)
            for candidate in candidates:
                if candidate.id == event.id or candidate.id in merged_sources:
                    continue
                pair_key = tuple(sorted([event.id, candidate.id]))
                if pair_key in visited_pairs:
                    continue
                visited_pairs.add(pair_key)
                report["candidate_pairs"] += 1

                score, reason = self._event_merge_similarity(event, candidate, now)
                gate = (
                    self.config.event_consolidation_threshold
                    if resolved_strategy == "heuristic"
                    else llm_gate
                )
                if score < gate:
                    continue

                canonical, merged = self._pick_canonical_event(event, candidate)
                if resolved_strategy == "llm":
                    decision = self._llm_event_merge_decision(
                        left=event,
                        right=candidate,
                        heuristic_score=score,
                        heuristic_reason=reason,
                    )
                    if decision is not None:
                        if not decision.get("should_merge", False):
                            continue
                        canonical_id = str(decision.get("canonical_id", "") or "").strip()
                        if canonical_id == event.id:
                            canonical, merged = event, candidate
                        elif canonical_id == candidate.id:
                            canonical, merged = candidate, event
                        reason = str(decision.get("reason", "") or reason or "llm_merge")
                        score = max(score, float(decision.get("confidence", score) or score))
                if not dry_run:
                    self._merge_event_pair(
                        canonical=canonical,
                        merged=merged,
                        similarity_score=score,
                        merge_reason=reason,
                        merged_at=now,
                    )
                merged_sources.add(merged.id)
                report["merged_events"] += 1
                report["archived_events"] += 1
                break

        return report

    def decay_stale_nodes(self, now: int) -> int:
        changed = 0
        stale_before = now - self.config.stale_seconds
        for context in self._list_all_contexts(only_active=False):
            if context.last_seen_at and context.last_seen_at >= stale_before:
                continue
            old_conf = context.confidence
            context.confidence = max(0.0, context.confidence - self.config.decay_step)
            context.updated_at = now
            if context.confidence < 0.25:
                context.status = "deprecated"
                context.valid_to = context.valid_to or now
            if abs(old_conf - context.confidence) > 1e-6:
                self.store.update_context(context)
                changed += 1

        for pattern in self._list_all_patterns(only_active=False):
            if pattern.last_seen_at and pattern.last_seen_at >= stale_before:
                continue
            old_conf = pattern.confidence
            pattern.confidence = max(0.0, pattern.confidence - self.config.decay_step)
            pattern.stability_score = max(0.0, pattern.stability_score - self.config.decay_step * 0.8)
            pattern.updated_at = now
            if pattern.confidence < 0.2:
                pattern.status = "deprecated"
                pattern.valid_to = pattern.valid_to or now
            if abs(old_conf - pattern.confidence) > 1e-6:
                self.store.update_pattern(pattern)
                changed += 1
        return changed

    def prune_weak_edges(self, now: int) -> int:
        stale_before = now - self.config.stale_seconds
        return self.store.prune_weak_event_relation_edges(
            min_confidence=self.config.event_relation_prune_threshold,
            stale_before=stale_before,
        )

    # -------------------------------------------------------------------------
    # Algorithm 7: Conflict and Drift Management
    # -------------------------------------------------------------------------
    def detect_conflict(self, node: Context, new_evidence: Context) -> bool:
        return self._context_conflict_ratio(node, new_evidence) >= self.config.context_conflict_threshold

    def handle_context_conflict(self, context_node: Context, evidence: Context) -> Context:
        now = evidence.updated_at or int(time.time())
        sibling = Context(
            id=f"{self._new_context_id(evidence)}_sib_{uuid.uuid4().hex[:6]}",
            context_type=evidence.context_type,
            subtype=evidence.subtype,
            summary=evidence.summary,
            structured_slots=evidence.structured_slots,
            confidence=max(0.45, evidence.confidence),
            support_count=1,
            created_at=now,
            updated_at=now,
            valid_from=now,
            last_seen_at=now,
            status="active",
        )
        self.store.save_context(sibling)
        context_node.status = "deprecated" if context_node.confidence < 0.4 else context_node.status
        context_node.valid_to = context_node.valid_to or now if context_node.status == "deprecated" else context_node.valid_to
        context_node.updated_at = now
        self.store.update_context(context_node)
        return sibling

    def handle_pattern_drift(self, pattern: Pattern, event: Event) -> None:
        now = event.last_active or event.timestamp or int(time.time())
        pattern.drift_score = min(1.0, pattern.drift_score + self.config.reinforcement_step)
        pattern.confidence = max(0.0, pattern.confidence - self.config.decay_step * 0.5)
        pattern.updated_at = now
        pattern.last_seen_at = now
        if pattern.drift_score >= self.config.pattern_split_drift_threshold:
            self.maybe_split_pattern(pattern)
        else:
            self.store.update_pattern(pattern)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------
    def _retrieve_event_consolidation_candidates(
        self,
        event: Event,
        event_map: dict[str, Event],
        now: int,
    ) -> list[Event]:
        candidates: dict[str, Event] = {}

        def add_candidate(candidate: Optional[Event]) -> None:
            if not candidate or candidate.id == event.id:
                return
            if candidate.status in {"merged", "archived"}:
                return
            candidates[candidate.id] = candidate

        event_time = event.last_active or event.timestamp or now
        recent = self.store.get_recent_events(
            current_time=event_time,
            window_seconds=self.config.event_consolidation_window_seconds,
            limit=self.config.event_consolidation_candidate_limit,
        )
        for candidate in recent:
            add_candidate(event_map.get(candidate.id, candidate))

        for context in self.store.get_event_contexts(event.id)[:2]:
            for candidate in self.store.retrieve_events_by_contexts(
                [context.id],
                limit=self.config.event_consolidation_candidate_limit,
            ):
                add_candidate(event_map.get(candidate.id, candidate))

        for pattern in self.store.get_event_patterns(event.id)[:2]:
            for candidate in self.store.retrieve_events_by_patterns(
                [pattern.id],
                limit=self.config.event_consolidation_candidate_limit,
            ):
                add_candidate(event_map.get(candidate.id, candidate))

        ordered = sorted(
            candidates.values(),
            key=lambda item: abs((item.last_active or item.timestamp or 0) - event_time),
        )
        return ordered[: self.config.event_consolidation_candidate_limit]

    def _event_merge_similarity(
        self,
        event_a: Event,
        event_b: Event,
        now: int,
    ) -> tuple[float, str]:
        text_similarity = self._lexical_similarity(event_a.summary, event_b.summary)
        context_similarity = self._context_overlap_for_events(event_a.id, event_b.id)
        pattern_similarity = self._pattern_overlap_for_events(event_a.id, event_b.id)
        payload_similarity = self._payload_similarity(event_a, event_b)
        time_similarity = self._time_similarity(event_a, event_b, now)
        score = (
            self.config.event_consolidation_text_weight * text_similarity
            + self.config.event_consolidation_context_weight * context_similarity
            + self.config.event_consolidation_pattern_weight * pattern_similarity
            + self.config.event_consolidation_payload_weight * payload_similarity
            + self.config.event_consolidation_time_weight * time_similarity
        )
        reasons = []
        if context_similarity > 0.0:
            reasons.append("shared_context")
        if pattern_similarity > 0.0:
            reasons.append("shared_pattern")
        if payload_similarity >= 0.5:
            reasons.append("payload_similarity")
        if text_similarity >= 0.5:
            reasons.append("summary_similarity")
        if time_similarity >= 0.5:
            reasons.append("time_window")
        return score, ",".join(reasons) or "local_similarity"

    def _event_relation_candidate_score(self, event_a: Event, event_b: Event) -> float:
        if event_a.id == event_b.id:
            return 0.0
        if event_a.status in {"merged", "archived"} or event_b.status in {"merged", "archived"}:
            return 0.0

        session_a = self._event_session_key(event_a)
        session_b = self._event_session_key(event_b)
        if session_a and session_b and session_a != session_b:
            return 0.0

        ts_a = event_a.last_active or event_a.timestamp or 0
        ts_b = event_b.last_active or event_b.timestamp or 0
        if (
            abs(ts_a - ts_b) > self.config.event_relation_window_seconds
            and not (session_a and session_a == session_b)
        ):
            return 0.0

        session_bonus = 1.0 if session_a and session_a == session_b else 0.0
        context_overlap = self._value_similarity(
            self._extract_context_slots(event_a),
            self._extract_context_slots(event_b),
        )
        participant_similarity = self._value_similarity(
            self._participant_labels(event_a.participants),
            self._participant_labels(event_b.participants),
        )
        lexical_similarity = self._lexical_similarity(event_a.summary, event_b.summary)
        action_similarity = 1.0 if event_a.action and event_a.action == event_b.action else 0.0
        time_similarity = self._time_similarity(event_a, event_b, max(ts_a, ts_b, int(time.time())))
        score = (
            0.30 * session_bonus
            + 0.22 * context_overlap
            + 0.18 * participant_similarity
            + 0.15 * lexical_similarity
            + 0.10 * action_similarity
            + 0.05 * time_similarity
        )
        return score if score >= 0.12 or session_bonus > 0.0 else 0.0

    def _event_session_key(self, event: Event) -> str:
        payload = event.payload if isinstance(event.payload, dict) else {}
        episode_metadata = payload.get("episode_metadata", {}) if isinstance(payload.get("episode_metadata"), dict) else {}
        for key in (
            "session_id",
            "session",
            "conversation_id",
            "trip_id",
        ):
            value = payload.get(key) or episode_metadata.get(key)
            if value not in (None, ""):
                return f"{key}:{value}"
        trip_index = episode_metadata.get("trip_index", payload.get("trip_index"))
        if trip_index not in (None, ""):
            return f"trip_index:{trip_index}"
        return ""

    def _pick_canonical_event(self, event_a: Event, event_b: Event) -> tuple[Event, Event]:
        def rank_key(event: Event) -> tuple[int, float, int]:
            return (
                int(event.support_count or 1),
                float(event.confidence or 0.0),
                int(event.created_at or event.timestamp or 0),
            )

        if rank_key(event_a) >= rank_key(event_b):
            return event_a, event_b
        return event_b, event_a

    def _merge_event_pair(
        self,
        canonical: Event,
        merged: Event,
        similarity_score: float,
        merge_reason: str,
        merged_at: int,
    ) -> None:
        canonical.support_count += max(1, merged.support_count)
        canonical.confidence = min(1.0, max(canonical.confidence, merged.confidence) + 0.03)
        canonical.last_active = max(canonical.last_active, merged.last_active, merged_at)
        canonical.updated_at = merged_at
        canonical.evidence = canonical.evidence + merged.evidence
        canonical.payload = self._merge_event_payload(canonical.payload, merged.payload, merged.id, merged_at)
        self.store.update_event(canonical)
        self.store.relink_event_references(
            source_event_id=merged.id,
            target_event_id=canonical.id,
            timestamp=merged_at,
        )

        merged.status = "merged"
        merged.valid_to = merged_at
        merged.updated_at = merged_at
        merged.payload = self._merge_event_payload(merged.payload, {}, canonical.id, merged_at, source_event_id=merged.id)
        self.store.update_event(merged)

        self.store.save_event_merge_trace(
            source_event_id=merged.id,
            target_event_id=canonical.id,
            merge_reason=merge_reason,
            similarity_score=similarity_score,
            merged_at=merged_at,
            strategy_version=self.config.event_merge_trace_strategy_version,
        )
        self._append_event_merge_trace_log(
            merged_at=merged_at,
            source_event_id=merged.id,
            target_event_id=canonical.id,
            merge_reason=merge_reason,
            similarity_score=similarity_score,
        )

    def _merge_event_payload(
        self,
        payload: dict[str, Any],
        incoming_payload: dict[str, Any],
        target_event_id: str,
        merged_at: int,
        source_event_id: Optional[str] = None,
    ) -> dict[str, Any]:
        base = dict(payload or {})
        if incoming_payload:
            base.setdefault("merge_inputs", []).append(incoming_payload)
        merge_trace = base.setdefault("merge_trace", [])
        trace_entry = {
            "target_event_id": target_event_id,
            # `merged_at` records when offline consolidation decided the merge.
            "merged_at": merged_at,
        }
        source_id = source_event_id or base.get("source_event_id")
        if source_id:
            # `source_event_id` keeps the archived source event directly traceable.
            trace_entry["source_event_id"] = source_id
        merge_trace.append(trace_entry)
        return base

    def _build_context_draft(self, event: Event) -> Context:
        timestamp = event.last_active or event.timestamp or int(time.time())
        slots = self._extract_context_slots(event)
        subtype = self._infer_context_subtype(event, slots)
        summary = self._build_context_summary(subtype, slots)
        return Context(
            id="",
            context_type="situation",
            subtype=subtype,
            summary=summary,
            structured_slots=slots,
            confidence=max(0.4, event.confidence),
            support_count=1,
            created_at=timestamp,
            updated_at=timestamp,
            valid_from=timestamp,
            last_seen_at=timestamp,
            status="active",
        )

    def _context_similarity(self, a: Context, b: Context) -> float:
        slots_a = a.structured_slots if isinstance(a.structured_slots, dict) else {}
        slots_b = b.structured_slots if isinstance(b.structured_slots, dict) else {}
        core_slot_sim = self._slot_group_similarity(
            slots_a,
            slots_b,
            self._core_context_slot_keys(),
        )
        aux_slot_sim = self._slot_group_similarity(
            slots_a,
            slots_b,
            self._aux_context_slot_keys(),
        )
        slot_sim = (
            self.config.context_core_slot_weight * core_slot_sim
            + self.config.context_aux_slot_weight * aux_slot_sim
        )
        type_sim = 1.0 if a.context_type == b.context_type else 0.0
        subtype_sim = 1.0 if a.subtype == b.subtype else 0.0
        return 0.20 * type_sim + 0.25 * subtype_sim + 0.55 * slot_sim

    def _context_merge_score(self, a: Context, b: Context) -> float:
        base = self._context_similarity(a, b)
        containment = self._context_slot_containment_ratio(a, b)
        return 0.7 * base + 0.3 * containment

    def _context_slot_containment_ratio(self, a: Context, b: Context) -> float:
        slots_a = a.structured_slots if isinstance(a.structured_slots, dict) else {}
        slots_b = b.structured_slots if isinstance(b.structured_slots, dict) else {}
        values_a = {
            key: value for key, value in slots_a.items()
            if value not in (None, "", [], {})
        }
        values_b = {
            key: value for key, value in slots_b.items()
            if value not in (None, "", [], {})
        }
        if not values_a or not values_b:
            return 0.0

        def contains(smaller: dict[str, Any], larger: dict[str, Any]) -> float:
            if not smaller:
                return 0.0
            matched = 0
            for key, value in smaller.items():
                if key in larger and self._value_overlap(value, larger.get(key)):
                    matched += 1
            return matched / len(smaller)

        return max(contains(values_a, values_b), contains(values_b, values_a))

    def _pick_canonical_context(self, left: Context, right: Context) -> tuple[Context, Context]:
        def rank_key(context: Context) -> tuple[int, float, int, int]:
            slot_count = len(context.structured_slots) if isinstance(context.structured_slots, dict) else 0
            return (
                int(context.support_count or 1),
                slot_count,
                int(context.last_seen_at or context.updated_at or 0),
                int(context.created_at or 0),
            )

        if rank_key(left) >= rank_key(right):
            return left, right
        return right, left

    def _context_conflict_ratio(self, a: Context, b: Context) -> float:
        slots_a = a.structured_slots if isinstance(a.structured_slots, dict) else {}
        slots_b = b.structured_slots if isinstance(b.structured_slots, dict) else {}
        overlap_keys = set(slots_a.keys()) & set(slots_b.keys())
        overlap = 0
        conflicts = 0
        for key in overlap_keys:
            va = slots_a.get(key)
            vb = slots_b.get(key)
            if va in (None, "", [], {}) or vb in (None, "", [], {}):
                continue
            overlap += 1
            if not self._value_overlap(va, vb):
                conflicts += 1
        return (conflicts / overlap) if overlap else 0.0

    def _infer_context_subtype(self, event: Event, slots: dict[str, Any]) -> str:
        for key in ("scene", "task_stage", "goal_hint", "constraint_hint", "geo_context", "digital_context"):
            value = str(slots.get(key, "") or "").strip()
            if value:
                return value[:48]
        return event.event_type or "generic"

    def _extract_context_slots(self, event: Event) -> dict[str, Any]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        location = event.location if isinstance(event.location, dict) else {}
        time_range = event.time_range if isinstance(event.time_range, dict) else {}
        payload_context = payload.get("context", {}) if isinstance(payload.get("context", {}), dict) else {}
        participants = self._participant_labels(event.participants)
        scene_hint = self._infer_scene_hint(event)
        scene = str(
            payload.get("scene")
            or location.get("scene")
            or payload_context.get("scene", "")
            or payload_context.get("geo_context", "")
            or payload_context.get("digital_context", "")
            or scene_hint
            or event.event_type
            or "generic"
        )
        slots = {
            "scene": scene,
            "geo_context": str(location.get("geo_context") or payload_context.get("geo_context", "") or ""),
            "digital_context": str(location.get("digital_context") or payload_context.get("digital_context", "") or ""),
            "time_bucket": str(time_range.get("display_time_bucket") or payload.get("time_bucket", "") or ""),
            "task_stage": str(payload.get("task_stage", "") or ""),
            "goal_hint": str(payload.get("goal_hint") or payload.get("goal") or scene_hint),
            "constraint_hint": str(payload.get("constraint_hint") or payload.get("constraint") or ""),
            "participants": participants,
            "device": self._collect_slot_values(payload, location, key="device"),
            "app": self._collect_slot_values(payload, location, key="app"),
            "place": self._collect_slot_values(payload, location, key="place"),
            # Action is kept only as low-weight metadata for compatibility, not as subtype driver.
            "action_hint": str(event.action or payload.get("action", "") or ""),
            "event_type": str(event.event_type or ""),
        }
        return {key: value for key, value in slots.items() if value not in (None, "", [], {})}

    def _build_context_summary(self, subtype: str, slots: dict[str, Any]) -> str:
        summary_parts = [
            str(slots.get("scene", "") or ""),
            str(slots.get("geo_context", "") or ""),
            str(slots.get("digital_context", "") or ""),
            str(slots.get("time_bucket", "") or ""),
            str(slots.get("task_stage", "") or ""),
        ]
        summary = " / ".join(part for part in summary_parts if part)
        if not summary:
            summary = subtype or "generic situation"
        return f"context:{summary}"[:180]

    def _infer_scene_hint(self, event: Event) -> str:
        text = f"{event.summary} {event.action} {safe_json_dumps(event.payload)}".lower()
        keyword_map = {
            "会议": "会议场景",
            "开会": "会议场景",
            "导航": "导航场景",
            "停车": "停车场景",
            "充电": "充电场景",
            "健身": "健身出行",
            "影院": "观影场景",
            "勿扰": "勿扰场景",
            "离车": "离车场景",
            "疲劳": "疲劳干预",
            "儿童": "儿童安抚",
        }
        for keyword, label in keyword_map.items():
            if keyword in text:
                return label
        return ""

    def _core_context_slot_keys(self) -> list[str]:
        return [
            "scene",
            "geo_context",
            "digital_context",
            "time_bucket",
            "task_stage",
            "goal_hint",
            "constraint_hint",
        ]

    def _aux_context_slot_keys(self) -> list[str]:
        return ["participants", "device", "app", "place", "action_hint", "event_type"]

    def _slot_group_similarity(
        self,
        slots_a: dict[str, Any],
        slots_b: dict[str, Any],
        keys: list[str],
    ) -> float:
        same = 0
        total = 0
        for key in keys:
            va = slots_a.get(key)
            vb = slots_b.get(key)
            if va in (None, "", [], {}) and vb in (None, "", [], {}):
                continue
            total += 1
            if self._value_overlap(va, vb):
                same += 1
        return (same / total) if total else 0.0

    def _participant_labels(self, participants: list[Any]) -> list[str]:
        if not isinstance(participants, list):
            return []
        labels = []
        for item in participants:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip()
            seat = str(item.get("seat", "") or "").strip()
            label = "/".join([part for part in [role, seat] if part])
            if label:
                labels.append(label)
        return sorted(set(labels))

    def _collect_slot_values(self, *sources: dict[str, Any], key: str) -> list[str]:
        values: list[str] = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            raw = source.get(key)
            if isinstance(raw, list):
                values.extend(str(item).strip() for item in raw if str(item).strip())
            elif raw not in (None, ""):
                values.append(str(raw).strip())
        return sorted(set(value for value in values if value))

    def _lexical_similarity(self, left: str, right: str) -> float:
        left_tokens = self._tokenize_text(left)
        right_tokens = self._tokenize_text(right)
        if not left_tokens and not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def _tokenize_text(self, text: str) -> set[str]:
        raw = str(text or "").lower()
        tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]+", raw))
        compact = re.sub(r"\s+", "", raw)
        if len(compact) >= 2:
            tokens.update(compact[idx: idx + 2] for idx in range(len(compact) - 1))
        elif compact:
            tokens.add(compact)
        return {token for token in tokens if token}

    def _context_overlap_for_events(self, event_a_id: str, event_b_id: str) -> float:
        left = {context.id for context in self.store.get_event_contexts(event_a_id)}
        right = {context.id for context in self.store.get_event_contexts(event_b_id)}
        if not left and not right:
            return 0.0
        return len(left & right) / len(left | right)

    def _pattern_overlap_for_events(self, event_a_id: str, event_b_id: str) -> float:
        left = {pattern.id for pattern in self.store.get_event_patterns(event_a_id)}
        right = {pattern.id for pattern in self.store.get_event_patterns(event_b_id)}
        if not left and not right:
            return 0.0
        return len(left & right) / len(left | right)

    def _payload_similarity(self, event_a: Event, event_b: Event) -> float:
        participant_similarity = self._value_similarity(
            self._participant_labels(event_a.participants),
            self._participant_labels(event_b.participants),
        )
        location_similarity = self._value_similarity(
            self._extract_context_slots(event_a).get("place", []),
            self._extract_context_slots(event_b).get("place", []),
        )
        action_similarity = 1.0 if event_a.action and event_a.action == event_b.action else 0.0
        causality_similarity = 1.0 if event_a.causality and event_a.causality == event_b.causality else 0.0
        return 0.35 * participant_similarity + 0.30 * location_similarity + 0.20 * action_similarity + 0.15 * causality_similarity

    def _time_similarity(self, event_a: Event, event_b: Event, now: int) -> float:
        ts_a = event_a.last_active or event_a.timestamp or now
        ts_b = event_b.last_active or event_b.timestamp or now
        diff = abs(ts_a - ts_b)
        window = max(1, self.config.event_consolidation_window_seconds)
        return math.exp(-diff / window)

    def _value_similarity(self, left: Any, right: Any) -> float:
        left_set = self._as_value_set(left)
        right_set = self._as_value_set(right)
        if not left_set and not right_set:
            return 0.0
        return len(left_set & right_set) / len(left_set | right_set)

    def _as_value_set(self, value: Any) -> set[str]:
        if isinstance(value, list):
            return {str(item).strip() for item in value if str(item).strip()}
        if isinstance(value, dict):
            return {f"{key}:{item}".strip() for key, item in value.items() if str(item).strip()}
        if value in (None, ""):
            return set()
        return {str(value).strip()}

    def _value_overlap(self, va: Any, vb: Any) -> bool:
        if isinstance(va, list) or isinstance(vb, list):
            sa = set(str(x) for x in (va if isinstance(va, list) else [va]) if str(x))
            sb = set(str(x) for x in (vb if isinstance(vb, list) else [vb]) if str(x))
            return bool(sa & sb)
        return str(va).strip() == str(vb).strip()

    def _merge_slots(self, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        merged = dict(a)
        for key, value in b.items():
            if value not in (None, "", [], {}):
                merged[key] = value
        return merged

    def merge_events(
        self,
        canonical_event_id: str,
        merged_event_id: str,
        merged_at: Optional[int] = None,
        similarity_score: float = 1.0,
        merge_reason: str = "manual_merge",
    ) -> dict[str, Any]:
        canonical = self.store.get_event(canonical_event_id)
        merged = self.store.get_event(merged_event_id)
        if canonical is None:
            raise ValueError(f"Canonical event not found: {canonical_event_id}")
        if merged is None:
            raise ValueError(f"Merged event not found: {merged_event_id}")
        if canonical.id == merged.id:
            raise ValueError("Cannot merge the same event")
        ts = int(merged_at or time.time())
        self._merge_event_pair(
            canonical=canonical,
            merged=merged,
            similarity_score=similarity_score,
            merge_reason=merge_reason,
            merged_at=ts,
        )
        return {
            "canonical_event_id": canonical.id,
            "merged_event_id": merged.id,
            "merged_at": ts,
            "similarity_score": float(similarity_score),
            "merge_reason": merge_reason,
        }

    def merge_contexts(
        self,
        canonical_context_id: str,
        merged_context_id: str,
        merged_at: Optional[int] = None,
    ) -> dict[str, Any]:
        canonical = self.store.get_context(canonical_context_id)
        merged = self.store.get_context(merged_context_id)
        if canonical is None:
            raise ValueError(f"Canonical context not found: {canonical_context_id}")
        if merged is None:
            raise ValueError(f"Merged context not found: {merged_context_id}")
        if canonical.id == merged.id:
            raise ValueError("Cannot merge the same context")
        ts = int(merged_at or time.time())
        canonical.support_count += max(1, merged.support_count)
        canonical.confidence = min(1.0, max(canonical.confidence, merged.confidence) + 0.03)
        canonical.updated_at = ts
        canonical.last_seen_at = max(canonical.last_seen_at, merged.last_seen_at, ts)
        canonical.structured_slots = self._merge_slots(canonical.structured_slots, merged.structured_slots)
        canonical.summary = canonical.summary or merged.summary
        self.store.update_context(canonical)
        moved_links = self.store.relink_context_edges(
            source_context_id=merged.id,
            target_context_id=canonical.id,
            timestamp=ts,
        )
        merged.status = "merged"
        merged.valid_to = ts
        merged.updated_at = ts
        self.store.update_context(merged)
        return {
            "canonical_context_id": canonical.id,
            "merged_context_id": merged.id,
            "merged_at": ts,
            "moved_links": moved_links,
        }

    def _apply_event_merge_plan(self, plan: dict[str, Any], merged_at: int) -> bool:
        canonical_event_id = str(plan.get("canonical_event_id", "") or "").strip()
        merged_event_id = str(plan.get("merged_event_id", "") or "").strip()
        if not canonical_event_id or not merged_event_id or canonical_event_id == merged_event_id:
            return False

        canonical = self.store.get_event(canonical_event_id)
        merged = self.store.get_event(merged_event_id)
        if canonical is None or merged is None:
            return False
        if canonical.status in {"merged", "archived"} or merged.status in {"merged", "archived"}:
            return False

        self._merge_event_pair(
            canonical=canonical,
            merged=merged,
            similarity_score=float(plan.get("score", 1.0) or 1.0),
            merge_reason=str(plan.get("reason", "auto_merge") or "auto_merge"),
            merged_at=int(merged_at),
        )
        return True

    def _apply_context_merge_plan(self, plan: dict[str, Any], merged_at: int) -> bool:
        canonical_context_id = str(plan.get("canonical_context_id", "") or "").strip()
        merged_context_id = str(plan.get("merged_context_id", "") or "").strip()
        if not canonical_context_id or not merged_context_id or canonical_context_id == merged_context_id:
            return False

        canonical = self.store.get_context(canonical_context_id)
        merged = self.store.get_context(merged_context_id)
        if canonical is None or merged is None:
            return False
        if canonical.status != "active" or merged.status != "active":
            return False

        self.merge_contexts(
            canonical_context_id=canonical_context_id,
            merged_context_id=merged_context_id,
            merged_at=int(merged_at),
        )
        return True

    def _resolve_merge_strategy(self, strategy: str) -> str:
        requested = (strategy or self.config.merge_decision_strategy or "auto").strip().lower()
        if requested not in {"auto", "heuristic", "llm"}:
            return "heuristic"
        if requested == "heuristic":
            return "heuristic"
        if requested == "llm":
            return "llm" if self._llm_merge_available() else "heuristic"
        return "llm" if self._llm_merge_available() else "heuristic"

    def _llm_merge_available(self) -> bool:
        api_key = str(self.config.llm_api_key or "").strip()
        return bool(
            not self.config.offline_mode
            and dashscope is not None
            and Generation is not None
            and api_key
            and api_key not in {"YOUR_API_KEY", "sk-xxx"}
        )

    def _llm_relation_available(self) -> bool:
        return self._llm_merge_available()

    def _llm_event_merge_decision(
        self,
        left: Event,
        right: Event,
        heuristic_score: float,
        heuristic_reason: str,
    ) -> Optional[dict[str, Any]]:
        prompt = {
            "task": "Decide whether two memory events should be merged into one canonical event.",
            "rules": [
                "Only merge if they describe the same user memory or a direct duplicate.",
                "If one event is more complete or has stronger support, choose it as canonical.",
                "Return strict JSON only.",
            ],
            "heuristic_score": round(float(heuristic_score), 4),
            "heuristic_reason": heuristic_reason,
            "left": self._event_prompt_payload(left),
            "right": self._event_prompt_payload(right),
            "output_schema": {
                "should_merge": True,
                "canonical_id": left.id,
                "reason": "short_reason",
                "confidence": 0.0,
            },
        }
        return self._call_merge_llm(prompt)

    def _llm_context_merge_decision(
        self,
        left: Context,
        right: Context,
        heuristic_score: float,
    ) -> Optional[dict[str, Any]]:
        prompt = {
            "task": "Decide whether two context nodes should be merged into one canonical context.",
            "rules": [
                "Only merge if they describe the same situation or one is a refinement of the other.",
                "Prefer the more informative or more supported context as canonical.",
                "Return strict JSON only.",
            ],
            "heuristic_score": round(float(heuristic_score), 4),
            "left": self._context_prompt_payload(left),
            "right": self._context_prompt_payload(right),
            "output_schema": {
                "should_merge": True,
                "canonical_id": left.id,
                "reason": "short_reason",
                "confidence": 0.0,
            },
        }
        return self._call_merge_llm(prompt)

    def _llm_event_relation_decision(
        self,
        left: Event,
        right: Event,
    ) -> Optional[dict[str, Any]]:
        if not self._llm_relation_available():
            return None
        prompt = {
            "task": "Decide whether two memory events have an explicit semantic relation worth storing as a graph edge.",
            "rules": [
                "Only link if there is a meaningful semantic relation beyond generic temporal adjacency.",
                "Good relation types include causes, enables, blocks, follows_from, refines, contradicts, co_occurs_with, same_goal, same_topic.",
                "Use direction when the relation is directional. Neutral relations can point from earlier event to later event.",
                "Return strict JSON only.",
            ],
            "left": self._event_prompt_payload(left),
            "right": self._event_prompt_payload(right),
            "shared_session_key": self._event_session_key(left) if self._event_session_key(left) == self._event_session_key(right) else "",
            "output_schema": {
                "should_link": True,
                "relation_type": "same_topic",
                "from_id": left.id,
                "to_id": right.id,
                "reason": "short_reason",
                "confidence": 0.0,
            },
        }
        return self._call_relation_llm(prompt)

    def _call_merge_llm(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not self._llm_merge_available():
            return None
        try:
            dashscope.base_http_api_url = self.config.llm_base_url
            dashscope.api_key = self.config.llm_api_key
            resp = Generation.call(
                api_key=self.config.llm_api_key,
                model=self.config.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a memory-graph merge judge. "
                            "Return a compact JSON object only with keys: "
                            "should_merge, canonical_id, reason, confidence."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                result_format="message",
                enable_thinking=False,
            )
            if getattr(resp, "status_code", None) != 200:
                return None
            content = resp.output.choices[0].message.content
            data = robust_json_loads(content, None)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _call_relation_llm(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not self._llm_relation_available():
            return None
        try:
            dashscope.base_http_api_url = self.config.llm_base_url
            dashscope.api_key = self.config.llm_api_key
            resp = Generation.call(
                api_key=self.config.llm_api_key,
                model=self.config.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a semantic event-relation judge for a memory graph. "
                            "Return a compact JSON object only with keys: "
                            "should_link, relation_type, from_id, to_id, reason, confidence."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                result_format="message",
                enable_thinking=False,
            )
            if getattr(resp, "status_code", None) != 200:
                return None
            content = resp.output.choices[0].message.content
            data = robust_json_loads(content, None)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _event_prompt_payload(self, event: Event) -> dict[str, Any]:
        return {
            "id": event.id,
            "summary": event.summary,
            "event_type": event.event_type,
            "action": event.action,
            "timestamp": event.timestamp,
            "last_active": event.last_active,
            "participants": event.participants,
            "location": event.location,
            "payload": event.payload,
            "confidence": event.confidence,
            "support_count": event.support_count,
            "context_ids": [context.id for context in self.store.get_event_contexts(event.id)],
            "pattern_ids": [pattern.id for pattern in self.store.get_event_patterns(event.id)],
        }

    def _context_prompt_payload(self, context: Context) -> dict[str, Any]:
        return {
            "id": context.id,
            "context_type": context.context_type,
            "subtype": context.subtype,
            "summary": context.summary,
            "structured_slots": context.structured_slots,
            "confidence": context.confidence,
            "support_count": context.support_count,
            "last_seen_at": context.last_seen_at,
        }

    def _new_context_id(self, context: Context) -> str:
        signature = f"{context.context_type}|{context.subtype}|{safe_json_dumps(context.structured_slots)}"
        return f"ctx_{hash_summary(signature)[:20]}"

    def _ensure_append_first_event_id(self, event: Event) -> str:
        if not self.config.append_first_mode:
            return event.id or hash_summary(event.summary)
        base = event.id or hash_summary(event.summary or uuid.uuid4().hex)
        timestamp = event.timestamp or event.last_active or int(time.time())
        return f"{base[:20]}_{timestamp}_{uuid.uuid4().hex[:6]}"

    def _next_score(self, prev: Event, cur: Event) -> float:
        prev_ts = prev.last_active or prev.timestamp or 0
        cur_ts = cur.last_active or cur.timestamp or 0
        dt = max(0, cur_ts - prev_ts)
        time_factor = math.exp(-dt / max(1, self.config.next_recent_window_seconds))
        action_factor = 1.0 if prev.action and cur.action and prev.action == cur.action else 0.4
        prev_roles = {str(p.get("role", "")) for p in prev.participants if isinstance(p, dict)}
        cur_roles = {str(p.get("role", "")) for p in cur.participants if isinstance(p, dict)}
        role_overlap = (len(prev_roles & cur_roles) / len(prev_roles | cur_roles)) if (prev_roles or cur_roles) else 0.5
        return 0.5 * time_factor + 0.2 * action_factor + 0.3 * role_overlap

    def _infer_pattern_type(self, event: Event) -> str:
        if event.action:
            text = event.action.lower()
            if "喜欢" in text or "偏好" in text or "like" in text or "prefer" in text:
                return "preference"
            if "失败" in text or "error" in text or "故障" in text:
                return "failure_mode"
            if "成功" in text or "完成" in text:
                return "success_mode"
        return "experience"

    def _event_features(self, event: Event) -> dict[str, Any]:
        geo = ""
        digital = ""
        if isinstance(event.location, dict):
            geo = str(event.location.get("geo_context", "") or "")
            digital = str(event.location.get("digital_context", "") or "")
        bucket = ""
        if isinstance(event.time_range, dict):
            bucket = str(event.time_range.get("display_time_bucket", "") or "")
        participants = []
        if isinstance(event.participants, list):
            participants = [str(p.get("role", "")) for p in event.participants if isinstance(p, dict)]
        return {
            "action": event.action or "",
            "event_type": event.event_type or "generic",
            "geo_context": geo,
            "digital_context": digital,
            "time_bucket": bucket,
            "participants": sorted([p for p in participants if p]),
        }

    def _merge_pattern_features(self, base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base or {})
        for key, value in (incoming or {}).items():
            if isinstance(value, list):
                existing = merged.get(key, [])
                if not isinstance(existing, list):
                    existing = [existing] if existing else []
                merged[key] = sorted(set(str(x) for x in existing + value if str(x)))
            else:
                if value not in (None, ""):
                    merged[key] = value
        return merged

    def _pattern_similarity(self, a: dict[str, Any], b: dict[str, Any]) -> float:
        if not a and not b:
            return 0.0
        keys = set(a.keys()) | set(b.keys())
        if not keys:
            return 0.0
        score = 0.0
        for key in keys:
            va = a.get(key)
            vb = b.get(key)
            if va in (None, "", [], {}) and vb in (None, "", [], {}):
                continue
            if self._value_overlap(va, vb):
                score += 1.0
        return score / len(keys)

    def _score_event_for_retrieval(
        self,
        query: str,
        query_entities: list[str],
        event_data: dict[str, Any],
    ) -> dict[str, Any]:
        event_id = event_data.get("event_id", event_data.get("id", ""))
        summary = str(event_data.get("summary", "") or "")
        now = int(time.time())
        last_active = int(event_data.get("last_active", event_data.get("t_valid", 0)) or 0)
        status = str(event_data.get("status", "active") or "active")
        support_count = int(event_data.get("support_count", event_data.get("c_valid", 1)) or 1)

        # Event similarity via lexical overlap (low-cost endpoint-safe).
        event_similarity = max(
            self._lexical_similarity(query, summary),
            1.0 if query and query.lower() in summary.lower() else 0.0,
        )

        contexts = self.store.get_event_contexts(event_id) if event_id else []
        patterns = self.store.get_event_patterns(event_id) if event_id else []

        context_match = self._context_query_match(contexts, query_entities, query)
        pattern_similarity = self._pattern_query_match(patterns, query_entities, query)

        age = max(0, now - last_active)
        recency = math.exp(-DECAY_RATE * age)

        validity = 1.0
        if status in {"deprecated", "archived", "merged"}:
            validity = 0.25
        if status in {"weakened"}:
            validity = 0.5

        support_norm = min(1.0, math.log1p(support_count) / math.log1p(20))
        drift_penalty = 0.0
        if patterns:
            drift_penalty = max(p.drift_score for p in patterns if hasattr(p, "drift_score"))
        decay_penalty = 1.0 - recency

        evolution_score = (
            self.config.retrieval_weight_event_sim * event_similarity
            + self.config.retrieval_weight_context * context_match
            + self.config.retrieval_weight_pattern * pattern_similarity
            + self.config.retrieval_weight_recency * recency
            + self.config.retrieval_weight_validity * validity
            + self.config.retrieval_weight_support * support_norm
            - 0.10 * drift_penalty
            - 0.06 * decay_penalty
        )

        return {
            "event_id": event_id,
            "summary": summary,
            "evolution_score": evolution_score,
            "event_similarity": event_similarity,
            "context_match": context_match,
            "pattern_similarity": pattern_similarity,
            "recency_factor": recency,
            "validity": validity,
            "support_norm": support_norm,
            "decay_penalty": decay_penalty,
            "drift_penalty": drift_penalty,
            "compressed_contexts": [c.summary for c in contexts[:2]],
            "compressed_patterns": [p.summary for p in patterns[:2]],
        }

    def _context_query_match(self, contexts: list[Context], query_entities: list[str], query: str) -> float:
        if not contexts:
            return 0.0
        q = query.lower()
        best = 0.0
        entity_set = {e.lower() for e in query_entities}
        for c in contexts:
            text = (c.summary + " " + safe_json_dumps(c.structured_slots)).lower()
            hits = sum(1 for e in entity_set if e and e in text)
            lexical = 1.0 if q and q in text else 0.0
            score = min(1.0, 0.2 * hits + 0.5 * lexical + 0.3 * c.confidence)
            if c.status != "active":
                score *= 0.5
            best = max(best, score)
        return best

    def _pattern_query_match(self, patterns: list[Pattern], query_entities: list[str], query: str) -> float:
        if not patterns:
            return 0.0
        q = query.lower()
        entity_set = {e.lower() for e in query_entities}
        best = 0.0
        for p in patterns:
            text = (p.summary + " " + safe_json_dumps(p.prototype_features)).lower()
            hits = sum(1 for e in entity_set if e and e in text)
            lexical = 1.0 if q and q in text else 0.0
            score = min(1.0, 0.15 * hits + 0.55 * lexical + 0.30 * p.confidence)
            if p.status in {"deprecated", "merged"}:
                score *= 0.4
            if p.status == "weakened":
                score *= 0.7
            best = max(best, score)
        return best

    def _list_all_contexts(self, only_active: bool) -> list[Context]:
        where = "WHERE c.status = 'active'" if only_active else ""
        resp = self.store.conn.execute(
            f"""
            MATCH (c:Context)
            {where}
            RETURN c.id, c.context_type, c.subtype, c.summary, c.structured_slots,
                   c.confidence, c.support_count, c.created_at, c.updated_at,
                   c.valid_from, c.valid_to, c.last_seen_at, c.status, c.embedding
            """
        )
        cols = [
            "id", "context_type", "subtype", "summary", "structured_slots",
            "confidence", "support_count", "created_at", "updated_at",
            "valid_from", "valid_to", "last_seen_at", "status", "embedding",
        ]
        result = []
        while resp.has_next():
            result.append(Context.from_db_row(list(resp.get_next()), cols))
        return result

    def _list_all_patterns(self, only_active: bool) -> list[Pattern]:
        where = "WHERE p.status = 'active'" if only_active else ""
        resp = self.store.conn.execute(
            f"""
            MATCH (p:Pattern)
            {where}
            RETURN p.id, p.pattern_type, p.summary, p.prototype_features,
                   p.support_count, p.confidence, p.stability_score, p.drift_score,
                   p.created_at, p.updated_at, p.valid_from, p.valid_to,
                   p.last_seen_at, p.status, p.embedding
            """
        )
        cols = [
            "id", "pattern_type", "summary", "prototype_features",
            "support_count", "confidence", "stability_score", "drift_score",
            "created_at", "updated_at", "valid_from", "valid_to",
            "last_seen_at", "status", "embedding",
        ]
        result = []
        while resp.has_next():
            result.append(Pattern.from_db_row(list(resp.get_next()), cols))
        return result

    def _archive_stale_events(self, now: int) -> int:
        stale_before = now - self.config.archive_event_seconds
        resp = self.store.conn.execute(
            """
            MATCH (e:Event)
            WHERE e.last_active < $stale_before AND (e.support_count IS NULL OR e.support_count <= 1)
            RETURN e.id
            """,
            {"stale_before": stale_before},
        )
        ids = []
        while resp.has_next():
            ids.append(resp.get_next()[0])
        for event_id in ids:
            self.store.archive_event(event_id, now)
        return len(ids)

    def _count_archivable_events(self, now: int) -> int:
        stale_before = now - self.config.archive_event_seconds
        resp = self.store.conn.execute(
            """
            MATCH (e:Event)
            WHERE e.last_active < $stale_before AND (e.support_count IS NULL OR e.support_count <= 1)
            RETURN count(e)
            """,
            {"stale_before": stale_before},
        )
        return int(resp.get_next()[0]) if resp.has_next() else 0

    def _append_consolidation_log(self, now: int, report: dict[str, int]) -> None:
        path = self.config.consolidation_log_path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {"timestamp": now, "report": report}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _append_event_merge_trace_log(
        self,
        merged_at: int,
        source_event_id: str,
        target_event_id: str,
        merge_reason: str,
        similarity_score: float,
    ) -> None:
        path = self.config.event_merge_trace_log_path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {
            "merged_at": merged_at,
            "source_event_id": source_event_id,
            "target_event_id": target_event_id,
            "merge_reason": merge_reason,
            "similarity_score": similarity_score,
            "strategy_version": self.config.event_merge_trace_strategy_version,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
