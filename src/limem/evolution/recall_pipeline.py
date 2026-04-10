# -*- coding: utf-8 -*-
"""Multi-channel event recall pipeline."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional
import math
import time

from ..core.event import Event


@dataclass
class RecallCandidate:
    event: Event
    channel: str
    channel_score: float
    features: dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateSet:
    candidates: list[RecallCandidate]
    channel_stats: dict[str, int]


class RecallPipeline:
    """Recall event candidates from multiple retrieval channels."""

    _CHANNELS = ("temporal", "entity", "semantic", "state", "reference")

    def __init__(self, store: Any, config: Any):
        self.store = store
        self.config = config

    def recall(
        self,
        event: Event,
        intra_batch_events: Optional[list[Event]] = None,
    ) -> CandidateSet:
        now = int(event.last_active or event.timestamp or time.time())
        channel_results = self._run_channels(event=event, current_time=now)
        if intra_batch_events:
            channel_results["temporal"] = list(channel_results.get("temporal", []))
            for prior_event in intra_batch_events:
                if not isinstance(prior_event, Event):
                    continue
                channel_results["temporal"].append(
                    RecallCandidate(
                        event=prior_event,
                        channel="temporal",
                        channel_score=1.0,
                        features={
                            "source": "intra_batch",
                            "delta_seconds": max(
                                0,
                                now - int(prior_event.last_active or prior_event.timestamp or now),
                            ),
                        },
                    )
                )
        return self._merge_candidates(
            event=event,
            channel_results=channel_results,
        )

    def _run_channels(
        self,
        event: Event,
        current_time: int,
    ) -> dict[str, list[RecallCandidate]]:
        methods = {
            "temporal": lambda: self._recall_temporal(event, current_time=current_time),
            "entity": lambda: self._recall_entity(event, current_time=current_time),
            "semantic": lambda: self._recall_semantic(event),
            "state": lambda: self._recall_state(event),
            "reference": lambda: self._recall_reference(event),
        }
        results: dict[str, list[RecallCandidate]] = {name: [] for name in self._CHANNELS}
        with ThreadPoolExecutor(max_workers=len(methods)) as pool:
            futures = {pool.submit(method): name for name, method in methods.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    payload = future.result() or []
                except Exception:
                    payload = []
                results[name] = [item for item in payload if isinstance(item, RecallCandidate)]
        return results

    def _merge_candidates(
        self,
        event: Event,
        channel_results: dict[str, list[RecallCandidate]],
    ) -> CandidateSet:
        merged: dict[str, RecallCandidate] = {}
        channel_stats = {
            channel: len(channel_results.get(channel, []))
            for channel in self._CHANNELS
        }
        weights = self._channel_weights()
        for channel, items in channel_results.items():
            for candidate in items:
                candidate_event = candidate.event
                if not isinstance(candidate_event, Event):
                    continue
                if candidate_event.id == event.id:
                    continue
                if candidate_event.status in {"merged", "archived"}:
                    continue
                key = str(candidate_event.id or "").strip()
                if not key:
                    continue
                existing = merged.get(key)
                if existing is None:
                    merged[key] = RecallCandidate(
                        event=candidate_event,
                        channel=candidate.channel,
                        channel_score=float(candidate.channel_score or 0.0),
                        features={
                            "aggregate_score": 0.0,
                            "channels": {channel: float(candidate.channel_score or 0.0)},
                            "channel_features": {channel: dict(candidate.features or {})},
                        },
                    )
                    existing = merged[key]
                else:
                    if float(candidate.channel_score or 0.0) > float(existing.channel_score or 0.0):
                        existing.channel = candidate.channel
                        existing.channel_score = float(candidate.channel_score or 0.0)
                    existing.features.setdefault("channels", {})[channel] = float(
                        candidate.channel_score or 0.0
                    )
                    existing.features.setdefault("channel_features", {})[channel] = dict(
                        candidate.features or {}
                    )

        candidates: list[RecallCandidate] = []
        for candidate in merged.values():
            channel_scores = candidate.features.get("channels", {})
            aggregate_score = 0.0
            for channel, score in channel_scores.items():
                aggregate_score += float(weights.get(channel, 0.0)) * float(score or 0.0)
            candidate.features["aggregate_score"] = round(float(aggregate_score), 6)
            candidates.append(candidate)

        candidates.sort(
            key=lambda item: (
                float(item.features.get("aggregate_score", 0.0) or 0.0),
                float(item.channel_score or 0.0),
                int(item.event.last_active or item.event.timestamp or 0),
            ),
            reverse=True,
        )
        max_candidates = max(1, int(getattr(self.config, "recall_max_candidates", 30) or 30))
        return CandidateSet(
            candidates=candidates[:max_candidates],
            channel_stats=channel_stats,
        )

    def _recall_temporal(self, event: Event, current_time: int) -> list[RecallCandidate]:
        window = max(1, int(getattr(self.config, "recall_temporal_window", 1) or 1))
        limit = max(1, int(getattr(self.config, "recall_temporal_limit", 20) or 20))
        recent_events = self.store.get_recent_events(
            current_time=current_time,
            window_seconds=window,
            limit=max(limit * 3, limit),
        )
        results: list[RecallCandidate] = []
        for candidate in recent_events:
            if candidate.id == event.id:
                continue
            ts = int(candidate.last_active or candidate.timestamp or current_time)
            delta = max(0, current_time - ts)
            score = math.exp(-float(delta) / float(window))
            results.append(
                RecallCandidate(
                    event=candidate,
                    channel="temporal",
                    channel_score=score,
                    features={"delta_seconds": delta},
                )
            )
        results.sort(key=lambda item: item.channel_score, reverse=True)
        return results[:limit]

    def _recall_entity(self, event: Event, current_time: int) -> list[RecallCandidate]:
        limit = max(1, int(getattr(self.config, "recall_entity_limit", 20) or 20))
        entity_ids = self._event_entity_ids(event)
        session_id = self._event_session_id(event)
        candidate_map: dict[str, RecallCandidate] = {}

        if entity_ids:
            try:
                candidate_events = self.store.get_events_by_entities(entity_ids)
            except Exception:
                candidate_events = []
            shared_expected = set(entity_ids)
            for candidate in candidate_events:
                if candidate.id == event.id:
                    continue
                candidate_entities = set(self._event_entity_ids(candidate))
                shared = shared_expected & candidate_entities
                denominator = max(1, len(shared_expected | candidate_entities))
                shared_ratio = len(shared) / denominator
                session_bonus = 0.2 if session_id and session_id == self._event_session_id(candidate) else 0.0
                score = min(1.0, shared_ratio + session_bonus)
                candidate_map[candidate.id] = RecallCandidate(
                    event=candidate,
                    channel="entity",
                    channel_score=score,
                    features={
                        "shared_entities": sorted(shared),
                        "shared_ratio": round(float(shared_ratio), 6),
                        "session_bonus": session_bonus,
                    },
                )

        if session_id:
            try:
                recent_events = self.store.get_recent_events(
                    current_time=current_time,
                    window_seconds=max(
                        1,
                        int(getattr(self.config, "recall_temporal_window", 1) or 1),
                    ),
                    limit=max(limit * 5, 50),
                )
            except Exception:
                recent_events = []
            for candidate in recent_events:
                if candidate.id == event.id or self._event_session_id(candidate) != session_id:
                    continue
                existing = candidate_map.get(candidate.id)
                if existing is None or existing.channel_score < 0.2:
                    candidate_map[candidate.id] = RecallCandidate(
                        event=candidate,
                        channel="entity",
                        channel_score=max(0.2, float(existing.channel_score or 0.0) if existing else 0.0),
                        features={
                            **(existing.features if existing else {}),
                            "session_bonus": 0.2,
                            "shared_entities": list(
                                sorted(
                                    set((existing.features if existing else {}).get("shared_entities", []))
                                )
                            ),
                            "shared_ratio": float(
                                (existing.features if existing else {}).get("shared_ratio", 0.0)
                            ),
                        },
                    )

        ranked = sorted(
            candidate_map.values(),
            key=lambda item: (
                float(item.channel_score or 0.0),
                int(item.event.last_active or item.event.timestamp or 0),
            ),
            reverse=True,
        )
        return ranked[:limit]

    def _recall_semantic(self, event: Event) -> list[RecallCandidate]:
        limit = max(1, int(getattr(self.config, "recall_semantic_limit", 20) or 20))
        threshold = float(getattr(self.config, "recall_semantic_threshold", 0.65) or 0.65)
        query_embedding = event.embedding or self._embed_event(event)
        if not query_embedding:
            return []
        try:
            candidates = self.store.get_active_events_with_embeddings(limit=max(limit * 10, 200))
        except Exception:
            candidates = []
        ranked: list[RecallCandidate] = []
        for candidate in candidates:
            if candidate.id == event.id:
                continue
            score = self._cosine_similarity(query_embedding, candidate.embedding)
            if score < threshold:
                continue
            ranked.append(
                RecallCandidate(
                    event=candidate,
                    channel="semantic",
                    channel_score=score,
                    features={"cosine_similarity": round(float(score), 6)},
                )
            )
        ranked.sort(key=lambda item: item.channel_score, reverse=True)
        return ranked[:limit]

    def _recall_state(self, event: Event) -> list[RecallCandidate]:
        if not self._state_channel_available(event):
            return []
        payload = event.payload if isinstance(event.payload, dict) else {}
        state_changes = payload.get("state_changes", [])
        limit = max(1, int(getattr(self.config, "recall_state_limit", 10) or 10))
        candidates: dict[str, RecallCandidate] = {}
        for item in state_changes:
            if not isinstance(item, dict):
                continue
            entity = str(item.get("entity", "") or "").strip()
            attribute = str(item.get("attribute", "") or "").strip()
            if not entity or not attribute:
                continue
            for candidate in self.store.find_events_by_state_key(entity=entity, attribute=attribute, limit=limit):
                score = 1.0
                key = candidate.id
                if key not in candidates or candidates[key].channel_score < score:
                    candidates[key] = RecallCandidate(
                        event=candidate,
                        channel="state",
                        channel_score=score,
                        features={"entity": entity, "attribute": attribute},
                    )
        ranked = sorted(candidates.values(), key=lambda item: item.channel_score, reverse=True)
        return ranked[:limit]

    def _recall_reference(self, event: Event) -> list[RecallCandidate]:
        if not self._reference_channel_available(event):
            return []
        payload = event.payload if isinstance(event.payload, dict) else {}
        parent_event_id = str(payload.get("parent_event_id", "") or "").strip()
        thread_id = str(payload.get("thread_id", "") or "").strip()
        limit = max(1, int(getattr(self.config, "recall_reference_limit", 10) or 10))
        candidates: dict[str, RecallCandidate] = {}
        if parent_event_id:
            parent_event = self.store.get_event(parent_event_id)
            if parent_event is not None:
                candidates[parent_event.id] = RecallCandidate(
                    event=parent_event,
                    channel="reference",
                    channel_score=1.0,
                    features={"match_type": "parent_event_id"},
                )
        if thread_id:
            for candidate in self.store.find_events_by_thread(thread_id=thread_id, limit=limit):
                if candidate.id == event.id:
                    continue
                candidates[candidate.id] = RecallCandidate(
                    event=candidate,
                    channel="reference",
                    channel_score=max(
                        0.8,
                        float(candidates.get(candidate.id, RecallCandidate(candidate, "reference", 0.0)).channel_score),
                    ),
                    features={"match_type": "thread_id"},
                )
        ranked = sorted(candidates.values(), key=lambda item: item.channel_score, reverse=True)
        return ranked[:limit]

    def _channel_weights(self) -> dict[str, float]:
        return {
            "temporal": float(getattr(self.config, "recall_weight_temporal", 0.15) or 0.15),
            "entity": float(getattr(self.config, "recall_weight_entity", 0.25) or 0.25),
            "semantic": float(getattr(self.config, "recall_weight_semantic", 0.35) or 0.35),
            "state": float(getattr(self.config, "recall_weight_state", 0.15) or 0.15),
            "reference": float(getattr(self.config, "recall_weight_reference", 0.10) or 0.10),
        }

    def _event_entity_ids(self, event: Event) -> list[str]:
        try:
            entity_ids = self.store.get_event_entities(event.id)
        except Exception:
            entity_ids = []
        payload = event.payload if isinstance(event.payload, dict) else {}
        if not entity_ids:
            raw_ids = payload.get("entity_ids", [])
            if isinstance(raw_ids, list):
                entity_ids = [str(item or "").strip() for item in raw_ids if str(item or "").strip()]
        return sorted(set(entity_ids))

    def _event_session_id(self, event: Event) -> str:
        payload = event.payload if isinstance(event.payload, dict) else {}
        return str(payload.get("session_id", "") or "").strip()

    def _state_channel_available(self, event: Event) -> bool:
        payload = event.payload if isinstance(event.payload, dict) else {}
        state_changes = payload.get("state_changes")
        return isinstance(state_changes, list) and bool(state_changes)

    def _reference_channel_available(self, event: Event) -> bool:
        payload = event.payload if isinstance(event.payload, dict) else {}
        return bool(
            str(payload.get("parent_event_id", "") or "").strip()
            or str(payload.get("thread_id", "") or "").strip()
        )

    def _embed_event(self, event: Event) -> Optional[list[float]]:
        text = " ".join(
            part for part in [
                str(event.summary or "").strip(),
                str(event.action or "").strip(),
                str(event.causality or "").strip(),
            ] if part
        )
        if not text:
            return None
        embedding_client = getattr(self.store, "embedding_client", None)
        try:
            if embedding_client is not None and hasattr(embedding_client, "get_embedding"):
                embedding = embedding_client.get_embedding(text)
                if embedding:
                    event.embedding = embedding
                return embedding
        except Exception:
            return None
        return None

    def _cosine_similarity(
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
