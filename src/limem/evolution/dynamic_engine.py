# -*- coding: utf-8 -*-
"""Dynamic evolution engine for long-term memory graph."""

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
    EVENT_CONSOLIDATION_TEXT_WEIGHT,
    EVENT_CONSOLIDATION_CONTEXT_WEIGHT,
    EVENT_CONSOLIDATION_THRESHOLD,
    EVENT_CONSOLIDATION_TIME_WEIGHT,
    EVENT_CONSOLIDATION_WINDOW_SECONDS,
    EVENT_MERGE_TRACE_LOG_PATH,
    EVENT_MERGE_TRACE_STRATEGY_VERSION,
    GENERATION_MODEL,
    REINFORCEMENT_STEP,
    RETRIEVAL_DEFAULT_CANDIDATE_LIMIT,
    RETRIEVAL_WEIGHT_CONTEXT,
    RETRIEVAL_WEIGHT_EVENT_SIM,
    RETRIEVAL_WEIGHT_RECENCY,
    RETRIEVAL_WEIGHT_SUPPORT,
    RETRIEVAL_WEIGHT_VALIDITY,
    STALE_SECONDS,
    WEAK_EDGE_PRUNE_THRESHOLD,
)
from ..builder.context_extractor import ContextExtractionPipeline
from ..core.context import Context, ContextDraft
from ..core.event import Event
from ..utils import hash_summary, robust_json_loads, safe_json_dumps, safe_json_loads


@dataclass
class DynamicEvolutionConfig:
    append_first_mode: bool = APPEND_FIRST_MODE
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

    reinforcement_step: float = REINFORCEMENT_STEP
    decay_step: float = DECAY_STEP
    stale_seconds: int = STALE_SECONDS
    archive_event_seconds: int = ARCHIVE_EVENT_SECONDS

    retrieval_weight_event_sim: float = RETRIEVAL_WEIGHT_EVENT_SIM
    retrieval_weight_context: float = RETRIEVAL_WEIGHT_CONTEXT
    retrieval_weight_recency: float = RETRIEVAL_WEIGHT_RECENCY
    retrieval_weight_validity: float = RETRIEVAL_WEIGHT_VALIDITY
    retrieval_weight_support: float = RETRIEVAL_WEIGHT_SUPPORT
    retrieval_default_candidate_limit: int = RETRIEVAL_DEFAULT_CANDIDATE_LIMIT

    enable_auto_consolidation: bool = ENABLE_AUTO_CONSOLIDATION
    consolidation_min_interval_seconds: int = CONSOLIDATION_MIN_INTERVAL_SECONDS
    weak_edge_prune_threshold: float = WEAK_EDGE_PRUNE_THRESHOLD
    consolidation_log_path: str = CONSOLIDATION_LOG_PATH
    event_consolidation_window_seconds: int = EVENT_CONSOLIDATION_WINDOW_SECONDS
    event_consolidation_candidate_limit: int = EVENT_CONSOLIDATION_CANDIDATE_LIMIT
    event_consolidation_threshold: float = EVENT_CONSOLIDATION_THRESHOLD
    event_consolidation_text_weight: float = EVENT_CONSOLIDATION_TEXT_WEIGHT
    event_consolidation_context_weight: float = EVENT_CONSOLIDATION_CONTEXT_WEIGHT
    event_consolidation_payload_weight: float = EVENT_CONSOLIDATION_PAYLOAD_WEIGHT
    event_consolidation_time_weight: float = EVENT_CONSOLIDATION_TIME_WEIGHT
    event_merge_trace_strategy_version: str = EVENT_MERGE_TRACE_STRATEGY_VERSION
    event_merge_trace_log_path: str = EVENT_MERGE_TRACE_LOG_PATH
    enable_event_relations: bool = ENABLE_EVENT_RELATIONS


