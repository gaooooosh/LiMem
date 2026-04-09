# -*- coding: utf-8 -*-
"""Dynamic evolution engine for long-term memory graph."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Optional, TypedDict
import json
import logging
import math
import os
import re
import time
import uuid

try:
    pass
except Exception:  # pragma: no cover - optional dependency for offline mode
    dashscope = None
    Generation = None

from ..config import (
    APPEND_FIRST_MODE,
    ARCHIVE_EVENT_SECONDS,
    BULK_INGEST_MODE,
    CONSOLIDATION_LOG_PATH,
    CONSOLIDATION_MIN_INTERVAL_SECONDS,
    CONTEXT_CANDIDATE_LIMIT,
    CONTEXT_CONFLICT_THRESHOLD,
    CONTEXT_EXTRACTION_BATCH_SIZE,
    CONTEXT_CORE_SLOT_WEIGHT,
    CONTEXT_AUX_SLOT_WEIGHT,
    CONTEXT_FUZZY_MATCH_THRESHOLD,
    CONTEXT_MERGE_CONTAINMENT_WEIGHT_DENSE,
    CONTEXT_MERGE_CONTAINMENT_WEIGHT_MID,
    CONTEXT_MERGE_CONTAINMENT_WEIGHT_SPARSE,
    CONTEXT_QUERY_CANDIDATE_LIMIT,
    CONTEXT_REUSE_THRESHOLD,
    CONTEXT_SIMILARITY_ACTIVE_WEIGHT,
    CONTEXT_SIMILARITY_EMBEDDING_WEIGHT,
    CONTEXT_SIMILARITY_SET_SLOT_WEIGHT,
    CONTEXT_SIMILARITY_SLOT_WEIGHT,
    CONTEXT_SIMILARITY_SUBTYPE_WEIGHT,
    CONTEXT_SIMILARITY_SUMMARY_WEIGHT,
    CONTEXT_SIMILARITY_TEMPORAL_WEIGHT,
    CONTEXT_SPARSE_SLOT_SUMMARY_FALLBACK,
    CONTEXT_SUBTYPE_COMPATIBLE_SCORE,
    CONTEXT_SUBTYPE_MISMATCH_FLOOR,
    CONTEXT_SUMMARY_CONTAINMENT_BONUS,
    CONTEXT_SUMMARY_SEMANTIC_EMBEDDING_WEIGHT,
    CONTEXT_SUMMARY_SEMANTIC_LEXICAL_WEIGHT,
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    DECAY_RATE,
    DECAY_STEP,
    ENABLE_AUTO_CONSOLIDATION,
    ENABLE_EVENT_RELATIONS,
    EVENT_CONSOLIDATION_CANDIDATE_LIMIT,
    EVENT_CONSOLIDATION_EMBEDDING_CANDIDATE_THRESHOLD,
    EVENT_CONSOLIDATION_EMBEDDING_TOP_K,
    EVENT_CONSOLIDATION_PAYLOAD_WEIGHT,
    EVENT_CONSOLIDATION_TEXT_WEIGHT,
    EVENT_CONSOLIDATION_CONTEXT_WEIGHT,
    EVENT_CONSOLIDATION_THRESHOLD,
    EVENT_CONSOLIDATION_TIME_WEIGHT,
    EVENT_CONSOLIDATION_WINDOW_SECONDS,
    EVENT_MERGE_TRACE_LOG_PATH,
    EVENT_MERGE_TRACE_STRATEGY_VERSION,
    GENERATION_MODEL,
    LLM_CONCURRENCY,
    REINFORCEMENT_STEP,
    RETRIEVAL_DEFAULT_CANDIDATE_LIMIT,
    RETRIEVAL_WEIGHT_CONTEXT,
    RETRIEVAL_WEIGHT_EVENT_SIM,
    RETRIEVAL_WEIGHT_RECENCY,
    RETRIEVAL_WEIGHT_SUPPORT,
    RETRIEVAL_WEIGHT_VALIDITY,
    STALE_SECONDS,
    WEAK_EDGE_PRUNE_THRESHOLD,
    normalize_dashscope_base_url,
)
from ..builder.context_extractor import ContextExtractionPipeline
from ..core.context import Context, ContextDraft
from ..core.event import Event
from ..llm import DashScopeClient
from ..utils import hash_summary, robust_json_loads, safe_json_dumps, safe_json_loads

logger = logging.getLogger(__name__)

_CONTEXT_ECHO_SLOT_KEYS = {
    "situation",
    "state",
    "constraint",
    "goal",
    "environment",
    "phase",
}


@dataclass
class DynamicEvolutionConfig:
    append_first_mode: bool = APPEND_FIRST_MODE
    llm_concurrency: int = LLM_CONCURRENCY
    bulk_ingest_mode: bool = BULK_INGEST_MODE
    merge_decision_strategy: str = "auto"
    llm_api_key: str = DASHSCOPE_API_KEY
    llm_base_url: str = DASHSCOPE_BASE_URL
    llm_model: str = GENERATION_MODEL
    context_reuse_threshold: float = CONTEXT_REUSE_THRESHOLD
    context_conflict_threshold: float = CONTEXT_CONFLICT_THRESHOLD
    context_candidate_limit: int = CONTEXT_CANDIDATE_LIMIT
    context_query_candidate_limit: int = CONTEXT_QUERY_CANDIDATE_LIMIT
    context_extraction_batch_size: int = CONTEXT_EXTRACTION_BATCH_SIZE
    context_core_slot_weight: float = CONTEXT_CORE_SLOT_WEIGHT
    context_aux_slot_weight: float = CONTEXT_AUX_SLOT_WEIGHT
    context_fuzzy_match_threshold: float = CONTEXT_FUZZY_MATCH_THRESHOLD
    context_summary_containment_bonus: float = CONTEXT_SUMMARY_CONTAINMENT_BONUS
    context_summary_semantic_lexical_weight: float = CONTEXT_SUMMARY_SEMANTIC_LEXICAL_WEIGHT
    context_summary_semantic_embedding_weight: float = CONTEXT_SUMMARY_SEMANTIC_EMBEDDING_WEIGHT
    context_sparse_slot_summary_fallback: float = CONTEXT_SPARSE_SLOT_SUMMARY_FALLBACK
    context_merge_containment_weight_dense: float = CONTEXT_MERGE_CONTAINMENT_WEIGHT_DENSE
    context_merge_containment_weight_mid: float = CONTEXT_MERGE_CONTAINMENT_WEIGHT_MID
    context_merge_containment_weight_sparse: float = CONTEXT_MERGE_CONTAINMENT_WEIGHT_SPARSE
    context_similarity_slot_weight: float = CONTEXT_SIMILARITY_SLOT_WEIGHT
    context_similarity_set_slot_weight: float = CONTEXT_SIMILARITY_SET_SLOT_WEIGHT
    context_similarity_summary_weight: float = CONTEXT_SIMILARITY_SUMMARY_WEIGHT
    context_similarity_subtype_weight: float = CONTEXT_SIMILARITY_SUBTYPE_WEIGHT
    context_similarity_active_weight: float = CONTEXT_SIMILARITY_ACTIVE_WEIGHT
    context_similarity_temporal_weight: float = CONTEXT_SIMILARITY_TEMPORAL_WEIGHT
    context_similarity_embedding_weight: float = CONTEXT_SIMILARITY_EMBEDDING_WEIGHT
    context_subtype_compatible_score: float = CONTEXT_SUBTYPE_COMPATIBLE_SCORE
    context_subtype_mismatch_floor: float = CONTEXT_SUBTYPE_MISMATCH_FLOOR

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
    event_consolidation_embedding_candidate_threshold: float = EVENT_CONSOLIDATION_EMBEDDING_CANDIDATE_THRESHOLD
    event_consolidation_embedding_top_k: int = EVENT_CONSOLIDATION_EMBEDDING_TOP_K
    event_consolidation_threshold: float = EVENT_CONSOLIDATION_THRESHOLD
    event_consolidation_text_weight: float = EVENT_CONSOLIDATION_TEXT_WEIGHT
    event_consolidation_context_weight: float = EVENT_CONSOLIDATION_CONTEXT_WEIGHT
    event_consolidation_payload_weight: float = EVENT_CONSOLIDATION_PAYLOAD_WEIGHT
    event_consolidation_time_weight: float = EVENT_CONSOLIDATION_TIME_WEIGHT
    event_merge_trace_strategy_version: str = EVENT_MERGE_TRACE_STRATEGY_VERSION
    event_merge_trace_log_path: str = EVENT_MERGE_TRACE_LOG_PATH
    enable_event_relations: bool = ENABLE_EVENT_RELATIONS

    def __post_init__(self) -> None:
        self.llm_base_url = normalize_dashscope_base_url(self.llm_base_url)


class EvolutionReport(TypedDict):
    context_links: int
    next_links: int
    event_relation_links: int


class WriteBatchReport(EvolutionReport):
    event_count: int


class DynamicEvolutionEngine:
    """Incremental dynamic graph update and retrieval engine."""

    def __init__(
        self,
        store: Any,
        config: Optional[DynamicEvolutionConfig] = None,
        llm_client: Optional[DashScopeClient] = None,
    ):
        self.store = store
        self.config = config or DynamicEvolutionConfig()
        self._last_consolidation_at = 0
        if llm_client is not None:
            self.llm_client = llm_client
        else:
            self.llm_client = DashScopeClient(
                api_key=self.config.llm_api_key,
                base_url=self.config.llm_base_url,
            )
        self.context_extractor = ContextExtractionPipeline(
            generation_model=self.config.llm_model,
            offline_mode=False,
            llm_client=self.llm_client,
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
    ) -> WriteBatchReport:
        if not events:
            return {
                "event_count": 0,
                "context_links": 0,
                "next_links": 0,
                "event_relation_links": 0,
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

        resolved_context_batches = self._resolve_context_pairs_for_event_batch(
            events=new_events,
            record=record,
        )
        for event, resolved_contexts in zip(new_events, resolved_context_batches):
            in_links += self.attach_contexts_to_event(event, resolved_contexts)
        if self.config.enable_event_relations:
            relation_links += self.extract_event_event_relations(
                events=new_events,
                record=record,
            )
        if self._should_run_auto_consolidation():
            ts = int(time.time())
            if ts - self._last_consolidation_at >= self.config.consolidation_min_interval_seconds:
                self.run_consolidation(current_time=ts)

        return {
            "event_count": len(new_events),
            "context_links": in_links,
            "next_links": 0,
            "event_relation_links": relation_links,
        }

    def evolve_existing_events(self, events: list[Event]) -> EvolutionReport:
        """Apply local dynamic updates for already-persisted events."""
        if not events:
            return {"context_links": 0, "next_links": 0, "event_relation_links": 0}

        context_links = 0
        relation_links = 0
        resolved_context_batches = self._resolve_context_pairs_for_event_batch(
            events=events,
            record=None,
        )
        for event, resolved_contexts in zip(events, resolved_context_batches):
            context_links += self.attach_contexts_to_event(event, resolved_contexts)
        if self.config.enable_event_relations:
            relation_links += self.extract_event_event_relations(
                events=events,
                record=None,
            )
        if self._should_run_auto_consolidation():
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
        relation_pairs: list[tuple[Event, Event]] = []
        for idx, left in enumerate(events):
            for right in events[idx + 1:]:
                if not left or not right or left.id == right.id:
                    continue
                if left.status in {"merged", "archived"} or right.status in {"merged", "archived"}:
                    continue
                if not self._same_relation_scope(left, right):
                    continue
                relation_pairs.append((left, right))

        if not relation_pairs:
            return 0

        workers = self._llm_workers(task_count=len(relation_pairs))
        if workers <= 1:
            decisions = [
                (
                    left,
                    right,
                    self._call_relation_llm(
                        self._relation_prompt_payload(left=left, right=right, source_text=source_text)
                    ),
                )
                for left, right in relation_pairs
            ]
        else:
            decisions: list[tuple[Event, Event, Optional[dict[str, Any]]]] = []
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        self._call_relation_llm,
                        self._relation_prompt_payload(left=left, right=right, source_text=source_text),
                    ): (left, right)
                    for left, right in relation_pairs
                }
                for future in as_completed(futures):
                    left, right = futures[future]
                    decisions.append((left, right, future.result()))

        for left, right, decision in decisions:
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
            resp = self.llm_client.call_generation(
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
            )
            content = self.llm_client.message_content(resp)
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

    def _llm_workers(self, task_count: int) -> int:
        concurrency = max(1, int(self.config.llm_concurrency or 1))
        return max(1, min(concurrency, task_count))

    def _should_run_auto_consolidation(self) -> bool:
        if not self.config.enable_auto_consolidation:
            return False
        return not self.config.bulk_ingest_mode

    def _is_valid_context_draft(self, draft: ContextDraft) -> bool:
        validator = getattr(self.context_extractor, "is_valid_context_draft", None)
        if callable(validator):
            try:
                return bool(validator(draft))
            except Exception:
                logger.warning("context draft validation failed", exc_info=True)
                return False
        return bool(isinstance(draft, ContextDraft) and self._context_summary(draft))

    def _filter_valid_context_drafts(self, drafts: list[ContextDraft]) -> list[ContextDraft]:
        return [
            draft for draft in drafts
            if isinstance(draft, ContextDraft) and self._is_valid_context_draft(draft)
        ]

    def _resolve_context_pairs_for_event_batch(
        self,
        events: list[Event],
        record: Optional[Any],
    ) -> list[list[tuple[Context, ContextDraft]]]:
        if not events:
            return []

        drafts_by_index: dict[int, list[ContextDraft]] = {}
        batched_extract = getattr(self.context_extractor, "extract_batch", None)
        max_batch_size = max(1, int(self.config.context_extraction_batch_size or 1))
        if callable(batched_extract) and len(events) > 1:
            for start in range(0, len(events), max_batch_size):
                batch_events = events[start : start + max_batch_size]
                batch_records = [record if record is not None else event for event in batch_events]
                try:
                    batch_drafts = batched_extract(records=batch_records, events=batch_events)
                    if len(batch_drafts) != len(batch_events):
                        raise ValueError(
                            f"expected {len(batch_events)} batch context results, got {len(batch_drafts)}"
                        )
                    for offset, drafts in enumerate(batch_drafts):
                        drafts_by_index[start + offset] = self._filter_valid_context_drafts(drafts)
                except Exception as exc:
                    logger.warning(
                        "batch context extraction failed for events[%s:%s]; "
                        "falling back to per-event extraction for this slice: %s",
                        start,
                        start + len(batch_events),
                        exc,
                    )

        missing_indices = [idx for idx in range(len(events)) if idx not in drafts_by_index]
        if missing_indices:
            workers = self._llm_workers(task_count=len(missing_indices))
            if workers <= 1:
                for idx in missing_indices:
                    drafts_by_index[idx] = self.extract_context_drafts(events[idx], record=record)
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(self.extract_context_drafts, events[idx], record): idx
                        for idx in missing_indices
                    }
                    for future in as_completed(futures):
                        drafts_by_index[futures[future]] = future.result()

        resolved_batches: list[list[tuple[Context, ContextDraft]]] = []
        for idx, event in enumerate(events):
            drafts = drafts_by_index.get(idx, [])
            resolved_batches.append(
                [(self.resolve_context(draft, event=event), draft) for draft in drafts]
            )
        return resolved_batches

    # -------------------------------------------------------------------------
    # Algorithm 2: Dynamic Context Resolution
    # -------------------------------------------------------------------------
    def extract_context_drafts(
        self,
        event: Event,
        record: Optional[Any] = None,
    ) -> list[ContextDraft]:
        drafts = self.context_extractor.extract(record=record or event, event=event)
        return self._filter_valid_context_drafts(drafts)

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
            predicted_id = self._new_context_id(context_draft)
            existing = self.store.get_context(predicted_id)
            if existing is not None and existing.status != "merged":
                match = existing
        if match is None:
            return self.create_context(context_draft)

        if self.detect_conflict(match, context_draft):
            return self.handle_context_conflict(match, context_draft)

        return self.update_context_with_evidence(match, context_draft, event)

    def match_existing_context(self, context_draft: ContextDraft) -> Optional[Context]:
        all_candidates = self.store.find_context_candidates(
            context_type=context_draft.context_type,
            subtype="",
            limit=self.config.context_candidate_limit,
            only_active=True,
        )
        draft_summary_key = self._normalized_context_text(self._context_summary(context_draft))
        seen_candidate_ids = {candidate.id for candidate in all_candidates}
        exact_matches = [
            candidate for candidate in all_candidates
            if self._normalized_context_text(candidate.summary) == draft_summary_key
        ]
        if exact_matches:
            return max(exact_matches, key=self._context_rank_key)

        if draft_summary_key:
            global_exact_matches: list[Context] = []
            for context_id, summary in self.store.find_contexts_summary_index(
                context_type=context_draft.context_type,
                only_active=True,
            ):
                if context_id in seen_candidate_ids:
                    continue
                if self._normalized_context_text(summary) != draft_summary_key:
                    continue
                context = self.store.get_context(context_id)
                if context is not None:
                    global_exact_matches.append(context)
            if global_exact_matches:
                return max(global_exact_matches, key=self._context_rank_key)

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
        if best is not None and best_score >= self.config.context_reuse_threshold:
            return best

        # Secondary pass: allow cross-subtype reuse for highly similar contexts.
        cross_candidates = self.store.find_context_candidates(
            context_type=context_draft.context_type,
            subtype="",
            limit=self.config.context_candidate_limit,
            only_active=True,
        )
        for candidate in cross_candidates:
            score = self._context_similarity(candidate, context_draft)
            if score > best_score:
                best = candidate
                best_score = score
        if best is None:
            return None
        # Cross-subtype reuse: same base threshold is sufficient because
        # _context_similarity already penalises subtype mismatches via its
        # subtype_sim component.
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
        focus_event_ids: Optional[list[str]] = None,
        event_same_scope_only: bool = False,
    ) -> dict[str, Any]:
        operation_time = int(current_time or time.time())
        reference_time = self._resolve_event_reference_time(current_time)
        normalized_scope = (scope or "all").strip().lower()
        if normalized_scope not in {"all", "event", "events", "context", "contexts"}:
            raise ValueError(f"Unsupported auto merge scope: {scope}")

        include_events = normalized_scope in {"all", "event", "events"}
        include_contexts = normalized_scope in {"all", "context", "contexts"}
        event_plans = self.detect_event_merges(
            current_time=reference_time,
            strategy=strategy,
            max_pairs=max_pairs,
            focus_event_ids=focus_event_ids,
            same_scope_only=event_same_scope_only,
        ) if include_events else []
        context_merge_result = self.merge_all_contexts(
            current_time=operation_time,
            strategy=strategy,
            dry_run=True,
            max_pairs=max_pairs,
        ) if include_contexts else {"context_plans": [], "context_candidates": 0}
        context_plans = list(context_merge_result.get("context_plans", []))

        applied_events = 0
        applied_contexts = 0
        if not dry_run:
            for plan in event_plans:
                if self._apply_event_merge_plan(plan, merged_at=operation_time):
                    applied_events += 1
            if include_contexts:
                applied_contexts = int(
                    self.merge_all_contexts(
                        current_time=operation_time,
                        strategy=strategy,
                        dry_run=False,
                        max_pairs=max_pairs,
                    ).get("merged_contexts", 0)
                    or 0
                )

        return {
            "scope": normalized_scope,
            "requested_strategy": (strategy or "auto").strip().lower(),
            "resolved_strategy": self._resolve_merge_strategy(strategy),
            "dry_run": bool(dry_run),
            "event_candidates": len(event_plans),
            "context_candidates": int(context_merge_result.get("context_candidates", len(context_plans)) or len(context_plans)),
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
        focus_event_ids: Optional[list[str]] = None,
        same_scope_only: bool = False,
    ) -> list[dict[str, Any]]:
        now = self._resolve_event_reference_time(current_time)
        resolved_strategy = self._resolve_merge_strategy(strategy)
        if resolved_strategy == "disabled":
            return []
        focus_set = {
            str(event_id or "").strip()
            for event_id in (focus_event_ids or [])
            if str(event_id or "").strip()
        }
        if focus_set:
            events = self.store.list_events(limit=300, statuses=["active"])
        else:
            events = self.store.get_recent_events(
                current_time=now,
                window_seconds=self.config.event_consolidation_window_seconds,
                limit=300,
            )
        if not events:
            return []

        event_map = {event.id: event for event in events}
        if focus_set:
            events = [event for event in events if event.id in focus_set]
            events.sort(key=lambda item: item.last_active or item.timestamp or 0, reverse=True)
            if not events:
                return []
        merged_sources: set[str] = set()
        visited_pairs: set[tuple[str, str]] = set()
        planned_canonicals: set[str] = set()
        plans: list[dict[str, Any]] = []
        llm_gate = self.config.event_consolidation_threshold * 0.72

        for event in events:
            if event.id in merged_sources or event.status in {"merged", "archived"}:
                continue

            candidates = self._retrieve_event_consolidation_candidates(event, event_map, now)
            for candidate in candidates:
                if candidate.id == event.id or candidate.id in merged_sources:
                    continue
                if self._should_skip_event_merge_pair(event, candidate, same_scope_only=same_scope_only):
                    continue
                pair_key = tuple(sorted([event.id, candidate.id]))
                if pair_key in visited_pairs:
                    continue
                visited_pairs.add(pair_key)

                score, reason = self._event_merge_similarity(event, candidate, now)
                embedding_similarity = self._event_embedding_similarity(
                    self._ensure_event_embedding(event),
                    self._ensure_event_embedding(candidate),
                )
                gate = llm_gate
                if resolved_strategy != "llm" and score < gate:
                    continue

                canonical, merged = self._pick_canonical_event(event, candidate)
                plan_reason = self._build_event_merge_reason(
                    source="embedding_preselect+heuristic_gate",
                    local_reason=reason,
                    embedding_similarity=embedding_similarity,
                    llm_reason="",
                    strategy=resolved_strategy,
                )
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
                    llm_reason = str(decision.get("reason", "") or "").strip()
                    plan_reason = self._build_event_merge_reason(
                        source="embedding_preselect+llm_judge",
                        local_reason=reason,
                        embedding_similarity=embedding_similarity,
                        llm_reason=llm_reason,
                        strategy="llm",
                    )
                    confidence = max(score, float(decision.get("confidence", score) or score))

                stabilized_pair = self._stabilize_event_merge_pair(
                    canonical=canonical,
                    merged=merged,
                    merged_sources=merged_sources,
                    planned_canonicals=planned_canonicals,
                )
                if stabilized_pair is None:
                    continue
                canonical, merged = stabilized_pair

                plans.append(
                    {
                        "kind": "event",
                        "strategy": strategy_used,
                        "canonical_event_id": canonical.id,
                        "merged_event_id": merged.id,
                        "canonical_summary": canonical.summary,
                        "merged_summary": merged.summary,
                        "score": round(float(confidence), 4),
                        "embedding_similarity": round(float(embedding_similarity), 4),
                        "reason": plan_reason,
                    }
                )
                merged_sources.add(merged.id)
                planned_canonicals.add(canonical.id)
                break

            if len(plans) >= max_pairs:
                break
        return plans[:max_pairs]

    def _resolve_event_reference_time(self, current_time: Optional[int] = None) -> int:
        if current_time is not None and int(current_time) > 0:
            return int(current_time)
        try:
            latest_events = self.store.list_events(limit=1, statuses=["active"])
        except Exception:
            latest_events = []
        if latest_events:
            latest_event = latest_events[0]
            latest_ts = int(latest_event.last_active or latest_event.timestamp or 0)
            if latest_ts > 0:
                return latest_ts
        return int(time.time())

    def _is_aggregated_event(self, event: Optional[Event]) -> bool:
        if event is None:
            return False
        payload = event.payload if isinstance(event.payload, dict) else {}
        merge_inputs = payload.get("merge_inputs", [])
        merge_trace = payload.get("merge_trace", [])
        if isinstance(merge_inputs, list) and merge_inputs:
            return True
        if isinstance(merge_trace, list) and merge_trace:
            return True
        return int(event.support_count or 1) > 1

    def _should_skip_event_merge_pair(
        self,
        left: Event,
        right: Event,
        same_scope_only: bool = False,
    ) -> bool:
        if same_scope_only and not self._same_event_scope(left, right):
            return True
        left_aggregated = self._is_aggregated_event(left)
        right_aggregated = self._is_aggregated_event(right)
        if left_aggregated and right_aggregated:
            return True
        if (left_aggregated or right_aggregated) and not self._same_event_scope(left, right):
            return True
        return False

    def _same_event_scope(self, left: Event, right: Event) -> bool:
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
        return False

    def _stabilize_event_merge_pair(
        self,
        canonical: Event,
        merged: Event,
        merged_sources: set[str],
        planned_canonicals: set[str],
    ) -> Optional[tuple[Event, Event]]:
        if canonical.id == merged.id:
            return None
        if canonical.id in merged_sources and merged.id not in merged_sources:
            canonical, merged = merged, canonical
        if merged.id in planned_canonicals and canonical.id not in planned_canonicals:
            canonical, merged = merged, canonical
        if canonical.id in merged_sources or merged.id in merged_sources:
            return None
        if merged.id in planned_canonicals:
            return None
        return canonical, merged

    def detect_context_merges(
        self,
        current_time: Optional[int] = None,
        strategy: str = "auto",
        max_pairs: int = 10,
    ) -> list[dict[str, Any]]:
        _ = int(current_time or time.time())
        plans, _resolved = self._plan_context_merges(strategy=strategy, max_pairs=max_pairs)
        return plans

    def merge_all_contexts(
        self,
        current_time: Optional[int] = None,
        strategy: str = "auto",
        dry_run: bool = False,
        max_pairs: int = 50,
    ) -> dict[str, Any]:
        now = int(current_time or time.time())
        plans, resolved_strategy = self._plan_context_merges(
            strategy=strategy,
            max_pairs=max_pairs,
        )
        merged_count = 0
        if not dry_run:
            for plan in plans:
                canonical_id = str(plan.get("canonical_context_id", "") or "").strip()
                merged_id = str(plan.get("merged_context_id", "") or "").strip()
                if not canonical_id or not merged_id or canonical_id == merged_id:
                    continue
                self.merge_contexts(
                    canonical_context_id=canonical_id,
                    merged_context_id=merged_id,
                    merged_at=now,
                )
                merged_count += 1

        return {
            "requested_strategy": (strategy or "auto").strip().lower(),
            "resolved_strategy": resolved_strategy,
            "dry_run": bool(dry_run),
            "context_candidates": len(plans),
            "merged_contexts": merged_count,
            "context_plans": plans,
        }

    def consolidate_contexts(self, now: int, strategy: str = "auto") -> int:
        result = self.merge_all_contexts(
            current_time=now,
            strategy=strategy,
            dry_run=False,
            max_pairs=max(1, self.config.context_candidate_limit * 3),
        )
        return int(result.get("merged_contexts", 0) or 0)

    def consolidate_events(
        self,
        now: int,
        dry_run: bool = False,
        strategy: str = "auto",
    ) -> dict[str, int]:
        report = {
            "scanned_events": 0,
            "candidate_pairs": 0,
            "merged_events": 0,
            "archived_events": 0,
            "skipped_events": 0,
        }
        resolved_strategy = self._resolve_merge_strategy(strategy)
        if resolved_strategy == "disabled":
            return report
        llm_gate = self.config.event_consolidation_threshold * 0.72
        events = self.store.get_recent_events(
            current_time=now,
            window_seconds=self.config.event_consolidation_window_seconds,
            limit=300,
        )
        report["scanned_events"] = len(events)
        if not events:
            return report

        event_map = {event.id: event for event in events}
        merged_sources: set[str] = set()
        planned_canonicals: set[str] = set()
        visited_pairs: set[tuple[str, str]] = set()

        for event in events:
            if event.id in merged_sources or event.status in {"merged", "archived"}:
                report["skipped_events"] += 1
                continue

            candidates = self._retrieve_event_consolidation_candidates(event, event_map, now)
            for candidate in candidates:
                if candidate.id == event.id or candidate.id in merged_sources:
                    continue
                if self._should_skip_event_merge_pair(event, candidate):
                    continue
                pair_key = tuple(sorted([event.id, candidate.id]))
                if pair_key in visited_pairs:
                    continue
                visited_pairs.add(pair_key)
                report["candidate_pairs"] += 1

                score, reason = self._event_merge_similarity(event, candidate, now)
                embedding_similarity = self._event_embedding_similarity(
                    self._ensure_event_embedding(event),
                    self._ensure_event_embedding(candidate),
                )
                gate = llm_gate
                if resolved_strategy != "llm" and score < gate:
                    continue

                canonical, merged = self._pick_canonical_event(event, candidate)
                merge_reason = self._build_event_merge_reason(
                    source="embedding_preselect+heuristic_gate",
                    local_reason=reason,
                    embedding_similarity=embedding_similarity,
                    llm_reason="",
                    strategy=resolved_strategy,
                )
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
                    llm_reason = str(decision.get("reason", "") or "").strip()
                    merge_reason = self._build_event_merge_reason(
                        source="embedding_preselect+llm_judge",
                        local_reason=reason,
                        embedding_similarity=embedding_similarity,
                        llm_reason=llm_reason,
                        strategy="llm",
                    )
                    score = max(score, float(decision.get("confidence", score) or score))
                stabilized_pair = self._stabilize_event_merge_pair(
                    canonical=canonical,
                    merged=merged,
                    merged_sources=merged_sources,
                    planned_canonicals=planned_canonicals,
                )
                if stabilized_pair is None:
                    continue
                canonical, merged = stabilized_pair
                if not dry_run:
                    self._merge_event_pair(
                        canonical=canonical,
                        merged=merged,
                        similarity_score=score,
                        merge_reason=merge_reason,
                        embedding_similarity=embedding_similarity,
                        merged_at=now,
                    )
                merged_sources.add(merged.id)
                planned_canonicals.add(canonical.id)
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
        overlap_count = self._context_overlap_key_count(node, new_evidence)
        if overlap_count < 1:
            return False
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

    def _plan_context_merges(
        self,
        strategy: str,
        max_pairs: int,
    ) -> tuple[list[dict[str, Any]], str]:
        resolved_strategy = self._resolve_merge_strategy(strategy)
        if resolved_strategy == "disabled":
            return [], resolved_strategy
        contexts = self._list_all_contexts(only_active=True)
        if not contexts:
            return [], resolved_strategy

        threshold = max(self.config.context_reuse_threshold, 0.58)
        conflict_skip_threshold = min(0.95, self.config.context_conflict_threshold + 0.20)
        llm_min_score = 0.30
        llm_cross_subtype_min_score = 0.45
        candidates: list[tuple[float, dict[str, Any]]] = []
        for i in range(len(contexts)):
            for j in range(i + 1, len(contexts)):
                left = contexts[i]
                right = contexts[j]
                if left.status != "active" or right.status != "active":
                    continue
                if left.context_type != right.context_type:
                    continue
                if self._context_conflict_ratio(left, right) >= conflict_skip_threshold:
                    continue

                score = self._context_merge_score(left, right)
                if resolved_strategy != "llm" and score < threshold:
                    continue
                if resolved_strategy == "llm":
                    if score < llm_min_score:
                        continue
                    if self._context_subtype(left) != self._context_subtype(right) and score < llm_cross_subtype_min_score:
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
        return plans, resolved_strategy

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

        # Step 1: embedding-high candidates as the primary candidate set.
        base_embedding = self._ensure_event_embedding(event)
        if base_embedding:
            ranked: list[tuple[float, Event]] = []
            threshold = float(self.config.event_consolidation_embedding_candidate_threshold or 0.0)
            for candidate in event_map.values():
                if candidate.id == event.id or candidate.status in {"merged", "archived"}:
                    continue
                similarity = self._event_embedding_similarity(
                    base_embedding,
                    self._ensure_event_embedding(candidate),
                )
                if similarity < threshold:
                    continue
                ranked.append((similarity, candidate))
            ranked.sort(key=lambda item: item[0], reverse=True)
            top_k = max(
                1,
                min(
                    int(self.config.event_consolidation_embedding_top_k or 1),
                    int(self.config.event_consolidation_candidate_limit or 1),
                ),
            )
            for _, candidate in ranked[:top_k]:
                add_candidate(candidate)

        # Step 2: temporal/context fallback to keep recall when embeddings are missing/noisy.
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
        embedding_similarity: Optional[float] = None,
    ) -> None:
        canonical.support_count += max(1, merged.support_count)
        canonical.time_range = self._merge_slots(
            canonical.time_range if isinstance(canonical.time_range, dict) else {},
            merged.time_range if isinstance(merged.time_range, dict) else {},
        )
        canonical.participants = self._merge_list_values(canonical.participants, merged.participants)
        canonical.last_active = max(canonical.last_active, merged.last_active, merged_at)
        canonical.updated_at = merged_at
        canonical.evidence = self._merge_list_values(canonical.evidence, merged.evidence)
        canonical.embedding = canonical.embedding or merged.embedding
        canonical.payload = self._merge_event_payload(canonical.payload, merged.payload, merged.id, merged_at)
        semantics = self._rewrite_merged_event_semantics(canonical, merged)
        canonical.summary = semantics["summary"] or canonical.summary or merged.summary
        canonical.action = semantics["action"] or canonical.action or merged.action
        canonical.causality = semantics["causality"] or canonical.causality or merged.causality
        if isinstance(canonical.payload, dict):
            canonical.payload["summary"] = canonical.summary
            canonical.payload["action"] = canonical.action
            canonical.payload["causality"] = canonical.causality
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
            embedding_similarity=embedding_similarity,
        )

    def _merge_event_payload(
        self,
        payload: dict[str, Any],
        incoming_payload: dict[str, Any],
        target_event_id: str,
        merged_at: int,
        source_event_id: Optional[str] = None,
    ) -> dict[str, Any]:
        import copy
        # 使用深拷贝彻底切断引用链
        base = self._merge_payload_values(
            copy.deepcopy(payload or {}),
            copy.deepcopy(incoming_payload or {})
        )
        if incoming_payload:
            # 使用深拷贝避免循环引用
            base.setdefault("merge_inputs", []).append(copy.deepcopy(incoming_payload))
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

    def _merge_payload_values(
        self,
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        import copy
        merged = copy.deepcopy(existing or {})
        for key, value in (incoming or {}).items():
            if key not in merged or merged.get(key) in (None, "", [], {}):
                # 使用深拷贝避免引用问题
                merged[key] = copy.deepcopy(value)
                continue
            current = merged.get(key)
            if isinstance(current, dict) and isinstance(value, dict):
                merged[key] = self._merge_payload_values(current, copy.deepcopy(value))
                continue
            if isinstance(current, list) and isinstance(value, list):
                merged[key] = self._merge_list_values(current, value)
        return merged

    def _merge_event_summary(self, canonical_summary: str, merged_summary: str) -> str:
        left = str(canonical_summary or "").strip()
        right = str(merged_summary or "").strip()
        if not left:
            return right
        if not right or right in left:
            return left
        return f"{left}；{right}"

    def _rewrite_merged_event_semantics(self, canonical: Event, merged: Event) -> dict[str, str]:
        fragments = self._collect_event_summary_fragments(canonical, merged)
        entities = sorted(set(self.store.get_event_entities(canonical.id)) | set(self.store.get_event_entities(merged.id)))
        actor = self._primary_event_actor_label(canonical.participants)

        summary = self._rewrite_merged_event_summary(canonical, merged)
        action = self._rewrite_merged_event_action(
            canonical,
            merged,
            summary=summary,
            entities=entities,
            actor=actor,
        )
        causality = self._rewrite_merged_event_causality(
            canonical,
            merged,
            summary=summary,
            action=action,
            fragments=fragments,
            actor=actor,
        )
        return {
            "summary": summary,
            "action": action,
            "causality": causality,
        }

    def _rewrite_merged_event_summary(self, canonical: Event, merged: Event) -> str:
        fragments = self._collect_event_summary_fragments(canonical, merged)
        if not fragments:
            return self._merge_event_summary(canonical.summary, merged.summary)

        entities = sorted(set(self.store.get_event_entities(canonical.id)) | set(self.store.get_event_entities(merged.id)))
        actor = self._primary_event_actor_label(canonical.participants)
        main_fragment = self._select_main_event_fragment(fragments, entities=entities, actor=actor)
        if not main_fragment:
            return self._merge_event_summary(canonical.summary, merged.summary)

        summary = self._normalize_main_event_fragment(main_fragment, actor=actor)
        detail_fragments = self._select_detail_fragments(
            fragments,
            main_fragment=main_fragment,
            actor=actor,
        )
        details: list[str] = []
        detail_signatures: set[str] = set()
        for fragment in detail_fragments:
            normalized = self._normalize_detail_fragment(fragment, actor=actor, main_summary=summary)
            signature = self._detail_signature(normalized)
            if not normalized or normalized == summary or signature in detail_signatures:
                continue
            details.append(normalized)
            if signature:
                detail_signatures.add(signature)
            if len(details) >= 2:
                break
        if details:
            summary = f"{summary}，{'，'.join(details)}"
        return summary[:180]

    def _rewrite_merged_event_action(
        self,
        canonical: Event,
        merged: Event,
        summary: str,
        entities: list[str],
        actor: str,
    ) -> str:
        action_candidates = self._collect_event_action_fragments(canonical, merged)
        if self._looks_like_navigation_cluster(summary, action_candidates, entities):
            return f"导航到{entities[0]}"[:120]

        best = self._select_best_action_fragment(action_candidates, summary=summary, actor=actor)
        if best:
            return best[:120]

        fallback = self._action_from_summary(summary=summary, actor=actor)
        return fallback[:120]

    def _rewrite_merged_event_causality(
        self,
        canonical: Event,
        merged: Event,
        summary: str,
        action: str,
        fragments: list[str],
        actor: str,
    ) -> str:
        explicit_candidates = self._collect_event_causality_fragments(canonical, merged)
        for candidate in explicit_candidates:
            normalized = self._normalize_causality_fragment(candidate, actor=actor, action=action)
            if normalized:
                return normalized[:120]

        main_fragment = summary.split("，", 1)[0].strip()
        detail_candidates = self._select_detail_fragments(
            fragments,
            main_fragment=main_fragment,
            actor=actor,
        )
        for fragment in detail_candidates:
            normalized = self._normalize_causality_fragment(fragment, actor=actor, action=action)
            if normalized:
                return normalized[:120]
        return ""

    def _collect_event_summary_fragments(self, canonical: Event, merged: Event) -> list[str]:
        fragments: list[str] = []

        def add_fragment(value: Any) -> None:
            text = str(value or "").strip()
            if not text or text in fragments:
                return
            fragments.append(text)

        add_fragment(canonical.summary)
        add_fragment(merged.summary)

        payloads: list[dict[str, Any]] = []
        if isinstance(canonical.payload, dict):
            payloads.append(canonical.payload)
            merge_inputs = canonical.payload.get("merge_inputs", [])
            if isinstance(merge_inputs, list):
                payloads.extend(item for item in merge_inputs if isinstance(item, dict))
        if isinstance(merged.payload, dict):
            payloads.append(merged.payload)

        for payload in payloads:
            add_fragment(payload.get("summary", ""))
            add_fragment(payload.get("action", ""))
            add_fragment(payload.get("causality", ""))

        return fragments

    def _collect_event_action_fragments(self, canonical: Event, merged: Event) -> list[str]:
        fragments: list[str] = []

        def add_fragment(value: Any) -> None:
            text = str(value or "").strip()
            if not text or text in fragments:
                return
            fragments.append(text)

        add_fragment(canonical.action)
        add_fragment(merged.action)
        if isinstance(canonical.payload, dict):
            add_fragment(canonical.payload.get("action", ""))
            merge_inputs = canonical.payload.get("merge_inputs", [])
            if isinstance(merge_inputs, list):
                for payload in merge_inputs:
                    if isinstance(payload, dict):
                        add_fragment(payload.get("action", ""))
                        add_fragment(payload.get("summary", ""))
        return fragments

    def _collect_event_causality_fragments(self, canonical: Event, merged: Event) -> list[str]:
        fragments: list[str] = []

        def add_fragment(value: Any) -> None:
            text = str(value or "").strip()
            if not text or text in fragments:
                return
            fragments.append(text)

        add_fragment(canonical.causality)
        add_fragment(merged.causality)

        for payload in [canonical.payload, merged.payload]:
            if not isinstance(payload, dict):
                continue
            add_fragment(payload.get("causality", ""))
            merge_inputs = payload.get("merge_inputs", [])
            if isinstance(merge_inputs, list):
                for item in merge_inputs:
                    if isinstance(item, dict):
                        add_fragment(item.get("causality", ""))
                        add_fragment(item.get("summary", ""))
        return fragments

    def _primary_event_actor_label(self, participants: list[Any]) -> str:
        if not isinstance(participants, list):
            return ""
        for item in participants:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip()
            if role:
                return role
        return ""

    def _event_fragment_kind(self, fragment: str) -> str:
        text = str(fragment or "").strip()
        if not text:
            return "empty"
        if re.search(r"(耗时|用时|时长|历时|持续)\s*\d*", text) or re.search(r"\d+\s*(分钟|小时|秒|公里|米|次)", text):
            return "metric"
        if any(token in text for token in ["发起", "请求", "准备", "提出", "尝试", "要求"]):
            return "trigger"
        if text.startswith(("已", "已经")) or (
            any(token in text for token in ["车机", "系统"])
            and any(token in text for token in ["启动", "开启", "关闭", "切换", "设置", "调整", "开始", "停止"])
        ):
            return "result"
        if any(token in text for token in ["导航到", "导航去", "前往", "去往", "播放", "开启", "打开", "关闭", "切换", "设置", "调整", "启动", "停止", "暂停", "恢复", "提醒"]):
            return "main"
        return "generic"

    def _select_main_event_fragment(
        self,
        fragments: list[str],
        entities: list[str],
        actor: str,
    ) -> str:
        best_fragment = ""
        best_score = float("-inf")
        for fragment in fragments:
            kind = self._event_fragment_kind(fragment)
            score = 0.0
            if kind == "main":
                score += 6.0
            elif kind == "result":
                score += 4.0
            elif kind == "generic":
                score += 3.0
            elif kind == "trigger":
                score += 1.0
            elif kind == "metric":
                score -= 2.0
            if 6 <= len(fragment) <= 28:
                score += 1.5
            if actor and actor in fragment:
                score += 0.5
            if any(entity and entity in fragment for entity in entities):
                score += 2.0
            if any(token in fragment for token in ["导航到", "导航去", "前往", "去往", "到", "去"]):
                score += 1.5
            if re.search(r"\d+\s*(分钟|小时|秒)", fragment):
                score -= 1.0
            if score > best_score:
                best_score = score
                best_fragment = fragment
        return best_fragment

    def _normalize_main_event_fragment(self, fragment: str, actor: str) -> str:
        text = str(fragment or "").strip(" ，,。；;")
        if not text:
            return ""
        if actor and actor not in {"车机", "系统"} and not text.startswith(("用户", "车机", "系统", "环境")):
            text = f"{actor}{text}"
        return text

    def _detail_signature(self, text: str) -> str:
        value = str(text or "").strip(" ，,。；;")
        if not value:
            return ""
        value = re.sub(r"^(用户|车机|系统|环境)", "", value).strip(" ：:，,。；;")
        if self._event_fragment_kind(value) == "metric":
            metric_match = re.search(r"(耗时|用时|时长|历时|持续).*$", value)
            if metric_match:
                value = metric_match.group(0)
        return value

    def _select_detail_fragments(
        self,
        fragments: list[str],
        main_fragment: str,
        actor: str,
    ) -> list[str]:
        metric_fragments = []
        result_fragments = []
        auxiliary_fragments = []
        for fragment in fragments:
            if fragment == main_fragment:
                continue
            kind = self._event_fragment_kind(fragment)
            if kind == "metric":
                metric_fragments.append(fragment)
                continue
            if kind == "result":
                result_fragments.append(fragment)
                continue
            if kind == "main" and fragment != main_fragment:
                normalized = self._normalize_main_event_fragment(fragment, actor=actor)
                if normalized != self._normalize_main_event_fragment(main_fragment, actor=actor):
                    auxiliary_fragments.append(fragment)
        return metric_fragments + result_fragments + auxiliary_fragments

    def _normalize_detail_fragment(self, fragment: str, actor: str, main_summary: str) -> str:
        text = str(fragment or "").strip(" ，,。；;")
        if not text:
            return ""
        if self._event_fragment_kind(text) == "metric":
            metric_match = re.search(r"(耗时|用时|时长|历时|持续).*$", text)
            if metric_match:
                text = metric_match.group(0)
        if actor and main_summary.startswith(actor) and text.startswith(actor):
            text = text[len(actor):].strip(" ，,。；;")
        detail_signature = self._detail_signature(text)
        if main_summary and text and (
            text in main_summary or (detail_signature and detail_signature in self._detail_signature(main_summary))
        ):
            return ""
        return text

    def _looks_like_navigation_cluster(
        self,
        summary: str,
        action_candidates: list[str],
        entities: list[str],
    ) -> bool:
        if not entities:
            return False
        combined = " ".join([summary] + list(action_candidates)).strip()
        return "导航" in combined or "前往" in combined or "去往" in combined

    def _action_from_summary(self, summary: str, actor: str) -> str:
        head = str(summary or "").split("，", 1)[0].split("；", 1)[0].strip(" ，,。；;")
        if actor and head.startswith(actor):
            head = head[len(actor):].strip(" ，,。；;")
        return head

    def _normalize_action_candidate(self, candidate: str, actor: str) -> str:
        text = str(candidate or "").strip(" ，,。；;")
        if not text:
            return ""
        if actor and text.startswith(actor):
            text = text[len(actor):].strip(" ，,。；;")
        return text

    def _select_best_action_fragment(
        self,
        candidates: list[str],
        summary: str,
        actor: str,
    ) -> str:
        summary_head = self._action_from_summary(summary, actor)
        best = ""
        best_score = float("-inf")
        for candidate in candidates:
            normalized = self._normalize_action_candidate(candidate, actor)
            if not normalized:
                continue
            kind = self._event_fragment_kind(normalized)
            score = 0.0
            if kind == "main":
                score += 4.0
            elif kind == "generic":
                score += 2.5
            elif kind == "trigger":
                score += 1.0
            elif kind == "result":
                score += 0.5
            elif kind == "metric":
                score -= 3.0
            if normalized == summary_head:
                score += 2.0
            elif normalized and normalized in summary_head:
                score += 1.5
            if 2 <= len(normalized) <= 16:
                score += 1.0
            elif len(normalized) <= 24:
                score += 0.5
            if score > best_score:
                best = normalized
                best_score = score
        return best

    def _normalize_causality_fragment(self, fragment: str, actor: str, action: str) -> str:
        text = str(fragment or "").strip(" ，,。；;")
        if not text:
            return ""
        if self._event_fragment_kind(text) == "trigger":
            return ""
        if actor and text.startswith(actor):
            text = text[len(actor):].strip(" ，,。；;")
        if self._detail_signature(text) == self._detail_signature(action):
            return ""
        if self._event_fragment_kind(text) == "metric":
            metric_match = re.search(r"(耗时|用时|时长|历时|持续).*$", text)
            if metric_match:
                text = metric_match.group(0)
        return text

    def _merge_list_values(self, left: Any, right: Any) -> list[Any]:
        result: list[Any] = []
        seen: set[str] = set()
        for item in list(left or []) + list(right or []):
            signature = safe_json_dumps(item)
            if signature in seen:
                continue
            seen.add(signature)
            result.append(item)
        return result

    def _event_embedding_similarity(
        self,
        left_embedding: Optional[list[float]],
        right_embedding: Optional[list[float]],
    ) -> float:
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

    def _ensure_event_embedding(self, event: Event) -> Optional[list[float]]:
        if event.embedding:
            return event.embedding
        text = self._event_text_for_embedding(event)
        if not text:
            return None
        embedding = self._maybe_embed_context(text)
        if embedding:
            event.embedding = embedding
            try:
                self.store.update_event(event)
            except Exception:
                pass
        return embedding

    def _event_text_for_embedding(self, event: Event) -> str:
        parts = [
            str(event.summary or "").strip(),
            str(event.action or "").strip(),
            str(event.causality or "").strip(),
        ]
        return " ".join(part for part in parts if part)

    def _build_event_merge_reason(
        self,
        source: str,
        local_reason: str,
        embedding_similarity: float,
        llm_reason: str,
        strategy: str,
    ) -> str:
        payload = {
            "source": str(source or "").strip(),
            "strategy": str(strategy or "").strip(),
            "local_reason": str(local_reason or "").strip(),
            "llm_reason": str(llm_reason or "").strip(),
            "embedding_similarity": round(float(embedding_similarity or 0.0), 4),
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

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
        slots_a = self._effective_context_slots(a)
        slots_b = self._effective_context_slots(b)
        core_keys = self._core_context_slot_keys()
        aux_keys = self._aux_context_slot_keys()
        core_slot_sim = self._slot_group_similarity(slots_a, slots_b, core_keys)
        aux_slot_sim = self._slot_group_similarity(slots_a, slots_b, aux_keys)
        core_group_weight = (
            float(self.config.context_core_slot_weight or 0.0)
            if self._slot_group_has_signal(slots_a, slots_b, core_keys)
            else 0.0
        )
        aux_group_weight = (
            float(self.config.context_aux_slot_weight or 0.0)
            if self._slot_group_has_signal(slots_a, slots_b, aux_keys)
            else 0.0
        )
        total_slot_weight = max(
            1e-6,
            core_group_weight + aux_group_weight,
        )
        slot_sim = (
            core_group_weight * core_slot_sim
            + aux_group_weight * aux_slot_sim
        ) / total_slot_weight
        set_slot_sim = self._value_similarity(slots_a, slots_b)
        summary_sim = self._summary_semantic_similarity(a, b)
        subtype_sim = self._context_subtype_similarity(self._context_subtype(a), self._context_subtype(b))
        active_sim = 1.0 if self._context_status(a) == "active" else 0.75
        temporal_sim = self._context_temporal_compatibility(a, b)
        embedding_sim = self._context_embedding_similarity(a, b)
        slot_weight = float(self.config.context_similarity_slot_weight or 0.0)
        set_slot_weight = float(self.config.context_similarity_set_slot_weight or 0.0)
        summary_weight = float(self.config.context_similarity_summary_weight or 0.0)
        if not slots_a and not slots_b:
            summary_weight += slot_weight + set_slot_weight
            slot_weight = 0.0
            set_slot_weight = 0.0
        return (
            slot_weight * slot_sim
            + set_slot_weight * set_slot_sim
            + summary_weight * summary_sim
            + float(self.config.context_similarity_subtype_weight or 0.0) * subtype_sim
            + float(self.config.context_similarity_active_weight or 0.0) * active_sim
            + float(self.config.context_similarity_temporal_weight or 0.0) * temporal_sim
            + float(self.config.context_similarity_embedding_weight or 0.0) * embedding_sim
        )

    def _context_merge_score(self, a: Context, b: Context) -> float:
        summary_sim = self._summary_semantic_similarity(a, b)
        base = self._context_similarity(a, b)
        if summary_sim >= 0.7:
            return max(base, 0.5 + 0.5 * summary_sim)
        containment = self._context_slot_containment_ratio(a, b)
        slot_density = min(
            len(self._filled_slot_values(self._effective_context_slots(a))),
            len(self._filled_slot_values(self._effective_context_slots(b))),
        )
        if slot_density >= 3:
            containment_weight = float(self.config.context_merge_containment_weight_dense or 0.0)
        elif slot_density >= 1:
            containment_weight = float(self.config.context_merge_containment_weight_mid or 0.0)
        else:
            containment_weight = float(self.config.context_merge_containment_weight_sparse or 0.0)
        return (1.0 - containment_weight) * base + containment_weight * containment

    def _context_slot_containment_ratio(self, a: Any, b: Any) -> float:
        values_a = self._filled_slot_values(self._effective_context_slots(a))
        values_b = self._filled_slot_values(self._effective_context_slots(b))
        if not values_a and not values_b:
            return self._summary_containment(a, b)
        if not values_a or not values_b:
            return float(self.config.context_sparse_slot_summary_fallback or 0.0) * self._summary_containment(a, b)

        def contains(smaller: dict[str, Any], larger: dict[str, Any]) -> float:
            if not smaller:
                return 0.0
            matched = 0.0
            for key, value in smaller.items():
                if key in larger:
                    matched += self._value_overlap_score(value, larger.get(key))
            return matched / len(smaller)

        return max(contains(values_a, values_b), contains(values_b, values_a))

    def _pick_canonical_context(self, left: Context, right: Context) -> tuple[Context, Context]:
        if self._context_rank_key(left) >= self._context_rank_key(right):
            return left, right
        return right, left

    def _context_rank_key(self, context: Context) -> tuple[int, int, int, int]:
        slot_count = len(self._effective_context_slots(context))
        return (
            int(context.support_count or 1),
            slot_count,
            int(context.last_seen_at or context.updated_at or 0),
            int(context.created_at or 0),
        )

    def _context_conflict_ratio(self, a: Any, b: Any) -> float:
        slots_a = self._effective_context_slots(a)
        slots_b = self._effective_context_slots(b)
        overlap_keys = set(slots_a.keys()) & set(slots_b.keys())
        overlap = 0
        conflicts = 0
        for key in overlap_keys:
            va = slots_a.get(key)
            vb = slots_b.get(key)
            if self._is_empty_slot_value(va) or self._is_empty_slot_value(vb):
                continue
            overlap += 1
            if not self._value_overlap(va, vb):
                conflicts += 1
        return (conflicts / overlap) if overlap else 0.0

    def _context_overlap_key_count(self, a: Any, b: Any) -> int:
        slots_a = self._effective_context_slots(a)
        slots_b = self._effective_context_slots(b)
        overlap_keys = set(slots_a.keys()) & set(slots_b.keys())
        count = 0
        for key in overlap_keys:
            va = slots_a.get(key)
            vb = slots_b.get(key)
            if self._is_empty_slot_value(va) or self._is_empty_slot_value(vb):
                continue
            count += 1
        return count

    def _context_subtype_similarity(self, left: str, right: str) -> float:
        left_norm = str(left or "").strip().lower()
        right_norm = str(right or "").strip().lower()
        if left_norm == right_norm:
            return 1.0
        compatible_groups = [
            {"situation", "environment", "state"},
            {"constraint", "goal"},
            {"phase", "state"},
            {"situation", "phase"},
        ]
        for group in compatible_groups:
            if left_norm in group and right_norm in group:
                return float(self.config.context_subtype_compatible_score or 0.0)
        return float(self.config.context_subtype_mismatch_floor or 0.0)

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

    def _effective_context_slots(self, node: Any) -> dict[str, Any]:
        return self._strip_echo_slots(
            summary=self._context_summary(node),
            slots=self._context_slots(node),
        )

    def _normalized_context_text(self, value: Any) -> str:
        return re.sub(r"[\s，,。；;：:、/\\-]+", "", str(value or "").strip().lower())

    def _strip_echo_slots(self, summary: str, slots: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(slots, dict) or not slots:
            return {}
        cleaned: dict[str, Any] = {}
        summary_norm = self._normalized_context_text(summary)
        for key, value in slots.items():
            if key in _CONTEXT_ECHO_SLOT_KEYS and isinstance(value, str):
                value_norm = self._normalized_context_text(value)
                if summary_norm and value_norm:
                    if value_norm == summary_norm:
                        continue
                    shorter, longer = (
                        (value_norm, summary_norm)
                        if len(value_norm) <= len(summary_norm)
                        else (summary_norm, value_norm)
                    )
                    ratio = len(shorter) / max(1, len(longer))
                    if len(shorter) > 8 and shorter in longer and ratio > 0.8:
                        continue
            cleaned[key] = value
        return cleaned

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

    @staticmethod
    def _is_empty_slot_value(value: Any) -> bool:
        return value in (None, "", [], {})

    def _filled_slot_values(self, slots: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value for key, value in slots.items()
            if not self._is_empty_slot_value(value)
        }

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
        score_sum = 0.0
        total = 0
        for key in keys:
            va = slots_a.get(key)
            vb = slots_b.get(key)
            if self._is_empty_slot_value(va) and self._is_empty_slot_value(vb):
                continue
            total += 1
            if self._is_empty_slot_value(va) or self._is_empty_slot_value(vb):
                continue
            score_sum += self._value_overlap_score(va, vb)
        return (score_sum / total) if total else 0.0

    def _slot_group_has_signal(
        self,
        slots_a: dict[str, Any],
        slots_b: dict[str, Any],
        keys: list[str],
    ) -> bool:
        for key in keys:
            if not self._is_empty_slot_value(slots_a.get(key)) or not self._is_empty_slot_value(slots_b.get(key)):
                return True
        return False

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
        jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
        if left_tokens and right_tokens:
            smaller, larger = (
                (left_tokens, right_tokens)
                if len(left_tokens) <= len(right_tokens)
                else (right_tokens, left_tokens)
            )
            containment = len(smaller & larger) / len(smaller)
            jaccard = max(
                jaccard,
                float(self.config.context_summary_containment_bonus or 0.0) * containment,
            )
        return max(jaccard, self._fuzzy_string_score(left, right))

    def _summary_containment(self, a: Any, b: Any) -> float:
        tokens_a = self._tokenize_text(self._context_summary(a))
        tokens_b = self._tokenize_text(self._context_summary(b))
        if not tokens_a or not tokens_b:
            return 0.0
        smaller, larger = (
            (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a)
        )
        return len(smaller & larger) / len(smaller)

    def _summary_semantic_similarity(self, a: Any, b: Any) -> float:
        lexical = self._lexical_similarity(self._context_summary(a), self._context_summary(b))
        embedding = self._context_embedding_similarity(a, b)
        if embedding > 0.0:
            lexical_weight = float(self.config.context_summary_semantic_lexical_weight or 0.0)
            embedding_weight = float(self.config.context_summary_semantic_embedding_weight or 0.0)
            total_weight = max(1e-6, lexical_weight + embedding_weight)
            return (lexical_weight * lexical + embedding_weight * embedding) / total_weight
        return lexical

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
        action_similarity = self._text_token_similarity(event_a.action, event_b.action)
        causality_similarity = self._text_token_similarity(event_a.causality, event_b.causality)
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
        exact = len(left_set & right_set) / len(left_set | right_set)
        if exact > 0.0:
            return exact
        if len(left_set) <= 5 and len(right_set) <= 5:
            best_scores = [
                max((self._fuzzy_string_score(left_value, right_value) for right_value in right_set), default=0.0)
                for left_value in left_set
            ]
            if best_scores:
                return sum(best_scores) / len(left_set | right_set)
        return exact

    def _fuzzy_string_score(self, a: str, b: str) -> float:
        a_norm = str(a or "").strip().lower()
        b_norm = str(b or "").strip().lower()
        if not a_norm or not b_norm:
            return 0.0
        if a_norm == b_norm:
            return 1.0
        if a_norm in b_norm or b_norm in a_norm:
            shorter, longer = (
                (a_norm, b_norm) if len(a_norm) <= len(b_norm) else (b_norm, a_norm)
            )
            return len(shorter) / max(1, len(longer))
        bg_a = {a_norm[idx: idx + 2] for idx in range(max(1, len(a_norm) - 1))}
        bg_b = {b_norm[idx: idx + 2] for idx in range(max(1, len(b_norm) - 1))}
        bigram_score = 0.0
        if bg_a and bg_b:
            bigram_score = len(bg_a & bg_b) / len(bg_a | bg_b)
        char_a = {ch for ch in a_norm if not ch.isspace()}
        char_b = {ch for ch in b_norm if not ch.isspace()}
        char_score = 0.0
        if char_a and char_b:
            char_score = len(char_a & char_b) / len(char_a | char_b)
        return max(bigram_score, char_score)

    @staticmethod
    def _text_token_similarity(a: Optional[str], b: Optional[str]) -> float:
        """Character-level token overlap similarity for short Chinese/mixed texts."""
        a_str = (a or "").strip()
        b_str = (b or "").strip()
        if not a_str or not b_str:
            return 0.0
        if a_str == b_str:
            return 1.0
        # Use character-level bigrams for Chinese text robustness
        def _bigrams(s: str) -> set[str]:
            s = s.lower()
            if len(s) < 2:
                return {s}
            return {s[i : i + 2] for i in range(len(s) - 1)}
        bg_a = _bigrams(a_str)
        bg_b = _bigrams(b_str)
        if not bg_a or not bg_b:
            return 0.0
        return len(bg_a & bg_b) / len(bg_a | bg_b)

    def _as_value_set(self, value: Any) -> set[str]:
        if isinstance(value, list):
            return {str(item).strip() for item in value if str(item).strip()}
        if isinstance(value, dict):
            return {f"{key}:{item}".strip() for key, item in value.items() if str(item).strip()}
        if value in (None, ""):
            return set()
        return {str(value).strip()}

    def _value_overlap(self, va: Any, vb: Any) -> bool:
        return self._value_overlap_score(va, vb) >= float(self.config.context_fuzzy_match_threshold or 0.0)

    def _value_overlap_score(self, va: Any, vb: Any) -> float:
        if isinstance(va, list) or isinstance(vb, list):
            sa = {str(item).strip() for item in (va if isinstance(va, list) else [va]) if str(item).strip()}
            sb = {str(item).strip() for item in (vb if isinstance(vb, list) else [vb]) if str(item).strip()}
            if not sa and not sb:
                return 0.0
            exact = len(sa & sb) / len(sa | sb)
            if exact > 0.0:
                return exact
            return max(
                (self._fuzzy_string_score(left_value, right_value) for left_value in sa for right_value in sb),
                default=0.0,
            )
        a_str = str(va).strip()
        b_str = str(vb).strip()
        if a_str == b_str:
            return 1.0
        return self._fuzzy_string_score(a_str, b_str)

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
        rewrite_strategy: str = "rewrite",
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
        original_canonical_summary = str(canonical.summary or "").strip()
        original_merged_summary = str(merged.summary or "").strip()
        if (rewrite_strategy or "rewrite").strip().lower() == "rewrite":
            canonical.summary = self._rewrite_merged_context_summary(canonical, merged)
        else:
            canonical.summary = canonical.summary or merged.summary
        canonical.source_refs = self._merge_source_refs(canonical.source_refs, merged.source_refs)
        canonical.source_refs = self._merge_source_refs(
            canonical.source_refs,
            [
                {
                    "source": "context_merge_rewrite",
                    "canonical_context_id": canonical.id,
                    "merged_context_id": merged.id,
                    "canonical_summary_before": original_canonical_summary,
                    "merged_summary_before": original_merged_summary,
                    "canonical_summary_after": canonical.summary,
                    "merged_at": ts,
                }
            ],
        )
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

    def _rewrite_merged_context_summary(self, canonical: Context, merged: Context) -> str:
        merged_slots = self._merge_slots(
            dict(canonical.structured_slots if isinstance(canonical.structured_slots, dict) else {}),
            dict(merged.structured_slots if isinstance(merged.structured_slots, dict) else {}),
        )
        subtype = self._context_subtype(canonical) or self._context_subtype(merged) or "situation"
        rewritten = self._build_context_summary(subtype, merged_slots)
        rewritten = str(rewritten or "").strip()
        if rewritten:
            return rewritten[:180]
        fallback = str(canonical.summary or merged.summary or subtype).strip()
        return fallback[:180]

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
            embedding_similarity=(
                float(plan.get("embedding_similarity"))
                if plan.get("embedding_similarity") is not None
                else None
            ),
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
        if requested not in {"auto", "llm", "heuristic", "disabled"}:
            requested = "auto"
        if requested == "disabled":
            return "disabled"
        if requested == "heuristic":
            return "heuristic"
        if requested == "llm":
            return "llm" if self._llm_merge_available() else "disabled"
        return "llm" if self._llm_merge_available() else "heuristic"

    def _llm_merge_available(self) -> bool:
        return self.llm_client.has_generation_api() and self.llm_client.has_valid_api_key()

    def _llm_event_merge_decision(
        self,
        left: Event,
        right: Event,
        similarity_score: float,
        local_reason: str,
    ) -> Optional[dict[str, Any]]:
        prompt = {
            "task": (
                "Decide whether two atomic memory events should stay separate or be aggregated "
                "into one canonical main event."
            ),
            "rules": [
                (
                    "Merge if they are direct duplicates, or if they are complementary atomic sub-events "
                    "from the same episode/session that together describe one higher-level user interaction."
                ),
                "Never merge two events when both are already aggregated main events produced by previous merges.",
                (
                    "Prefer merge when they share the same episode/session, time anchor, context, participants, "
                    "or one event refines the trigger/request and the other adds target/result/duration details."
                ),
                (
                    "Do not merge if they express different intents or should remain independently retrievable "
                    "as separate memories."
                ),
                "If merging, choose the more informative or more supported event as canonical.",
                "Return strict JSON only.",
            ],
            "similarity_score": round(float(similarity_score), 4),
            "local_reason": local_reason,
            "pair_features": self._event_pair_features(left, right),
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
            resp = self.llm_client.call_generation(
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
            )
            content = self.llm_client.message_content(resp)
            data = robust_json_loads(content, None)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _event_prompt_payload(self, event: Event) -> dict[str, Any]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        merge_inputs = payload.get("merge_inputs", [])
        return {
            "id": event.id,
            "summary": event.summary,
            "action": event.action,
            "timestamp": event.timestamp,
            "last_active": event.last_active,
            "participants": event.participants,
            "payload": payload,
            "support_count": event.support_count,
            "is_aggregated_main_event": self._is_aggregated_event(event),
            "merge_input_count": len(merge_inputs) if isinstance(merge_inputs, list) else 0,
            "context_ids": [context.id for context in self.store.get_event_contexts(event.id)],
        }

    def _event_pair_features(self, left: Event, right: Event) -> dict[str, Any]:
        left_payload = left.payload if isinstance(left.payload, dict) else {}
        right_payload = right.payload if isinstance(right.payload, dict) else {}

        left_episode_id = str(left_payload.get("episode_id", "") or "").strip()
        right_episode_id = str(right_payload.get("episode_id", "") or "").strip()
        left_session_id = str(left_payload.get("session_id", "") or "").strip()
        right_session_id = str(right_payload.get("session_id", "") or "").strip()

        left_episode_text = str(left_payload.get("episode_text", "") or "").strip()
        right_episode_text = str(right_payload.get("episode_text", "") or "").strip()
        shared_episode_text = left_episode_text if left_episode_text and left_episode_text == right_episode_text else ""

        left_context_ids = {context.id for context in self.store.get_event_contexts(left.id)}
        right_context_ids = {context.id for context in self.store.get_event_contexts(right.id)}
        left_entity_ids = set(self.store.get_event_entities(left.id))
        right_entity_ids = set(self.store.get_event_entities(right.id))
        left_participants = set(self._participant_labels(left.participants))
        right_participants = set(self._participant_labels(right.participants))

        left_index = left_payload.get("event_index")
        right_index = right_payload.get("event_index")
        try:
            left_index = int(left_index) if left_index not in (None, "") else None
        except (TypeError, ValueError):
            left_index = None
        try:
            right_index = int(right_index) if right_index not in (None, "") else None
        except (TypeError, ValueError):
            right_index = None

        return {
            "same_episode": bool(left_episode_id and left_episode_id == right_episode_id),
            "same_session": bool(left_session_id and left_session_id == right_session_id),
            "shared_episode_id": left_episode_id if left_episode_id and left_episode_id == right_episode_id else "",
            "shared_session_id": left_session_id if left_session_id and left_session_id == right_session_id else "",
            "shared_episode_text_excerpt": shared_episode_text[:240],
            "left_event_index": left_index,
            "right_event_index": right_index,
            "event_index_distance": (
                abs(left_index - right_index)
                if left_index is not None and right_index is not None
                else None
            ),
            "shared_context_ids": sorted(left_context_ids & right_context_ids)[:4],
            "shared_entity_ids": sorted(left_entity_ids & right_entity_ids)[:6],
            "shared_participants": sorted(left_participants & right_participants)[:4],
            "left_is_aggregated_main_event": self._is_aggregated_event(left),
            "right_is_aggregated_main_event": self._is_aggregated_event(right),
            "both_are_aggregated_main_events": (
                self._is_aggregated_event(left) and self._is_aggregated_event(right)
            ),
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
        slots = self._effective_context_slots(context)
        subtype = self._context_subtype(context) or "situation"
        signature = f"context|{subtype}|{summary}|{safe_json_dumps(slots)}"
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
        embedding_similarity: Optional[float] = None,
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
        if embedding_similarity is not None:
            payload["embedding_similarity"] = float(embedding_similarity)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
