# -*- coding: utf-8 -*-
"""Batch relation classification and operation execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional
import copy
import json
import logging
import os
import time
import uuid

from ..core.event import Event
from ..utils import hash_summary, load_prompt, robust_json_loads, safe_json_dumps
from .recall_pipeline import CandidateSet, RecallCandidate

logger = logging.getLogger(__name__)


@dataclass
class OperationDecision:
    candidate: RecallCandidate
    operation: str
    confidence: float
    reason: str
    link_subtype: str = ""
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
    """Classify recalled event pairs and execute merge/update/link operations."""

    _STRUCTURAL_OPS = {"update", "extend", "derive", "merge"}

    def __init__(self, store: Any, llm_client: Any, config: Any):
        self.store = store
        self.llm_client = llm_client
        self.config = config
        self._classify_relations_system_prompt = load_prompt("classify_relations_system.txt")
        self._classify_relations_user_prompt = load_prompt("classify_relations_user.txt")
        self._fuse_event_system_prompt = load_prompt("fuse_event_system.txt")
        self._fuse_event_user_prompt = load_prompt("fuse_event_user.txt")
        self._derive_event_system_prompt = load_prompt("derive_event_system.txt")
        self._derive_event_user_prompt = load_prompt("derive_event_user.txt")
        self._rewrite_merged_event_system_prompt = load_prompt("rewrite_merged_event_system.txt")
        self._rewrite_merged_event_user_prompt = load_prompt("rewrite_merged_event_user.txt")

        self._legacy_relation_call: Optional[Callable[[dict[str, Any]], Optional[dict[str, Any]]]] = None
        self._legacy_relation_payload: Optional[
            Callable[[Event, Event, str], dict[str, Any]]
        ] = None
        self._legacy_relation_enabled: Optional[Callable[[], bool]] = None
        self._rewrite_merge_callback: Optional[
            Callable[[Event, Event], Optional[dict[str, Any]]]
        ] = None

    def bind_engine(self, engine: Any) -> None:
        self._legacy_relation_call = lambda payload: engine._call_relation_llm(payload)
        self._legacy_relation_payload = (
            lambda left, right, source_text: engine._relation_prompt_payload(
                left=left,
                right=right,
                source_text=source_text,
            )
        )
        engine_type = type(engine)
        self._legacy_relation_enabled = lambda: self._method_is_overridden(
            engine,
            "_call_relation_llm",
            getattr(engine_type, "_call_relation_llm", None),
        )
        self._rewrite_merge_callback = lambda canonical, merged: engine._llm_rewrite_merged_event(
            canonical,
            merged,
        )

    def process(self, e_new: Event, candidates: CandidateSet, source_text: str) -> ProcessResult:
        decisions = self._classify_batch(e_new=e_new, candidates=candidates, source_text=source_text)
        return self._execute_decisions(e_new=e_new, decisions=decisions)

    def _classify_batch(
        self,
        e_new: Event,
        candidates: CandidateSet,
        source_text: str,
    ) -> list[OperationDecision]:
        if not candidates.candidates:
            return []

        if self._should_use_legacy_relation_compat():
            return self._classify_batch_legacy(
                e_new=e_new,
                candidates=candidates,
                source_text=source_text,
            )

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

    def _classify_batch_legacy(
        self,
        e_new: Event,
        candidates: CandidateSet,
        source_text: str,
    ) -> list[OperationDecision]:
        if not callable(self._legacy_relation_call) or not callable(self._legacy_relation_payload):
            return []
        decisions: list[OperationDecision] = []
        for candidate in candidates.candidates:
            payload = self._legacy_relation_payload(e_new, candidate.event, source_text)
            raw = self._legacy_relation_call(payload) or {}
            if not isinstance(raw, dict) or not bool(raw.get("should_link", False)):
                decisions.append(
                    OperationDecision(
                        candidate=candidate,
                        operation="skip",
                        confidence=0.0,
                        reason="legacy_skip",
                        raw=raw if isinstance(raw, dict) else {},
                    )
                )
                continue
            decisions.append(
                OperationDecision(
                    candidate=candidate,
                    operation="link",
                    confidence=self._coerce_confidence(raw.get("confidence")),
                    reason=str(raw.get("reason", "") or "").strip(),
                    link_subtype=str(raw.get("relation_type", "") or "").strip(),
                    direction=self._legacy_direction(
                        e_new=e_new,
                        candidate=candidate.event,
                        raw=raw,
                    ),
                    raw=dict(raw),
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
                        "operation": "link",
                        "confidence": 0.8,
                        "reason": "简短原因",
                        "direction": "candidate_to_new",
                        "link_subtype": "促成",
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
            if operation not in {"update", "extend", "derive", "merge", "link", "skip"}:
                operation = "skip"
            confidence = self._coerce_confidence(item.get("confidence"))
            if confidence < min_confidence:
                operation = "skip"
            if operation == "derive" and not bool(getattr(self.config, "enable_derive_operation", False)):
                operation = "skip"
            decision = OperationDecision(
                candidate=chunk[index],
                operation=operation,
                confidence=confidence,
                reason=str(item.get("reason", "") or "").strip(),
                link_subtype=str(item.get("link_subtype", "") or "").strip(),
                direction=str(item.get("direction", "") or "").strip(),
                value_before=str(item.get("value_before", "") or "").strip(),
                value_after=str(item.get("value_after", "") or "").strip(),
                raw=dict(item),
            )
            indexed[index] = decision

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

        structural_executed = False
        derivations_used = 0
        ordered = sorted(decisions, key=lambda item: item.confidence, reverse=True)
        for decision in ordered:
            candidate = decision.candidate.event
            decision_log = {
                "candidate_id": candidate.id,
                "operation": decision.operation,
                "confidence": round(float(decision.confidence or 0.0), 4),
                "reason": decision.reason,
                "link_subtype": decision.link_subtype,
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

            if candidate.status in {"merged", "archived"}:
                result.skipped += 1
                decision_log["status"] = "candidate_inactive"
                result.decisions.append(decision_log)
                continue

            if decision.operation in self._STRUCTURAL_OPS:
                if structural_executed:
                    result.skipped += 1
                    decision_log["status"] = "structural_conflict"
                    result.decisions.append(decision_log)
                    continue
                if decision.operation == "derive":
                    max_derivations = max(
                        0,
                        int(getattr(self.config, "max_derivations_per_batch", 3) or 3),
                    )
                    if derivations_used >= max_derivations:
                        result.skipped += 1
                        decision_log["status"] = "derive_budget_exhausted"
                        result.decisions.append(decision_log)
                        continue
                    derivations_used += 1

            operation_result = self._execute_operation(e_new=e_new, decision=decision)
            decision_log.update(operation_result.get("log", {}))
            decision_log["status"] = operation_result.get("status", "executed")
            result.total_links += int(operation_result.get("total_links", 0) or 0)
            result.updates += int(operation_result.get("updates", 0) or 0)
            result.extensions += int(operation_result.get("extensions", 0) or 0)
            result.derivations += int(operation_result.get("derivations", 0) or 0)
            result.merges += int(operation_result.get("merges", 0) or 0)
            result.links += int(operation_result.get("links", 0) or 0)
            result.skipped += int(operation_result.get("skipped", 0) or 0)
            result.decisions.append(decision_log)

            if decision.operation in self._STRUCTURAL_OPS and operation_result.get("executed", False):
                structural_executed = True
            if e_new.status in {"merged", "archived"}:
                break
        return result

    def _execute_operation(
        self,
        e_new: Event,
        decision: OperationDecision,
    ) -> dict[str, Any]:
        handlers = {
            "update": self._execute_update,
            "extend": self._execute_extend,
            "derive": self._execute_derive,
            "merge": self._execute_merge,
            "link": self._execute_link,
        }
        handler = handlers.get(decision.operation)
        if handler is None:
            return {"executed": False, "skipped": 1, "status": "unknown_operation"}
        return handler(e_new=e_new, decision=decision)

    def _execute_update(self, e_new: Event, decision: OperationDecision) -> dict[str, Any]:
        return self._execute_version_operation(
            mode="update",
            e_new=e_new,
            decision=decision,
            relation_type="更新",
            source_relation_type="更新源",
        )

    def _execute_extend(self, e_new: Event, decision: OperationDecision) -> dict[str, Any]:
        return self._execute_version_operation(
            mode="extend",
            e_new=e_new,
            decision=decision,
            relation_type="补充",
            source_relation_type="补充源",
        )

    def _execute_version_operation(
        self,
        mode: str,
        e_new: Event,
        decision: OperationDecision,
        relation_type: str,
        source_relation_type: str,
    ) -> dict[str, Any]:
        old_event = decision.candidate.event
        fused = self._fuse_event(mode=mode, old_event=old_event, new_event=e_new)
        now = max(
            int(time.time()),
            int(old_event.last_active or old_event.timestamp or 0),
            int(e_new.last_active or e_new.timestamp or 0),
        )
        version_event = self._build_version_event(
            mode=mode,
            old_event=old_event,
            new_event=e_new,
            fused=fused,
            timestamp=now,
        )
        self.store.save_event(version_event)
        self.store.archive_event(old_event.id, now)
        old_event.status = "archived"
        old_event.valid_to = now
        old_event.updated_at = now
        self.store.relink_event_references(
            source_event_id=old_event.id,
            target_event_id=version_event.id,
            timestamp=now,
        )
        created_links = 0
        created_links += int(
            bool(
                self.store.upsert_event_relation(
                    from_event_id=old_event.id,
                    to_event_id=version_event.id,
                    relation_type=relation_type,
                    operation=mode,
                    description=decision.reason,
                    confidence=decision.confidence,
                    evidence_span="",
                    value_before=decision.value_before,
                    value_after=decision.value_after,
                    recall_channel=decision.candidate.channel,
                    recall_score=float(
                        decision.candidate.features.get("aggregate_score", decision.candidate.channel_score)
                        or 0.0
                    ),
                    source_episode_id=self._source_episode_id(old_event, e_new),
                    source_session_id=self._source_session_id(old_event, e_new),
                    timestamp=now,
                )
            )
        )
        created_links += int(
            bool(
                self.store.upsert_event_relation(
                    from_event_id=e_new.id,
                    to_event_id=version_event.id,
                    relation_type=source_relation_type,
                    operation=mode,
                    description=decision.reason,
                    confidence=decision.confidence,
                    evidence_span="",
                    value_before=decision.value_before,
                    value_after=decision.value_after,
                    recall_channel=decision.candidate.channel,
                    recall_score=float(
                        decision.candidate.features.get("aggregate_score", decision.candidate.channel_score)
                        or 0.0
                    ),
                    source_episode_id=self._source_episode_id(old_event, e_new),
                    source_session_id=self._source_session_id(old_event, e_new),
                    timestamp=now,
                )
            )
        )
        counter_key = "updates" if mode == "update" else "extensions"
        return {
            "executed": True,
            "status": "executed",
            counter_key: 1,
            "total_links": created_links,
            "log": {"version_event_id": version_event.id},
        }

    def _execute_derive(self, e_new: Event, decision: OperationDecision) -> dict[str, Any]:
        del e_new, decision
        logger.info("derive operation is not enabled yet; skipping")
        return {
            "executed": False,
            "skipped": 1,
            "status": "derive_stub",
        }

    def _execute_merge(self, e_new: Event, decision: OperationDecision) -> dict[str, Any]:
        candidate = decision.candidate.event
        canonical, merged = self._pick_canonical_event(e_new, candidate)
        now = max(
            int(time.time()),
            int(canonical.last_active or canonical.timestamp or 0),
            int(merged.last_active or merged.timestamp or 0),
        )
        merge_reason = self._build_event_merge_reason(
            source="relation_classification",
            local_reason=decision.reason,
            embedding_similarity=0.0,
            llm_reason="",
            strategy="relation_pipeline",
        )
        self._merge_event_pair(
            canonical=canonical,
            merged=merged,
            similarity_score=decision.confidence,
            merge_reason=merge_reason,
            merged_at=now,
        )
        created = self.store.upsert_event_relation(
            from_event_id=merged.id,
            to_event_id=canonical.id,
            relation_type="合并",
            operation="merge",
            description=decision.reason,
            confidence=decision.confidence,
            evidence_span="",
            value_before="",
            value_after="",
            recall_channel=decision.candidate.channel,
            recall_score=float(
                decision.candidate.features.get("aggregate_score", decision.candidate.channel_score) or 0.0
            ),
            source_episode_id=self._source_episode_id(e_new, candidate),
            source_session_id=self._source_session_id(e_new, candidate),
            timestamp=now,
        )
        if merged.id == e_new.id:
            e_new.status = "merged"
            e_new.valid_to = now
            e_new.updated_at = now
        return {
            "executed": True,
            "status": "executed",
            "merges": 1,
            "total_links": int(bool(created)),
            "log": {
                "canonical_event_id": canonical.id,
                "merged_event_id": merged.id,
            },
        }

    def _execute_link(self, e_new: Event, decision: OperationDecision) -> dict[str, Any]:
        if not decision.link_subtype:
            return {"executed": False, "skipped": 1, "status": "missing_link_subtype"}
        candidate = decision.candidate.event
        from_event, to_event = self._resolve_link_direction(
            e_new=e_new,
            candidate=candidate,
            direction=decision.direction,
        )
        created = self.store.upsert_event_relation(
            from_event_id=from_event.id,
            to_event_id=to_event.id,
            relation_type=decision.link_subtype,
            operation="link",
            description=decision.reason,
            confidence=decision.confidence,
            evidence_span="",
            value_before=decision.value_before,
            value_after=decision.value_after,
            recall_channel=decision.candidate.channel,
            recall_score=float(
                decision.candidate.features.get("aggregate_score", decision.candidate.channel_score) or 0.0
            ),
            source_episode_id=self._source_episode_id(e_new, candidate),
            source_session_id=self._source_session_id(e_new, candidate),
            timestamp=max(
                int(time.time()),
                int(from_event.last_active or from_event.timestamp or 0),
                int(to_event.last_active or to_event.timestamp or 0),
            ),
        )
        return {
            "executed": True,
            "status": "executed",
            "links": 1,
            "total_links": int(bool(created)),
            "log": {"from_event_id": from_event.id, "to_event_id": to_event.id},
        }

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
            key=lambda item: (int(item.timestamp or item.last_active or 0), item.id),
        )
        return ordered[0], ordered[1]

    def _fuse_event(self, mode: str, old_event: Event, new_event: Event) -> dict[str, str]:
        if not self._fuse_event_system_prompt or not self._fuse_event_user_prompt:
            return self._fallback_fused_semantics(mode=mode, old_event=old_event, new_event=new_event)
        payload = {
            "mode": mode,
            "existing_event": self._event_prompt_payload(old_event),
            "incoming_event": self._event_prompt_payload(new_event),
            "output_schema": {
                "summary": "融合后的事件摘要",
                "action": "融合后的主动作",
                "causality": "融合后的结果或影响",
            },
        }
        user_message = self._fuse_event_user_prompt.format(
            payload_json=json.dumps(payload, ensure_ascii=False)
        )
        try:
            response = self.llm_client.call_generation(
                model=self.config.llm_model,
                messages=self.llm_client.build_messages(
                    self._fuse_event_system_prompt,
                    user_message,
                ),
            )
            raw = robust_json_loads(self.llm_client.message_content(response), {})
        except Exception:
            logger.warning("event fusion failed", exc_info=True)
            raw = {}
        if not isinstance(raw, dict):
            return self._fallback_fused_semantics(mode=mode, old_event=old_event, new_event=new_event)
        summary = str(raw.get("summary", "") or "").strip()
        action = str(raw.get("action", "") or "").strip()
        causality = str(raw.get("causality", "") or "").strip()
        if not summary:
            return self._fallback_fused_semantics(mode=mode, old_event=old_event, new_event=new_event)
        return {
            "summary": summary[:180],
            "action": action[:120],
            "causality": causality[:120],
        }

    def _fallback_fused_semantics(self, mode: str, old_event: Event, new_event: Event) -> dict[str, str]:
        if mode == "update":
            summary = str(new_event.summary or old_event.summary or "").strip()
            action = str(new_event.action or old_event.action or summary).strip()
            causality = str(new_event.causality or old_event.causality or "").strip()
            return {
                "summary": summary[:180],
                "action": action[:120],
                "causality": causality[:120],
            }
        summary = self._merge_event_summary(old_event.summary, new_event.summary)[:180]
        action = str(new_event.action or old_event.action or summary).strip()[:120]
        causality = str(new_event.causality or old_event.causality or "").strip()[:120]
        return {
            "summary": summary,
            "action": action,
            "causality": causality,
        }

    def _build_version_event(
        self,
        mode: str,
        old_event: Event,
        new_event: Event,
        fused: dict[str, str],
        timestamp: int,
    ) -> Event:
        payload = self._merge_payload_values(
            copy.deepcopy(old_event.payload if isinstance(old_event.payload, dict) else {}),
            copy.deepcopy(new_event.payload if isinstance(new_event.payload, dict) else {}),
        )
        payload["parent_event_id"] = old_event.id
        payload["version_source"] = sorted({old_event.id, new_event.id})
        payload["version_mode"] = mode
        payload["summary"] = fused.get("summary", "") or old_event.summary
        payload["action"] = fused.get("action", "") or old_event.action
        payload["causality"] = fused.get("causality", "") or old_event.causality
        base = f"{old_event.id}|{mode}|{fused.get('summary', old_event.summary)}"
        event_id = f"evt_{hash_summary(base)[:20]}_{timestamp}_{uuid.uuid4().hex[:6]}"
        return Event(
            id=event_id,
            summary=fused.get("summary", "") or old_event.summary or new_event.summary,
            action=fused.get("action", "") or old_event.action or new_event.action,
            causality=fused.get("causality", "") or old_event.causality or new_event.causality,
            time_range=self._merge_slots(
                old_event.time_range if isinstance(old_event.time_range, dict) else {},
                new_event.time_range if isinstance(new_event.time_range, dict) else {},
            ),
            timestamp=min(
                int(old_event.timestamp or timestamp),
                int(new_event.timestamp or timestamp),
            ),
            last_active=max(
                int(old_event.last_active or old_event.timestamp or timestamp),
                int(new_event.last_active or new_event.timestamp or timestamp),
                int(timestamp),
            ),
            created_at=timestamp,
            updated_at=timestamp,
            valid_from=timestamp,
            participants=self._merge_list_values(old_event.participants, new_event.participants),
            payload=payload,
            evidence=self._merge_list_values(old_event.evidence, new_event.evidence),
            status="active",
            support_count=max(1, int(old_event.support_count or 1)) + 1,
            embedding=old_event.embedding or new_event.embedding,
        )

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
            "context_ids": [context.id for context in self.store.get_event_contexts(event.id)],
            "entity_ids": list(self.store.get_event_entities(event.id)),
        }

    def _legacy_direction(self, e_new: Event, candidate: Event, raw: dict[str, Any]) -> str:
        from_id = str(raw.get("from_id", "") or "").strip()
        to_id = str(raw.get("to_id", "") or "").strip()
        if from_id == e_new.id and to_id == candidate.id:
            return "new_to_candidate"
        if from_id == candidate.id and to_id == e_new.id:
            return "candidate_to_new"
        return ""

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

    def _should_use_legacy_relation_compat(self) -> bool:
        return bool(callable(self._legacy_relation_enabled) and self._legacy_relation_enabled())

    def _coerce_confidence(self, value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            confidence = 0.0
        return max(0.0, min(1.0, confidence))

    @staticmethod
    def _method_is_overridden(instance: Any, name: str, default: Any) -> bool:
        current = getattr(instance, name, None)
        if current is None or default is None:
            return False
        func = getattr(current, "__func__", None)
        if func is not None:
            return func is not default
        return current is not default

    def _pick_canonical_event(self, event_a: Event, event_b: Event) -> tuple[Event, Event]:
        def rank_key(event: Event) -> tuple[int, int, int]:
            return (
                int(event.support_count or 1),
                1 if str(event.summary or "").strip() else 0,
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