class DynamicEvolutionEngine:
    """Incremental dynamic graph update and retrieval engine."""

    def __init__(self, store: Any, config: Optional[DynamicEvolutionConfig] = None):
        self.store = store
        self.config = config or DynamicEvolutionConfig()
        self._last_consolidation_at = 0
        self.context_extractor = ContextExtractionPipeline(
            api_key=self.config.llm_api_key,
            base_url=self.config.llm_base_url,
            generation_model=self.config.llm_model,
            offline_mode=False,
        )

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
                "next_links": 0,
            }

        new_events: list[Event] = []
        in_links = 0
        relation_links = 0
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
            resolved_contexts = self.resolve_context_pairs(event, record=record)
            in_links += self.attach_contexts_to_event(event, resolved_contexts)
        if self.config.enable_event_relations:
            relation_links += self.extract_event_event_relations(
                events=new_events,
                record=record,
            )
        if self.config.enable_auto_consolidation:
            ts = int(time.time())
            if ts - self._last_consolidation_at >= self.config.consolidation_min_interval_seconds:
                self.run_consolidation(current_time=ts)

        return {
            "event_count": len(new_events),
            "context_links": in_links,
            "next_links": 0,
            "event_relation_links": relation_links,
        }

    def evolve_existing_events(self, events: list[Event]) -> dict[str, int]:
        """Apply local dynamic updates for already-persisted events."""
        if not events:
            return {"context_links": 0, "next_links": 0, "event_relation_links": 0}

        context_links = 0
        relation_links = 0
        for event in events:
            resolved_contexts = self.resolve_context_pairs(event, record=None)
            context_links += self.attach_contexts_to_event(event, resolved_contexts)
        if self.config.enable_event_relations:
            relation_links += self.extract_event_event_relations(
                events=events,
                record=None,
            )
        if self.config.enable_auto_consolidation:
            ts = int(time.time())
            if ts - self._last_consolidation_at >= self.config.consolidation_min_interval_seconds:
                self.run_consolidation(current_time=ts)

        return {
            "context_links": context_links,
            "next_links": 0,
            "event_relation_links": relation_links,
        }

    def extract_event_event_relations(
        self,
        events: list[Event],
        record: Optional[Any] = None,
    ) -> int:
        if len(events) < 2 or not self._llm_relation_available():
            return 0

        source_text = self._extract_relation_source_text(record=record, events=events)
        if not source_text:
            source_text = " ".join(event.summary for event in events if event.summary).strip()
        if not source_text:
            return 0

        created = 0
        for idx, left in enumerate(events):
            for right in events[idx + 1:]:
                if not left or not right or left.id == right.id:
                    continue
                if left.status in {"merged", "archived"} or right.status in {"merged", "archived"}:
                    continue
                if not self._same_relation_scope(left, right):
                    continue
                payload = self._relation_prompt_payload(left=left, right=right, source_text=source_text)
                decision = self._call_relation_llm(payload)
                if not isinstance(decision, dict) or not bool(decision.get("should_link", False)):
                    continue
                relation = self._normalize_relation_decision(left=left, right=right, decision=decision)
                if relation is None:
                    continue
                self.store.upsert_event_relation(
                    from_event_id=relation["from_event_id"],
                    to_event_id=relation["to_event_id"],
                    relation_type=relation["relation_type"],
                    description=relation["description"],
                    confidence=relation["confidence"],
                    evidence_span=relation["evidence_span"],
                    source_episode_id=relation["source_episode_id"],
                    source_session_id=relation["source_session_id"],
                    timestamp=relation["timestamp"],
                )
                created += 1
        return created

    def _extract_relation_source_text(self, record: Optional[Any], events: list[Event]) -> str:
        if isinstance(record, str):
            return record.strip()
        if isinstance(record, dict):
            for key in ["episode_text", "content", "text", "raw_text"]:
                value = str(record.get(key, "") or "").strip()
                if value:
                    return value
        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            text = str(payload.get("episode_text", "") or "").strip()
            if text:
                return text
        return ""

    def _same_relation_scope(self, left: Event, right: Event) -> bool:
        left_payload = left.payload if isinstance(left.payload, dict) else {}
        right_payload = right.payload if isinstance(right.payload, dict) else {}
        left_session = str(left_payload.get("session_id", "") or "").strip()
        right_session = str(right_payload.get("session_id", "") or "").strip()
        if left_session and right_session:
            return left_session == right_session
        left_episode = str(left_payload.get("episode_id", "") or "").strip()
        right_episode = str(right_payload.get("episode_id", "") or "").strip()
        if left_episode and right_episode:
            return left_episode == right_episode
        return True

    def _relation_prompt_payload(
        self,
        left: Event,
        right: Event,
        source_text: str,
    ) -> dict[str, Any]:
        return {
            "task": (
                "Given two extracted events from the same source text, decide whether to create an event-event "
                "relation edge and provide a detailed relation description."
            ),
            "rules": [
                "Only create an edge if the relation is explicitly supported by the source text.",
                "Prefer concrete relation types such as causality, adjacency, prerequisite, enables, follows.",
                "If no relation can be grounded in text, return should_link=false.",
                "Return strict JSON only.",
            ],
            "source_text": source_text,
            "left": self._event_prompt_payload(left),
            "right": self._event_prompt_payload(right),
            "output_schema": {
                "should_link": True,
                "relation_type": "causality",
                "from_id": left.id,
                "to_id": right.id,
                "reason": "detailed relation description between the two events",
                "evidence_span": "optional quote span from source text",
                "confidence": 0.0,
            },
        }

    def _llm_relation_available(self) -> bool:
        return self._llm_merge_available()

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
                            "You are an event relation extractor for memory graph construction. "
                            "Return compact JSON only with keys: should_link, relation_type, from_id, to_id, "
                            "reason, evidence_span, confidence."
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

    def _normalize_relation_decision(
        self,
        left: Event,
        right: Event,
        decision: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        relation_type = str(decision.get("relation_type", "") or "").strip().lower()
        if not relation_type:
            return None
        allowed_ids = {left.id, right.id}
        from_event_id = str(decision.get("from_id", "") or "").strip()
        to_event_id = str(decision.get("to_id", "") or "").strip()
        if from_event_id not in allowed_ids or to_event_id not in allowed_ids or from_event_id == to_event_id:
            ordered = sorted(
                [left, right],
                key=lambda event: (event.timestamp or event.last_active or 0, event.id),
            )
            from_event_id, to_event_id = ordered[0].id, ordered[1].id
        reason = str(decision.get("reason", "") or "").strip()
        if not reason:
            return None
        confidence_raw = decision.get("confidence", 0.0)
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        evidence_span = str(decision.get("evidence_span", "") or "").strip()

        source_episode_id = ""
        source_session_id = ""
        for event in (left, right):
            payload = event.payload if isinstance(event.payload, dict) else {}
            if not source_episode_id:
                source_episode_id = str(payload.get("episode_id", "") or "").strip()
            if not source_session_id:
                source_session_id = str(payload.get("session_id", "") or "").strip()

        timestamp = max(
            int(left.last_active or left.timestamp or 0),
            int(right.last_active or right.timestamp or 0),
            int(time.time()),
        )
        return {
            "from_event_id": from_event_id,
            "to_event_id": to_event_id,
            "relation_type": relation_type,
            "description": reason,
            "confidence": confidence,
            "evidence_span": evidence_span,
            "source_episode_id": source_episode_id,
            "source_session_id": source_session_id,
            "timestamp": timestamp,
        }

    # -------------------------------------------------------------------------
    # Algorithm 2: Dynamic Context Resolution
    # -------------------------------------------------------------------------
    def extract_context_drafts(
        self,
        event: Event,
        record: Optional[Any] = None,
    ) -> list[ContextDraft]:
        return self.context_extractor.extract(record=record or event, event=event)

    def resolve_context_pairs(
        self,
        event: Event,
        record: Optional[Any] = None,
    ) -> list[tuple[Context, ContextDraft]]:
        drafts = self.extract_context_drafts(event, record=record)
        resolved: list[tuple[Context, ContextDraft]] = []
        for draft in drafts:
            resolved.append((self.resolve_context(draft, event=event), draft))
        return resolved

    def resolve_contexts(
        self,
        event: Event,
        record: Optional[Any] = None,
    ) -> list[Context]:
        return [context for context, _ in self.resolve_context_pairs(event, record=record)]

    def resolve_context(
        self,
        context_draft: ContextDraft,
        event: Optional[Event] = None,
    ) -> Context:
        match = self.match_existing_context(context_draft)
        if match is None:
            return self.create_context(context_draft)

        if self.detect_conflict(match, context_draft):
            return self.handle_context_conflict(match, context_draft)

        return self.update_context_with_evidence(match, context_draft, event)

    def match_existing_context(self, context_draft: ContextDraft) -> Optional[Context]:
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

    def update_context_with_evidence(
        self,
        context_node: Context,
        evidence: ContextDraft,
        event: Optional[Event],
    ) -> Context:
        now = max(evidence.valid_from or 0, int(time.time()))
        merged_slots = dict(context_node.structured_slots)
        for key, value in evidence.structured_slots.items():
            if value not in (None, "", [], {}):
                merged_slots[key] = value

        context_node.structured_slots = merged_slots
        if not context_node.summary or len(evidence.summary) > len(context_node.summary):
            context_node.summary = evidence.summary
        context_node.support_count += 1
        context_node.updated_at = now
        context_node.last_seen_at = now
        context_node.valid_from = min(
            int(context_node.valid_from or now),
            int(evidence.valid_from or now),
        )
        if evidence.valid_to:
            context_node.valid_to = max(int(context_node.valid_to or 0), int(evidence.valid_to))
        context_node.source_refs = self._merge_source_refs(context_node.source_refs, evidence.source_refs)
        context_node.confidence = min(
            1.0,
            (context_node.confidence * 0.8) + (evidence.confidence * 0.2) + self.config.reinforcement_step * 0.2,
        )
        context_node.status = "active"
        self.store.update_context(context_node)
        return context_node

    def create_context(self, context_draft: ContextDraft) -> Context:
        now = context_draft.valid_from or int(time.time())
        context = context_draft.to_node(
            context_id=self._new_context_id(context_draft),
            timestamp=now,
            embedding=self._maybe_embed_context(context_draft.summary),
        )
        self.store.save_context(context)
        return context

    def maybe_deprecate_context(self, context_node: Context, now: Optional[int] = None) -> Context:
        ts = int(now or time.time())
        if context_node.status == "merged":
            return context_node
        context_node.confidence = max(0.0, context_node.confidence - self.config.decay_step)
        context_node.updated_at = ts
        if context_node.confidence < 0.25:
            context_node.status = "deprecated"
            context_node.valid_to = context_node.valid_to or ts
        else:
            context_node.status = "weakened"
        self.store.update_context(context_node)
        return context_node

    def maybe_merge_contexts(self, context_a: Context, context_b: Context) -> Optional[dict[str, Any]]:
        if self._context_merge_score(context_a, context_b) < max(
            self.config.context_reuse_threshold,
            0.82,
        ):
            return None
        canonical, merged = self._pick_canonical_context(context_a, context_b)
        return self.merge_contexts(
            canonical_context_id=canonical.id,
            merged_context_id=merged.id,
            merged_at=int(time.time()),
        )

    def attach_contexts_to_event(
        self,
        event: Event,
        resolved_contexts: list[tuple[Context, ContextDraft]],
    ) -> int:
        count = 0
        ts = event.last_active or event.timestamp or int(time.time())
        for context, draft in resolved_contexts:
            source_ref = draft.source_refs[0] if draft.source_refs else {}
            self.store.link_event_to_context(
                event_id=event.id,
                context_id=context.id,
                confidence=max(0.3, min(1.0, context.confidence)),
                weight=max(0.1, min(5.0, 1.0 + math.log1p(context.support_count))),
                original_signal=str(source_ref.get("signal", "context_resolution") or "context_resolution"),
                evidence_span=str(source_ref.get("evidence_span", draft.evidence_span) or draft.evidence_span),
                timestamp=ts,
            )
            count += 1
        return count

    # -------------------------------------------------------------------------
    # Algorithm 3: Evolution-aware Retrieval
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
                    "confidence": 0.7,
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
        context_events = self.store.retrieve_events_by_contexts(
            [context.id for context in contexts],
            limit=route_limit,
        )

        merged: dict[str, Event] = {}
        for route_events in (entity_events, context_events):
            for event in route_events:
                merged[event.id] = event

        return {
            "events": list(merged.values()),
            "entity_events": entity_events,
            "context_events": context_events,
            "contexts": contexts,
        }

    # -------------------------------------------------------------------------
    # Algorithm 4: Consolidation and Forgetting
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
        if resolved_strategy == "disabled":
            return []
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
                gate = llm_gate
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
                        similarity_score=score,
                        local_reason=reason,
                    )
                    if decision is None or not decision.get("should_merge", False):
                        continue
                    strategy_used = "llm"
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
        if resolved_strategy == "disabled":
            return []
        contexts = self._list_all_contexts(only_active=True)
        if not contexts:
            return []

        threshold = 0.56
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
                        similarity_score=score,
                    )
                    if decision is None or not decision.get("should_merge", False):
                        continue
                    strategy_used = "llm"
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
        if resolved_strategy == "disabled":
            return 0
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
                gate = max(self.config.context_reuse_threshold, llm_gate)
                if score < gate:
                    continue

                canonical, merged_context = self._pick_canonical_context(a, b)
                if resolved_strategy == "llm":
                    decision = self._llm_context_merge_decision(
                        left=a,
                        right=b,
                        similarity_score=score,
                    )
                    if decision is None or not decision.get("should_merge", False):
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

    def consolidate_events(
        self,
        now: int,
        dry_run: bool = False,
        strategy: str = "auto",
    ) -> dict[str, int]:
        resolved_strategy = self._resolve_merge_strategy(strategy)
        if resolved_strategy == "disabled":
            return report
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
                gate = llm_gate
                if score < gate:
                    continue

                canonical, merged = self._pick_canonical_event(event, candidate)
                if resolved_strategy == "llm":
                    decision = self._llm_event_merge_decision(
                        left=event,
                        right=candidate,
                        similarity_score=score,
                        local_reason=reason,
                    )
                    if decision is None or not decision.get("should_merge", False):
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

        return changed

    def prune_weak_edges(self, now: int) -> int:
        stale_before = now - self.config.stale_seconds
        return 0

    # -------------------------------------------------------------------------
    # Algorithm 5: Conflict and Drift Management
    # -------------------------------------------------------------------------
    def detect_conflict(self, node: Context, new_evidence: ContextDraft) -> bool:
        return self._context_conflict_ratio(node, new_evidence) >= self.config.context_conflict_threshold

    def handle_context_conflict(self, context_node: Context, evidence: ContextDraft) -> Context:
        now = evidence.valid_from or int(time.time())
        sibling = evidence.to_node(
            context_id=f"{self._new_context_id(evidence)}_sib_{uuid.uuid4().hex[:6]}",
            timestamp=now,
            embedding=self._maybe_embed_context(evidence.summary),
        )
        sibling.confidence = max(0.45, sibling.confidence)
        self.store.save_context(sibling)
        if context_node.confidence < 0.4:
            context_node.status = "deprecated"
            context_node.valid_to = context_node.valid_to or now
        else:
            context_node.status = "weakened"
        context_node.updated_at = now
        self.store.update_context(context_node)
        return sibling

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
        payload_similarity = self._payload_similarity(event_a, event_b)
        time_similarity = self._time_similarity(event_a, event_b, now)
        score = (
            self.config.event_consolidation_text_weight * text_similarity
            + self.config.event_consolidation_context_weight * context_similarity
            + self.config.event_consolidation_payload_weight * payload_similarity
            + self.config.event_consolidation_time_weight * time_similarity
        )
        reasons = []
        if context_similarity > 0.0:
            reasons.append("shared_context")
        if payload_similarity >= 0.5:
            reasons.append("payload_similarity")
        if text_similarity >= 0.5:
            reasons.append("summary_similarity")
        if time_similarity >= 0.5:
            reasons.append("time_window")
        return score, ",".join(reasons) or "local_similarity"

    def _pick_canonical_event(self, event_a: Event, event_b: Event) -> tuple[Event, Event]:
        def rank_key(event: Event) -> tuple[int, float, int]:
            return (
                int(event.support_count or 1),
                float(0.7 or 0.0),
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

    def _build_context_draft(self, event: Event) -> ContextDraft:
        timestamp = event.last_active or event.timestamp or int(time.time())
        slots = self._extract_context_slots(event)
        subtype = self._infer_context_subtype(event, slots)
        summary = self._build_context_summary(subtype, slots)
        return ContextDraft(
            subtype=subtype,
            summary=summary,
            structured_slots=slots,
            confidence=max(0.4, 0.7),
            evidence_span=summary,
            source_refs=[{"source": "event_payload", "event_id": event.id, "signal": "event_payload"}],
            valid_from=timestamp,
        )

    def _context_similarity(self, a: Any, b: Any) -> float:
        slots_a = self._context_slots(a)
        slots_b = self._context_slots(b)
        slot_sim = self._value_similarity(slots_a, slots_b)
        summary_sim = self._lexical_similarity(self._context_summary(a), self._context_summary(b))
        subtype_sim = 1.0 if self._context_subtype(a) == self._context_subtype(b) else 0.0
        active_sim = 1.0 if self._context_status(a) == "active" else 0.75
        temporal_sim = self._context_temporal_compatibility(a, b)
        embedding_sim = self._context_embedding_similarity(a, b)
        return (
            0.28 * slot_sim
            + 0.24 * summary_sim
            + 0.18 * subtype_sim
            + 0.12 * active_sim
            + 0.10 * temporal_sim
            + 0.08 * embedding_sim
        )

    def _context_merge_score(self, a: Context, b: Context) -> float:
        base = self._context_similarity(a, b)
        containment = self._context_slot_containment_ratio(a, b)
        return 0.7 * base + 0.3 * containment

    def _context_slot_containment_ratio(self, a: Any, b: Any) -> float:
        slots_a = self._context_slots(a)
        slots_b = self._context_slots(b)
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

    def _context_conflict_ratio(self, a: Any, b: Any) -> float:
        slots_a = self._context_slots(a)
        slots_b = self._context_slots(b)
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
        if str(slots.get("phase", "") or slots.get("task_stage", "")).strip():
            return "phase"
        if str(slots.get("goal", "") or slots.get("goal_hint", "")).strip():
            return "goal"
        if str(slots.get("constraint", "") or slots.get("constraint_hint", "")).strip():
            return "constraint"
        if str(slots.get("state", "")).strip():
            return "state"
        if str(slots.get("environment", "") or slots.get("geo_context", "") or slots.get("digital_context", "")).strip():
            return "environment"
        return "situation"

    def _extract_context_slots(self, event: Event) -> dict[str, Any]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        time_range = event.time_range if isinstance(event.time_range, dict) else {}
        payload_context = payload.get("context", {}) if isinstance(payload.get("context", {}), dict) else {}
        participants = self._participant_labels(event.participants)
        scene_hint = self._infer_scene_hint(event)
        scene = str(
            payload.get("scene")
            or payload_context.get("scene", "")
            or payload_context.get("geo_context", "")
            or payload_context.get("digital_context", "")
            or scene_hint
            or ""
            or "generic"
        )
        slots = {
            "scene": scene,
            "geo_context": str(payload_context.get("geo_context", "") or ""),
            "digital_context": str(payload_context.get("digital_context", "") or ""),
            "time_bucket": str(time_range.get("display_time_bucket") or payload.get("time_bucket", "") or ""),
            "task_stage": str(payload.get("task_stage", "") or ""),
            "goal_hint": str(payload.get("goal_hint") or payload.get("goal") or scene_hint),
            "constraint_hint": str(payload.get("constraint_hint") or payload.get("constraint") or ""),
            "participants": participants,
            "device": self._collect_slot_values(payload, payload_context, key="device"),
            "app": self._collect_slot_values(payload, payload_context, key="app"),
            "place": self._collect_slot_values(payload, payload_context, key="place"),
            # Action is kept only as low-weight metadata for compatibility, not as subtype driver.
            "action_hint": str(event.action or payload.get("action", "") or ""),
        }
        return {key: value for key, value in slots.items() if value not in (None, "", [], {})}

    def _build_context_summary(self, subtype: str, slots: dict[str, Any]) -> str:
        preferred_keys = (
            subtype,
            "goal",
            "constraint",
            "state",
            "environment",
            "phase",
            "scene",
            "geo_context",
            "digital_context",
            "time_bucket",
        )
        for key in preferred_keys:
            value = slots.get(key)
            if value in (None, "", [], {}):
                continue
            if isinstance(value, list):
                text = " / ".join(str(item) for item in value if str(item).strip())
            else:
                text = str(value).strip()
            if text:
                return text[:180]
        return subtype[:180]

    def _context_slots(self, node: Any) -> dict[str, Any]:
        slots = getattr(node, "structured_slots", {})
        return dict(slots) if isinstance(slots, dict) else {}

    def _context_summary(self, node: Any) -> str:
        return str(getattr(node, "summary", "") or "").strip()

    def _context_subtype(self, node: Any) -> str:
        return str(getattr(node, "subtype", "") or "").strip()

    def _context_status(self, node: Any) -> str:
        return str(getattr(node, "status", "active") or "active").strip()

    def _context_embedding_similarity(self, left: Any, right: Any) -> float:
        left_embedding = getattr(left, "embedding", None)
        right_embedding = getattr(right, "embedding", None)
        if not left_embedding or not right_embedding:
            return 0.0
        try:
            numerator = sum(float(a) * float(b) for a, b in zip(left_embedding, right_embedding))
            left_norm = math.sqrt(sum(float(a) * float(a) for a in left_embedding))
            right_norm = math.sqrt(sum(float(b) * float(b) for b in right_embedding))
            if left_norm <= 0.0 or right_norm <= 0.0:
                return 0.0
            return max(0.0, min(1.0, numerator / (left_norm * right_norm)))
        except Exception:
            return 0.0

    def _context_temporal_compatibility(self, left: Any, right: Any) -> float:
        left_start = int(getattr(left, "valid_from", 0) or getattr(left, "created_at", 0) or 0)
        right_start = int(getattr(right, "valid_from", 0) or getattr(right, "created_at", 0) or 0)
        left_end = getattr(left, "valid_to", None)
        right_end = getattr(right, "valid_to", None)

        if left_end and right_start and int(left_end) < right_start:
            return 0.35
        if right_end and left_start and int(right_end) < left_start:
            return 0.35
        if not left_start or not right_start:
            return 0.7
        diff = abs(left_start - right_start)
        window = max(1, self.config.stale_seconds)
        return max(0.3, math.exp(-diff / window))

    def _merge_source_refs(
        self,
        existing: list[dict[str, Any]],
        incoming: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ref in list(existing or []) + list(incoming or []):
            if not isinstance(ref, dict):
                continue
            signature = safe_json_dumps(ref)
            if signature in seen:
                continue
            seen.add(signature)
            merged.append(dict(ref))
        return merged

    def _maybe_embed_context(self, text: str) -> Optional[list[float]]:
        if not text or not hasattr(self.store, "embedding_client"):
            return None
        embedding_client = getattr(self.store, "embedding_client", None)
        if embedding_client is None or not hasattr(embedding_client, "get_embedding"):
            return None
        try:
            return embedding_client.get_embedding(text)
        except Exception:
            return None

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
        return ["participants", "device", "app", "place", "action_hint"]

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
        canonical.source_refs = self._merge_source_refs(canonical.source_refs, merged.source_refs)
        canonical.merged_from = sorted(set(canonical.merged_from + [merged.id] + merged.merged_from))
        self.store.update_context(canonical)
        moved_links = self.store.relink_context_edges(
            source_context_id=merged.id,
            target_context_id=canonical.id,
            timestamp=ts,
        )
        merged.status = "merged"
        merged.valid_to = ts
        merged.updated_at = ts
        merged.merged_from = sorted(set(merged.merged_from + [merged.id]))
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
        if requested not in {"auto", "llm"}:
            requested = "auto"
        if requested == "llm":
            return "llm" if self._llm_merge_available() else "disabled"
        return "llm" if self._llm_merge_available() else "disabled"

    def _llm_merge_available(self) -> bool:
        api_key = str(self.config.llm_api_key or "").strip()
        return bool(
            dashscope is not None
            and Generation is not None
            and api_key
            and api_key not in {"YOUR_API_KEY", "sk-xxx"}
        )

    def _llm_event_merge_decision(
        self,
        left: Event,
        right: Event,
        similarity_score: float,
        local_reason: str,
    ) -> Optional[dict[str, Any]]:
        prompt = {
            "task": "Decide whether two memory events should be merged into one canonical event.",
            "rules": [
                "Only merge if they describe the same user memory or a direct duplicate.",
                "If one event is more complete or has stronger support, choose it as canonical.",
                "Return strict JSON only.",
            ],
            "similarity_score": round(float(similarity_score), 4),
            "local_reason": local_reason,
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
        similarity_score: float,
    ) -> Optional[dict[str, Any]]:
        prompt = {
            "task": "Decide whether two context nodes should be merged into one canonical context.",
            "rules": [
                "Only merge if they describe the same situation or one is a refinement of the other.",
                "Prefer the more informative or more supported context as canonical.",
                "Return strict JSON only.",
            ],
            "similarity_score": round(float(similarity_score), 4),
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

    def _event_prompt_payload(self, event: Event) -> dict[str, Any]:
        return {
            "id": event.id,
            "summary": event.summary,
            "action": event.action,
            "timestamp": event.timestamp,
            "last_active": event.last_active,
            "participants": event.participants,
            "payload": event.payload,
            "support_count": event.support_count,
            "context_ids": [context.id for context in self.store.get_event_contexts(event.id)],
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
            "source_refs": context.source_refs[:3],
        }

    def _new_context_id(self, context: Any) -> str:
        summary = self._context_summary(context)
        slots = self._context_slots(context)
        subtype = self._context_subtype(context) or "situation"
        valid_from = int(getattr(context, "valid_from", 0) or 0)
        signature = f"context|{subtype}|{summary}|{safe_json_dumps(slots)}|{valid_from}"
        return f"ctx_{hash_summary(signature)[:20]}"

    def _ensure_append_first_event_id(self, event: Event) -> str:
        if not self.config.append_first_mode:
            return event.id or hash_summary(event.summary)
        base = event.id or hash_summary(event.summary or uuid.uuid4().hex)
        timestamp = event.timestamp or event.last_active or int(time.time())
        return f"{base[:20]}_{timestamp}_{uuid.uuid4().hex[:6]}"

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

        context_match = self._context_query_match(contexts, query_entities, query)
        age = max(0, now - last_active)
        recency = math.exp(-DECAY_RATE * age)

        validity = 1.0
        if status in {"deprecated", "archived", "merged"}:
            validity = 0.25
        if status in {"weakened"}:
            validity = 0.5

        support_norm = min(1.0, math.log1p(support_count) / math.log1p(20))
        drift_penalty = 0.0
        decay_penalty = 1.0 - recency

        evolution_score = (
            self.config.retrieval_weight_event_sim * event_similarity
            + self.config.retrieval_weight_context * context_match
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
            "recency_factor": recency,
            "validity": validity,
            "support_norm": support_norm,
            "decay_penalty": decay_penalty,
            "drift_penalty": drift_penalty,
            "compressed_contexts": [c.summary for c in contexts[:2]],
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
