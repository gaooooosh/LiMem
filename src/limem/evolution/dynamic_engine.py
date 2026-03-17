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
import time
import uuid

from ..config import (
    APPEND_FIRST_MODE,
    ARCHIVE_EVENT_SECONDS,
    CONSOLIDATION_LOG_PATH,
    CONSOLIDATION_MIN_INTERVAL_SECONDS,
    CONTEXT_CANDIDATE_LIMIT,
    CONTEXT_CONFLICT_THRESHOLD,
    CONTEXT_REUSE_THRESHOLD,
    DECAY_RATE,
    DECAY_STEP,
    ENABLE_AUTO_CONSOLIDATION,
    NEXT_MAX_PREDECESSORS,
    NEXT_MIN_SCORE,
    NEXT_RECENT_WINDOW_SECONDS,
    PATTERN_ASSIGN_THRESHOLD,
    PATTERN_CANDIDATE_LIMIT,
    PATTERN_DRIFT_THRESHOLD,
    PATTERN_MERGE_THRESHOLD,
    PATTERN_SPLIT_DRIFT_THRESHOLD,
    REINFORCEMENT_STEP,
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
from ..utils import hash_summary, safe_json_dumps, safe_json_loads


@dataclass
class DynamicEvolutionConfig:
    append_first_mode: bool = APPEND_FIRST_MODE
    context_reuse_threshold: float = CONTEXT_REUSE_THRESHOLD
    context_conflict_threshold: float = CONTEXT_CONFLICT_THRESHOLD
    context_candidate_limit: int = CONTEXT_CANDIDATE_LIMIT

    next_recent_window_seconds: int = NEXT_RECENT_WINDOW_SECONDS
    next_max_predecessors: int = NEXT_MAX_PREDECESSORS
    next_min_score: float = NEXT_MIN_SCORE

    pattern_assign_threshold: float = PATTERN_ASSIGN_THRESHOLD
    pattern_drift_threshold: float = PATTERN_DRIFT_THRESHOLD
    pattern_split_drift_threshold: float = PATTERN_SPLIT_DRIFT_THRESHOLD
    pattern_merge_threshold: float = PATTERN_MERGE_THRESHOLD
    pattern_candidate_limit: int = PATTERN_CANDIDATE_LIMIT

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

    enable_auto_consolidation: bool = ENABLE_AUTO_CONSOLIDATION
    consolidation_min_interval_seconds: int = CONSOLIDATION_MIN_INTERVAL_SECONDS
    weak_edge_prune_threshold: float = WEAK_EDGE_PRUNE_THRESHOLD
    consolidation_log_path: str = CONSOLIDATION_LOG_PATH


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
            return {"event_count": 0, "context_links": 0, "next_links": 0, "pattern_links": 0}

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

        next_links = self.link_next_for_new_events(new_events, recent_candidates=None)

        if self.config.enable_auto_consolidation:
            ts = int(time.time())
            if ts - self._last_consolidation_at >= self.config.consolidation_min_interval_seconds:
                self.run_consolidation(current_time=ts)

        return {
            "event_count": len(new_events),
            "context_links": in_links,
            "next_links": next_links,
            "pattern_links": pattern_links,
        }

    def evolve_existing_events(self, events: list[Event]) -> dict[str, int]:
        """Apply local dynamic updates for already-persisted events."""
        if not events:
            return {"context_links": 0, "next_links": 0, "pattern_links": 0}

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

        next_links = self.link_next_for_new_events(events, recent_candidates=None)
        if self.config.enable_auto_consolidation:
            ts = int(time.time())
            if ts - self._last_consolidation_at >= self.config.consolidation_min_interval_seconds:
                self.run_consolidation(current_time=ts)

        return {
            "context_links": context_links,
            "next_links": next_links,
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
    # Algorithm 3: Local NEXT Evolution
    # -------------------------------------------------------------------------
    def link_next_for_new_events(
        self,
        new_events: list[Event],
        recent_candidates: Optional[list[Event]],
    ) -> int:
        if not new_events:
            return 0

        count = 0
        ordered = sorted(new_events, key=lambda e: e.last_active or e.timestamp)
        for idx in range(1, len(ordered)):
            prev = ordered[idx - 1]
            cur = ordered[idx]
            score = self._next_score(prev, cur)
            if score >= self.config.next_min_score:
                self.store.link_next(
                    from_event_id=prev.id,
                    to_event_id=cur.id,
                    confidence=min(1.0, 0.6 + score * 0.4),
                    score=score,
                    relation_hint="temporal",
                    timestamp=cur.last_active or cur.timestamp or int(time.time()),
                )
                count += 1

        history = recent_candidates
        if history is None:
            now = max(e.last_active or e.timestamp or 0 for e in new_events)
            history = self.store.get_recent_events(
                current_time=now,
                window_seconds=self.config.next_recent_window_seconds,
                limit=200,
            )

        new_ids = {e.id for e in new_events}
        for cur in ordered:
            scored: list[tuple[float, Event]] = []
            for cand in history:
                if cand.id in new_ids or cand.id == cur.id:
                    continue
                score = self._next_score(cand, cur)
                if score >= self.config.next_min_score:
                    scored.append((score, cand))
            scored.sort(key=lambda x: x[0], reverse=True)
            for score, pred in scored[: self.config.next_max_predecessors]:
                self.store.link_next(
                    from_event_id=pred.id,
                    to_event_id=cur.id,
                    confidence=min(1.0, 0.5 + score * 0.5),
                    score=score,
                    relation_hint="temporal_or_causal",
                    timestamp=cur.last_active or cur.timestamp or int(time.time()),
                )
                count += 1
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
            now = int(time.time())
            events = self.store.get_recent_events(now, self.config.archive_event_seconds * 2, limit=300)
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

    # -------------------------------------------------------------------------
    # Algorithm 6: Consolidation and Forgetting
    # -------------------------------------------------------------------------
    def run_consolidation(self, current_time: Optional[int] = None) -> dict[str, int]:
        now = int(current_time or time.time())
        report = {
            "merged_contexts": self.consolidate_contexts(now),
            "merged_patterns": self.consolidate_patterns(now),
            "decayed_nodes": self.decay_stale_nodes(now),
            "pruned_edges": self.prune_weak_edges(now),
            "archived_events": self._archive_stale_events(now),
        }
        self._last_consolidation_at = now
        self._append_consolidation_log(now, report)
        return report

    def consolidate_contexts(self, now: int) -> int:
        contexts = self._list_all_contexts(only_active=True)
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
                if score < max(self.config.context_reuse_threshold, 0.82):
                    continue
                a.support_count += b.support_count
                a.confidence = min(1.0, (a.confidence + b.confidence) / 2.0 + 0.05)
                a.updated_at = now
                a.last_seen_at = max(a.last_seen_at, b.last_seen_at, now)
                a.structured_slots = self._merge_slots(a.structured_slots, b.structured_slots)
                self.store.update_context(a)
                b.status = "merged"
                b.valid_to = now
                b.updated_at = now
                self.store.update_context(b)
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
        return self.store.prune_weak_next_edges(
            min_score=self.config.weak_edge_prune_threshold,
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
    def _build_context_draft(self, event: Event) -> Context:
        timestamp = event.last_active or event.timestamp or int(time.time())
        geo_context = ""
        digital_context = ""
        if isinstance(event.location, dict):
            geo_context = str(event.location.get("geo_context", "") or "")
            digital_context = str(event.location.get("digital_context", "") or "")
        time_bucket = ""
        if isinstance(event.time_range, dict):
            time_bucket = str(event.time_range.get("display_time_bucket", "") or "")
        participants = []
        if isinstance(event.participants, list):
            participants = [str(p.get("role", "")) for p in event.participants if isinstance(p, dict)]
        slots = {
            "geo_context": geo_context,
            "digital_context": digital_context,
            "time_bucket": time_bucket,
            "action": event.action,
            "event_type": event.event_type,
            "participants": [p for p in participants if p],
        }
        subtype = event.action or event.event_type or "generic"
        summary = event.summary[:180] if event.summary else f"{subtype} context"
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
        keys = set(slots_a.keys()) | set(slots_b.keys())
        if not keys:
            slot_sim = 0.0
        else:
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
            slot_sim = (same / total) if total else 0.0
        type_sim = 1.0 if a.context_type == b.context_type else 0.0
        subtype_sim = 1.0 if a.subtype == b.subtype else 0.0
        return 0.25 * type_sim + 0.35 * subtype_sim + 0.40 * slot_sim

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
        q_tokens = {t.strip().lower() for t in query.split() if t.strip()}
        s_tokens = {t.strip().lower() for t in summary.split() if t.strip()}
        event_similarity = (len(q_tokens & s_tokens) / len(q_tokens | s_tokens)) if (q_tokens or s_tokens) else 0.0

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

    def _append_consolidation_log(self, now: int, report: dict[str, int]) -> None:
        path = self.config.consolidation_log_path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {"timestamp": now, "report": report}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
