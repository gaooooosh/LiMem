# -*- coding: utf-8 -*-
"""Event lifecycle relation extraction and manual event merging."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional
import copy
import json
import logging
import os
import time

from ..core.event import Event
from ..utils import load_prompt, robust_json_loads, safe_json_dumps
from .recall_pipeline import CandidateSet, RecallCandidate
from .relation_types import REL_MEANING_UPDATE, REL_SHARED_CONTEXT

logger = logging.getLogger(__name__)


@dataclass
class OperationDecision:
    """A narrow relation decision between two active Event nodes.

    Event relation extraction is intentionally not a graph-rewrite engine. It
    may write lifecycle edges, but it must not merge, archive, derive, or create
    replacement Event nodes during normal ingest.
    """

    candidate: RecallCandidate
    operation: str
    confidence: float
    reason: str
    direction: str = ""
    value_before: str = ""
    value_after: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessResult:
    updates: int = 0
    extensions: int = 0
    derivations: int = 0
    merges: int = 0
    links: int = 0
    skipped: int = 0
    total_links: int = 0
    decisions: list[dict[str, Any]] = field(default_factory=list)


class RelationProcessor:
    """Write only residual Event-Event lifecycle constraints.

    Entity overlap, shared Context, and merge history are modeled elsewhere in
    the graph. This processor only handles:
    - supersedes: an old Event interpretation is changed by a newer Event.
    - co_recall: two active Events must be recalled together and the shared
      background cannot already be represented by Context.
    """

    _OPERATIONS = {"skip", "supersedes", "co_recall"}

    def __init__(self, store: Any, llm_client: Any, config: Any):
        self.store = store
        self.llm_client = llm_client
        self.config = config
        self._classify_relations_system_prompt = load_prompt("evolution/classify_relations_system.txt")
        self._classify_relations_user_prompt = load_prompt("evolution/classify_relations_user.txt")
        self._rewrite_merged_event_system_prompt = load_prompt("evolution/rewrite_merged_event_system.txt")
        self._rewrite_merged_event_user_prompt = load_prompt("evolution/rewrite_merged_event_user.txt")
        self._rewrite_merge_callback: Optional[
            Callable[[Event, Event], Optional[dict[str, Any]]]
        ] = None

    def bind_engine(self, engine: Any) -> None:
        self._rewrite_merge_callback = lambda canonical, merged: engine._llm_rewrite_merged_event(
            canonical,
            merged,
        )

    def process(self, e_new: Event, candidates: CandidateSet, source_text: str) -> ProcessResult:
        if not candidates.candidates:
            return ProcessResult()

        deterministic, llm_candidates, skipped = self._split_candidates(e_new, candidates)
        decisions = deterministic + self._classify_batch(
            e_new=e_new,
            candidates=CandidateSet(candidates=llm_candidates, channel_stats=candidates.channel_stats),
            source_text=source_text,
        )
        result = self._execute_decisions(e_new=e_new, decisions=decisions)
        result.skipped += skipped
        return result

    def _split_candidates(
        self,
        e_new: Event,
        candidates: CandidateSet,
    ) -> tuple[list[OperationDecision], list[RecallCandidate], int]:
        deterministic: list[OperationDecision] = []
        llm_candidates: list[RecallCandidate] = []
        skipped = 0

        for candidate in candidates.candidates:
            event = candidate.event
            if not isinstance(event, Event) or event.id == e_new.id:
                skipped += 1
                continue
            if event.status in {"merged", "archived", "ignored"}:
                skipped += 1
                continue

            state_update = self._state_update_decision(e_new=e_new, candidate=candidate)
            if state_update is not None:
                deterministic.append(state_update)
                continue

            explicit_reference = self._explicit_reference_decision(e_new=e_new, candidate=candidate)
            if explicit_reference is not None:
                deterministic.append(explicit_reference)
                continue

            if self._shared_context_ids(e_new, event):
                skipped += 1
                continue

            if self._is_entity_only_candidate(candidate):
                skipped += 1
                continue

            if self._needs_llm_judgement(candidate):
                llm_candidates.append(candidate)
            else:
                skipped += 1

        limited_llm_candidates = self._limit_llm_candidates(llm_candidates)
        skipped += max(0, len(llm_candidates) - len(limited_llm_candidates))
        return deterministic, limited_llm_candidates, skipped

    def _classify_batch(
        self,
        e_new: Event,
        candidates: CandidateSet,
        source_text: str,
    ) -> list[OperationDecision]:
        if not candidates.candidates:
            return []

        batch_size = max(
            1,
            int(getattr(self.config, "relation_classification_batch_size", 15) or 15),
        )
        decisions: list[OperationDecision] = []
        for start in range(0, len(candidates.candidates), batch_size):
            chunk = candidates.candidates[start : start + batch_size]
            decisions.extend(
                self._classify_chunk_llm(
                    e_new=e_new,
                    chunk=chunk,
                    source_text=source_text,
                )
            )
        return decisions

    def _classify_chunk_llm(
        self,
        e_new: Event,
        chunk: list[RecallCandidate],
        source_text: str,
    ) -> list[OperationDecision]:
        if not self._classify_relations_system_prompt or not self._classify_relations_user_prompt:
            return [
                OperationDecision(
                    candidate=candidate,
                    operation="skip",
                    confidence=0.0,
                    reason="missing_classification_prompt",
                )
                for candidate in chunk
            ]
        payload = {
            "new_event": self._event_prompt_payload(e_new),
            "source_text": str(source_text or "").strip(),
            "candidates": [
                {
                    "index": idx,
                    "event": self._event_prompt_payload(candidate.event),
                    "recall": {
                        "primary_channel": candidate.channel,
                        "primary_score": round(float(candidate.channel_score or 0.0), 6),
                        "aggregate_score": round(
                            float(candidate.features.get("aggregate_score", 0.0) or 0.0),
                            6,
                        ),
                        "channels": candidate.features.get("channels", {}),
                        "channel_features": candidate.features.get("channel_features", {}),
                    },
                }
                for idx, candidate in enumerate(chunk)
            ],
            "output_schema": {
                "decisions": [
                    {
                        "index": 0,
                        "operation": "skip",
                        "confidence": 0.3,
                        "reason": "实体或背景已经由 Entity/Context 表达，不需要 Event-Event 约束",
                        "direction": "",
                        "value_before": "",
                        "value_after": "",
                    }
                ]
            },
        }
        user_message = self._classify_relations_user_prompt.format(
            payload_json=json.dumps(payload, ensure_ascii=False)
        )
        try:
            response = self.llm_client.call_generation(
                model=self.config.llm_model,
                messages=self.llm_client.build_messages(
                    self._classify_relations_system_prompt,
                    user_message,
                ),
            )
            raw = robust_json_loads(self.llm_client.message_content(response), {})
        except Exception:
            logger.warning("relation classification failed", exc_info=True)
            raw = {}
        return self._normalize_classification_output(chunk=chunk, raw=raw)

    def _normalize_classification_output(
        self,
        chunk: list[RecallCandidate],
        raw: Any,
    ) -> list[OperationDecision]:
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            items = raw.get("decisions", [])
        else:
            items = []

        min_confidence = float(getattr(self.config, "relation_min_confidence", 0.5) or 0.5)
        indexed: dict[int, OperationDecision] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= len(chunk):
                continue
            operation = str(item.get("operation", "skip") or "skip").strip().lower()
            if operation not in self._OPERATIONS:
                operation = "skip"
            confidence = self._coerce_confidence(item.get("confidence"))
            if confidence < min_confidence:
                operation = "skip"
            indexed[index] = OperationDecision(
                candidate=chunk[index],
                operation=operation,
                confidence=confidence,
                reason=str(item.get("reason", "") or "").strip(),
                direction=str(item.get("direction", "") or "").strip(),
                value_before=str(item.get("value_before", "") or "").strip(),
                value_after=str(item.get("value_after", "") or "").strip(),
                raw=dict(item),
            )

        decisions: list[OperationDecision] = []
        for idx, candidate in enumerate(chunk):
            decisions.append(
                indexed.get(
                    idx,
                    OperationDecision(
                        candidate=candidate,
                        operation="skip",
                        confidence=0.0,
                        reason="unclassified",
                    ),
                )
            )
        return decisions

    def _execute_decisions(
        self,
        e_new: Event,
        decisions: list[OperationDecision],
    ) -> ProcessResult:
        result = ProcessResult()
        if not decisions:
            return result

        max_links = max(0, int(getattr(self.config, "relation_max_links_per_event", 3) or 3))
        executed_links = 0
        ordered = sorted(decisions, key=lambda item: item.confidence, reverse=True)
        for decision in ordered:
            candidate = decision.candidate.event
            decision_log = {
                "candidate_id": candidate.id,
                "operation": decision.operation,
                "confidence": round(float(decision.confidence or 0.0), 4),
                "reason": decision.reason,
                "direction": decision.direction,
                "recall_channel": decision.candidate.channel,
                "recall_score": round(float(decision.candidate.channel_score or 0.0), 6),
                "aggregate_score": round(
                    float(decision.candidate.features.get("aggregate_score", 0.0) or 0.0),
                    6,
                ),
            }

            if decision.operation == "skip":
                result.skipped += 1
                decision_log["status"] = "skipped"
                result.decisions.append(decision_log)
                continue

            if candidate.status in {"merged", "archived", "ignored"}:
                result.skipped += 1
                decision_log["status"] = "candidate_inactive"
                result.decisions.append(decision_log)
                continue

            if executed_links >= max_links:
                result.skipped += 1
                decision_log["status"] = "relation_budget_exhausted"
                result.decisions.append(decision_log)
                continue

            operation_result = self._execute_relation(e_new=e_new, decision=decision)
            decision_log.update(operation_result.get("log", {}))
            decision_log["status"] = operation_result.get("status", "executed")
            result.total_links += int(operation_result.get("total_links", 0) or 0)
            result.updates += int(operation_result.get("updates", 0) or 0)
            result.links += int(operation_result.get("links", 0) or 0)
            result.skipped += int(operation_result.get("skipped", 0) or 0)
            result.decisions.append(decision_log)
            if operation_result.get("executed", False):
                executed_links += 1
        return result

    def _execute_relation(self, e_new: Event, decision: OperationDecision) -> dict[str, Any]:
        if decision.operation == "supersedes":
            return self._execute_supersedes(e_new=e_new, decision=decision)
        if decision.operation == "co_recall":
            return self._execute_co_recall(e_new=e_new, decision=decision)
        return {"executed": False, "skipped": 1, "status": "unknown_operation"}

    def _execute_supersedes(self, e_new: Event, decision: OperationDecision) -> dict[str, Any]:
        old_event, new_event = self._resolve_supersedes_direction(
            e_new=e_new,
            candidate=decision.candidate.event,
            direction=decision.direction,
        )
        created = self.store.upsert_event_relation(
            from_event_id=old_event.id,
            to_event_id=new_event.id,
            relation_type=REL_MEANING_UPDATE,
            operation="supersedes",
            description=decision.reason,
            confidence=decision.confidence,
            evidence_span="",
            value_before=decision.value_before,
            value_after=decision.value_after,
            recall_channel=decision.candidate.channel,
            recall_score=float(
                decision.candidate.features.get("aggregate_score", decision.candidate.channel_score) or 0.0
            ),
            source_episode_id=self._source_episode_id(old_event, new_event),
            source_session_id=self._source_session_id(old_event, new_event),
            timestamp=self._relation_timestamp(old_event, new_event),
        )
        return {
            "executed": True,
            "status": "executed",
            "updates": 1,
            "total_links": int(bool(created)),
            "log": {"from_event_id": old_event.id, "to_event_id": new_event.id},
        }

    def _execute_co_recall(self, e_new: Event, decision: OperationDecision) -> dict[str, Any]:
        from_event, to_event = self._resolve_link_direction(
            e_new=e_new,
            candidate=decision.candidate.event,
            direction=decision.direction,
        )
        created = self.store.upsert_event_relation(
            from_event_id=from_event.id,
            to_event_id=to_event.id,
            relation_type=REL_SHARED_CONTEXT,
            operation="co_recall",
            description=decision.reason,
            confidence=decision.confidence,
            evidence_span="",
            value_before="",
            value_after="",
            recall_channel=decision.candidate.channel,
            recall_score=float(
                decision.candidate.features.get("aggregate_score", decision.candidate.channel_score) or 0.0
            ),
            source_episode_id=self._source_episode_id(from_event, to_event),
            source_session_id=self._source_session_id(from_event, to_event),
            timestamp=self._relation_timestamp(from_event, to_event),
        )
        return {
            "executed": True,
            "status": "executed",
            "links": 1,
            "total_links": int(bool(created)),
            "log": {"from_event_id": from_event.id, "to_event_id": to_event.id},
        }

    def _state_update_decision(
        self,
        e_new: Event,
        candidate: RecallCandidate,
    ) -> Optional[OperationDecision]:
        if self._event_sort_key(candidate.event) >= self._event_sort_key(e_new):
            return None
        old_changes = self._state_changes(candidate.event)
        new_changes = self._state_changes(e_new)
        if not old_changes or not new_changes:
            return None
        old_by_key = {self._state_key(item): item for item in old_changes if self._state_key(item)}
        for item in new_changes:
            key = self._state_key(item)
            if not key or key not in old_by_key:
                continue
            old_item = old_by_key[key]
            old_after = str(old_item.get("value_after", old_item.get("value", "")) or "").strip()
            new_before = str(item.get("value_before", "") or "").strip()
            new_after = str(item.get("value_after", item.get("value", "")) or "").strip()
            if not new_after or new_after == old_after:
                continue
            if new_before and old_after and new_before != old_after:
                continue
            entity, attribute = key
            return OperationDecision(
                candidate=candidate,
                operation="supersedes",
                confidence=0.95,
                reason=f"新事件将{entity}的{attribute}从{old_after or '旧值'}更新为{new_after}，旧事件不能再作为当前状态依据。",
                direction="candidate_to_new",
                value_before=old_after or new_before,
                value_after=new_after,
            )
        return None

    def _explicit_reference_decision(
        self,
        e_new: Event,
        candidate: RecallCandidate,
    ) -> Optional[OperationDecision]:
        new_payload = e_new.payload if isinstance(e_new.payload, dict) else {}
        old_payload = candidate.event.payload if isinstance(candidate.event.payload, dict) else {}
        if str(new_payload.get("parent_event_id", "") or "").strip() == candidate.event.id:
            return OperationDecision(
                candidate=candidate,
                operation="co_recall",
                confidence=0.9,
                reason="新事件显式引用候选事件作为父事件，未来召回时需要保留这条上下文链。",
                direction="candidate_to_new",
            )
        if str(old_payload.get("parent_event_id", "") or "").strip() == e_new.id:
            return OperationDecision(
                candidate=candidate,
                operation="co_recall",
                confidence=0.9,
                reason="候选事件显式引用新事件作为父事件，未来召回时需要保留这条上下文链。",
                direction="new_to_candidate",
            )
        return None

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

    def _limit_llm_candidates(self, candidates: list[RecallCandidate]) -> list[RecallCandidate]:
        if not candidates:
            return []
        max_links = max(1, int(getattr(self.config, "relation_max_links_per_event", 3) or 3))
        limit = min(5, max_links + 2)
        ranked = sorted(
            candidates,
            key=lambda item: (
                self._candidate_priority(item),
                float(item.features.get("aggregate_score", 0.0) or 0.0),
                float(item.channel_score or 0.0),
                int(item.event.last_active or item.event.timestamp or 0),
            ),
            reverse=True,
        )
        return ranked[:limit]

    def _candidate_priority(self, candidate: RecallCandidate) -> int:
        channels = self._candidate_channels(candidate)
        if "reference" in channels:
            return 5
        if "state" in channels:
            return 4
        if "semantic" in channels and "entity" in channels:
            return 3
        if "semantic" in channels:
            return 2
        return 1

    def _needs_llm_judgement(self, candidate: RecallCandidate) -> bool:
        channels = self._candidate_channels(candidate)
        if "reference" in channels or "state" in channels:
            return True
        aggregate = float(candidate.features.get("aggregate_score", 0.0) or 0.0)
        semantic_score = float(channels.get("semantic", 0.0) or 0.0)
        return semantic_score >= 0.82 and aggregate >= 0.25

    def _is_entity_only_candidate(self, candidate: RecallCandidate) -> bool:
        channels = self._candidate_channels(candidate)
        return bool(channels) and set(channels.keys()) <= {"entity", "temporal"}

    def _candidate_channels(self, candidate: RecallCandidate) -> dict[str, float]:
        channels = candidate.features.get("channels", {})
        if isinstance(channels, dict) and channels:
            return {str(key): float(value or 0.0) for key, value in channels.items()}
        return {str(candidate.channel): float(candidate.channel_score or 0.0)}

    def _shared_context_ids(self, left: Event, right: Event) -> set[str]:
        return self._event_context_ids(left) & self._event_context_ids(right)

    def _event_context_ids(self, event: Event) -> set[str]:
        try:
            contexts = self.store.get_event_contexts(event.id)
        except Exception:
            contexts = []
        ids: set[str] = set()
        for item in contexts or []:
            context_id = getattr(item, "id", None)
            if context_id:
                ids.add(str(context_id))
            elif isinstance(item, dict) and item.get("id"):
                ids.add(str(item["id"]))
            elif isinstance(item, str):
                ids.add(item)
        return ids

    def _event_entity_ids(self, event: Event) -> set[str]:
        try:
            entity_ids = self.store.get_event_entities(event.id)
        except Exception:
            entity_ids = []
        payload = event.payload if isinstance(event.payload, dict) else {}
        if not entity_ids:
            raw_ids = payload.get("entity_ids", [])
            if isinstance(raw_ids, list):
                entity_ids = raw_ids
        return {str(item or "").strip() for item in entity_ids if str(item or "").strip()}

    def _state_changes(self, event: Event) -> list[dict[str, Any]]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        raw = payload.get("state_changes", [])
        return [item for item in raw if isinstance(item, dict)]

    def _state_key(self, item: dict[str, Any]) -> Optional[tuple[str, str]]:
        entity = str(item.get("entity", "") or "").strip()
        attribute = str(item.get("attribute", "") or "").strip()
        if not entity or not attribute:
            return None
        return entity, attribute

    def _resolve_link_direction(
        self,
        e_new: Event,
        candidate: Event,
        direction: str,
    ) -> tuple[Event, Event]:
        normalized = str(direction or "").strip().lower()
        if normalized == "new_to_candidate":
            return e_new, candidate
        if normalized == "candidate_to_new":
            return candidate, e_new
        ordered = sorted(
            [candidate, e_new],
            key=self._event_sort_key,
        )
        return ordered[0], ordered[1]

    def _resolve_supersedes_direction(
        self,
        e_new: Event,
        candidate: Event,
        direction: str,
    ) -> tuple[Event, Event]:
        normalized = str(direction or "").strip().lower()
        if normalized == "new_to_candidate":
            return e_new, candidate
        if normalized == "candidate_to_new":
            return candidate, e_new
        ordered = sorted(
            [candidate, e_new],
            key=self._event_sort_key,
        )
        return ordered[0], ordered[1]

    def _event_sort_key(self, event: Event) -> tuple[int, str]:
        return (int(event.timestamp or event.last_active or event.created_at or 0), str(event.id or ""))

    def _event_prompt_payload(self, event: Event) -> dict[str, Any]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        return {
            "id": event.id,
            "summary": event.summary,
            "action": event.action,
            "causality": event.causality,
            "timestamp": event.timestamp,
            "last_active": event.last_active,
            "participants": event.participants,
            "payload": payload,
            "support_count": event.support_count,
            "context_ids": sorted(self._event_context_ids(event)),
            "entity_ids": sorted(self._event_entity_ids(event)),
        }

    def _source_episode_id(self, left: Event, right: Event) -> str:
        for event in (left, right):
            payload = event.payload if isinstance(event.payload, dict) else {}
            value = str(payload.get("episode_id", "") or "").strip()
            if value:
                return value
        return ""

    def _source_session_id(self, left: Event, right: Event) -> str:
        for event in (left, right):
            payload = event.payload if isinstance(event.payload, dict) else {}
            value = str(payload.get("session_id", "") or "").strip()
            if value:
                return value
        return ""

    def _relation_timestamp(self, left: Event, right: Event) -> int:
        return max(
            int(time.time()),
            int(left.last_active or left.timestamp or 0),
            int(right.last_active or right.timestamp or 0),
        )

    def _coerce_confidence(self, value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            confidence = 0.0
        return max(0.0, min(1.0, confidence))

    def _merge_event_pair(
        self,
        canonical: Event,
        merged: Event,
        similarity_score: float,
        merge_reason: str,
        merged_at: int,
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
        merged.payload = self._merge_event_payload(
            merged.payload,
            {},
            canonical.id,
            merged_at,
            source_event_id=merged.id,
        )
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
        base = self._merge_payload_values(
            copy.deepcopy(payload or {}),
            copy.deepcopy(incoming_payload or {}),
        )
        if incoming_payload:
            base.setdefault("merge_inputs", []).append(copy.deepcopy(incoming_payload))
        merge_trace = base.setdefault("merge_trace", [])
        trace_entry = {
            "target_event_id": target_event_id,
            "merged_at": merged_at,
        }
        source_id = source_event_id or base.get("source_event_id")
        if source_id:
            trace_entry["source_event_id"] = source_id
        merge_trace.append(trace_entry)
        return base

    def _merge_payload_values(
        self,
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        merged = copy.deepcopy(existing or {})
        for key, value in (incoming or {}).items():
            if key not in merged or merged.get(key) in (None, "", [], {}):
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
        rewritten = self._llm_rewrite_merged_event(canonical, merged)
        if rewritten:
            summary = str(rewritten.get("summary", "") or "").strip()
            action = str(rewritten.get("action", "") or "").strip()
            causality = str(rewritten.get("causality", "") or "").strip()
            if summary:
                return {
                    "summary": summary[:180],
                    "action": action[:120],
                    "causality": causality[:120],
                }
        return self._fallback_merged_event_semantics(canonical, merged)

    def _llm_rewrite_merged_event(
        self,
        canonical: Event,
        merged: Event,
    ) -> Optional[dict[str, Any]]:
        if callable(self._rewrite_merge_callback):
            try:
                rewritten = self._rewrite_merge_callback(canonical, merged)
            except Exception:
                rewritten = None
            if isinstance(rewritten, dict):
                return rewritten
        if not self._rewrite_merged_event_system_prompt or not self._rewrite_merged_event_user_prompt:
            return None
        payload = {
            "canonical": self._event_prompt_payload(canonical),
            "merged": self._event_prompt_payload(merged),
            "output_schema": {
                "summary": "rewritten summary",
                "action": "rewritten action",
                "causality": "rewritten causality",
            },
        }
        try:
            response = self.llm_client.call_generation(
                model=self.config.llm_model,
                messages=self.llm_client.build_messages(
                    self._rewrite_merged_event_system_prompt,
                    self._rewrite_merged_event_user_prompt.format(
                        payload_json=json.dumps(payload, ensure_ascii=False)
                    ),
                ),
            )
            data = robust_json_loads(self.llm_client.message_content(response), None)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _fallback_merged_event_semantics(
        self,
        canonical: Event,
        merged: Event,
    ) -> dict[str, str]:
        summary = self._merge_event_summary(canonical.summary, merged.summary)[:180]
        action = str(canonical.action or merged.action or summary).strip()[:120]
        causality = str(canonical.causality or merged.causality or "").strip()[:120]
        return {
            "summary": summary,
            "action": action,
            "causality": causality,
        }

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

    def _merge_slots(self, a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        merged = dict(a)
        for key, value in b.items():
            if value not in (None, "", [], {}):
                merged[key] = value
        return merged

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
