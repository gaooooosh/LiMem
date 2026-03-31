# -*- coding: utf-8 -*-
"""Rule-based event relationship inference."""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import re

from ..core.event import Event


@dataclass
class InferredRelation:
    from_event_id: str
    to_event_id: str
    relation_type: str
    description: str
    confidence: float
    evidence_span: str = ""


class RelationshipInferrer:
    """Infer temporal, causal and parallel event relations without LLMs."""

    def __init__(self, *, causal_time_window_seconds: int = 3600):
        self.causal_time_window_seconds = int(causal_time_window_seconds)

    def infer(self, events: list[Event]) -> list[InferredRelation]:
        ordered = [event for event in events if isinstance(event, Event) and event.id]
        if len(ordered) < 2:
            return []

        ordered.sort(key=lambda event: (self._event_start(event), event.timestamp, event.id))
        relations: list[InferredRelation] = []

        relations.extend(self._infer_temporal_next(ordered))
        relations.extend(self._infer_causality(ordered))
        relations.extend(self._infer_parallel(ordered))
        return self._dedupe(relations)

    def _infer_temporal_next(self, events: list[Event]) -> list[InferredRelation]:
        relations: list[InferredRelation] = []
        for left, right in zip(events, events[1:]):
            if left.id == right.id:
                continue
            relations.append(
                InferredRelation(
                    from_event_id=left.id,
                    to_event_id=right.id,
                    relation_type="temporal_next",
                    description=f"{left.summary or left.action} happens before {right.summary or right.action}",
                    confidence=0.65,
                    evidence_span=f"{left.summary or left.action} -> {right.summary or right.action}",
                )
            )
        return relations

    def _infer_causality(self, events: list[Event]) -> list[InferredRelation]:
        relations: list[InferredRelation] = []
        for cause, effect in itertools.permutations(events, 2):
            if cause.id == effect.id:
                continue
            if self._event_start(effect) < self._event_start(cause):
                continue
            if not self._within_causal_window(cause, effect):
                continue
            overlap = self._text_overlap(
                cause.action or cause.summary,
                effect.causality or effect.summary,
            )
            if overlap < 0.35:
                continue
            relations.append(
                InferredRelation(
                    from_event_id=cause.id,
                    to_event_id=effect.id,
                    relation_type="causality",
                    description=f"{effect.summary or effect.action} references {cause.action or cause.summary}",
                    confidence=min(0.9, 0.45 + overlap),
                    evidence_span=effect.causality or effect.summary,
                )
            )
        return relations

    def _infer_parallel(self, events: list[Event]) -> list[InferredRelation]:
        relations: list[InferredRelation] = []
        for left, right in itertools.combinations(events, 2):
            if not self._time_ranges_overlap(left, right):
                continue
            left_roles = self._participant_roles(left)
            right_roles = self._participant_roles(right)
            if left_roles and right_roles and left_roles == right_roles:
                continue
            relations.append(
                InferredRelation(
                    from_event_id=left.id,
                    to_event_id=right.id,
                    relation_type="parallel",
                    description=f"{left.summary or left.action} overlaps with {right.summary or right.action}",
                    confidence=0.6,
                    evidence_span=f"{left.summary or left.action} || {right.summary or right.action}",
                )
            )
        return relations

    def _event_start(self, event: Event) -> int:
        if isinstance(event.time_range, dict):
            start = int(event.time_range.get("start", 0) or 0)
            if start > 0:
                return start
        return int(event.timestamp or event.last_active or 0)

    def _event_end(self, event: Event) -> int:
        if isinstance(event.time_range, dict):
            end = int(event.time_range.get("end", 0) or 0)
            if end > 0:
                return end
        return self._event_start(event)

    def _within_causal_window(self, left: Event, right: Event) -> bool:
        left_end = self._event_end(left)
        right_start = self._event_start(right)
        if left_end <= 0 or right_start <= 0:
            return False
        return 0 <= right_start - left_end <= self.causal_time_window_seconds

    def _time_ranges_overlap(self, left: Event, right: Event) -> bool:
        left_start = self._event_start(left)
        right_start = self._event_start(right)
        left_end = self._event_end(left)
        right_end = self._event_end(right)
        if min(left_start, right_start, left_end, right_end) <= 0:
            return False
        return max(left_start, right_start) <= min(left_end, right_end)

    def _participant_roles(self, event: Event) -> set[str]:
        roles: set[str] = set()
        for item in event.participants or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "") or "").strip().lower()
            if role:
                roles.add(role)
        return roles

    def _text_overlap(self, left: str, right: str) -> float:
        left_tokens = self._tokenize(left)
        right_tokens = self._tokenize(right)
        if not left_tokens or not right_tokens:
            return 0.0
        intersection = len(left_tokens & right_tokens)
        return intersection / max(1, min(len(left_tokens), len(right_tokens)))

    def _tokenize(self, text: str) -> set[str]:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return set()
        latin_tokens = set(re.findall(r"[a-z0-9]+", normalized))
        cjk_runs = re.findall(r"[\u4e00-\u9fff]+", normalized)
        cjk_tokens: set[str] = set()
        for run in cjk_runs:
            if len(run) <= 4:
                cjk_tokens.add(run)
            for index in range(len(run) - 1):
                cjk_tokens.add(run[index:index + 2])
        return latin_tokens | cjk_tokens

    def _dedupe(self, relations: list[InferredRelation]) -> list[InferredRelation]:
        deduped: list[InferredRelation] = []
        seen: set[tuple[str, str, str]] = set()
        for relation in relations:
            signature = (
                relation.from_event_id,
                relation.to_event_id,
                relation.relation_type,
            )
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(relation)
        return deduped
