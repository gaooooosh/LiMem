# -*- coding: utf-8 -*-
"""MemoryBuilder - orchestrates extraction, embedding, and persistence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import inspect
import time
from typing import Any, Optional
import uuid

from ..config import (
    APPEND_FIRST_MODE,
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    DEFAULT_USER_ID,
    DEFERRED_EVOLUTION,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_MODEL,
    LLM_CONCURRENCY,
    PRUNE_C_VALID_THRESHOLD,
    PRUNE_EVIDENCE_TOP_K,
    normalize_dashscope_base_url,
)
from ..core.episode import Episode
from ..core.event import Event
from ..core.memory import IngestResult
from ..llm import DashScopeClient
from ..utils import hash_summary, time_bucket_from_ts
from .extractor import ExtractionResult, LLMExtractor


@dataclass
class BuilderConfig:
    prune_threshold: int = PRUNE_C_VALID_THRESHOLD
    prune_top_k: int = PRUNE_EVIDENCE_TOP_K
    default_user_id: str = DEFAULT_USER_ID
    append_first_mode: bool = APPEND_FIRST_MODE
    deferred_evolution: bool = DEFERRED_EVOLUTION
    llm_concurrency: int = LLM_CONCURRENCY


@dataclass
class _PersistenceOutcome:
    built_events: list[Event]
    is_new_flags: list[bool]
    merged_targets: list[Optional[str]]
    entities_created_total: int


@dataclass
class _ExtractionBundle:
    episode: Any
    extraction: Any
    pending_events: list
    embeddings: list
    metrics: dict[str, Any]


class MemoryBuilder:
    """Build memory events from extracted LLM payloads."""

    def __init__(
        self,
        extractor: LLMExtractor,
        store,
        config: Optional[BuilderConfig] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        embedding_model: Optional[str] = None,
        dynamic_engine=None,
        llm_client: Optional[DashScopeClient] = None,
    ):
        self.extractor = extractor
        self.store = store
        self.config = config or BuilderConfig()
        self.embedding_model = embedding_model or EMBEDDING_MODEL
        self.dynamic_engine = dynamic_engine
        if llm_client is not None:
            self.llm_client = llm_client
        else:
            self.api_key = api_key or DASHSCOPE_API_KEY
            self.base_url = normalize_dashscope_base_url(base_url or DASHSCOPE_BASE_URL)
            self.llm_client = DashScopeClient(
                api_key=self.api_key,
                base_url=self.base_url,
            )

    def build(self, episode: Episode) -> IngestResult:
        build_started_at = time.perf_counter()
        current_time = episode.timestamp
        metrics = self._init_metrics()

        stage_started_at = time.perf_counter()
        self.store.save_episode(episode)
        metrics["save_episode_ms"] = self._elapsed_ms(stage_started_at)

        stage_started_at = time.perf_counter()
        extraction = self._extract_episode(episode)
        metrics["extract_episode_ms"] = self._elapsed_ms(stage_started_at)

        event_payloads = self._collect_event_payloads(extraction)
        self._record_extraction_metrics(
            metrics=metrics,
            extraction=extraction,
            event_payloads=event_payloads,
        )
        pending_events: list[tuple[int, Event]] = []
        for idx, event_payload in enumerate(event_payloads):
            event = self._build_event_frame(event_payload, episode, current_time, index=idx)
            if self._is_effective_event(event):
                pending_events.append((idx, event))
        metrics["event_count"] = len(pending_events)

        if not pending_events:
            metrics["total_ms"] = self._elapsed_ms(build_started_at)
            return self._ignored_ingest_result(
                episode=episode,
                current_time=current_time,
                metrics=metrics,
            )

        stage_started_at = time.perf_counter()
        embeddings = self._get_embeddings([event.summary for _, event in pending_events])
        metrics["embedding_ms"] = self._elapsed_ms(stage_started_at)

        stage_started_at = time.perf_counter()
        persistence = self._persist_pending_events(
            episode=episode,
            current_time=current_time,
            pending_events=pending_events,
            embeddings=embeddings,
        )
        metrics["persist_events_ms"] = self._elapsed_ms(stage_started_at)

        stage_started_at = time.perf_counter()
        if self.dynamic_engine and not self.config.deferred_evolution:
            self.dynamic_engine.evolve_existing_events(persistence.built_events)
        metrics["dynamic_evolution_ms"] = self._elapsed_ms(stage_started_at)
        metrics["total_ms"] = self._elapsed_ms(build_started_at)

        return IngestResult(
            event=persistence.built_events[0],
            is_new=persistence.is_new_flags[0],
            merged_with=persistence.merged_targets[0],
            entities_created=persistence.entities_created_total,
            events=persistence.built_events,
            metrics=metrics,
        )

    def extract_only(self, episode: Episode) -> _ExtractionBundle:
        started_at = time.perf_counter()
        current_time = episode.timestamp
        metrics = self._init_metrics()

        stage_started_at = time.perf_counter()
        extraction = self._extract_episode(episode)
        metrics["extract_episode_ms"] = self._elapsed_ms(stage_started_at)
        event_payloads = self._collect_event_payloads(extraction)
        self._record_extraction_metrics(
            metrics=metrics,
            extraction=extraction,
            event_payloads=event_payloads,
        )

        pending_events: list[tuple[int, Event]] = []
        for idx, event_payload in enumerate(event_payloads):
            event = self._build_event_frame(event_payload, episode, current_time, index=idx)
            if self._is_effective_event(event):
                pending_events.append((idx, event))
        metrics["event_count"] = len(pending_events)

        stage_started_at = time.perf_counter()
        embeddings = (
            self._get_embeddings([event.summary for _, event in pending_events])
            if pending_events
            else []
        )
        metrics["embedding_ms"] = self._elapsed_ms(stage_started_at)
        metrics["total_ms"] = self._elapsed_ms(started_at)

        return _ExtractionBundle(
            episode=episode,
            extraction=extraction,
            pending_events=pending_events,
            embeddings=embeddings,
            metrics=metrics,
        )

    def persist_extraction(self, bundle: _ExtractionBundle) -> IngestResult:
        started_at = time.perf_counter()
        episode = bundle.episode
        current_time = episode.timestamp
        metrics = dict(bundle.metrics or self._init_metrics())

        stage_started_at = time.perf_counter()
        self.store.save_episode(episode)
        metrics["save_episode_ms"] = metrics.get("save_episode_ms", 0.0) + self._elapsed_ms(stage_started_at)

        if not bundle.pending_events:
            metrics["total_ms"] = float(metrics.get("total_ms", 0.0)) + self._elapsed_ms(started_at)
            return self._ignored_ingest_result(
                episode=episode,
                current_time=current_time,
                metrics=metrics,
            )

        stage_started_at = time.perf_counter()
        persistence = self._persist_pending_events(
            episode=episode,
            current_time=current_time,
            pending_events=bundle.pending_events,
            embeddings=bundle.embeddings,
        )
        metrics["persist_events_ms"] = metrics.get("persist_events_ms", 0.0) + self._elapsed_ms(stage_started_at)

        stage_started_at = time.perf_counter()
        if self.dynamic_engine and not self.config.deferred_evolution:
            self.dynamic_engine.evolve_existing_events(persistence.built_events)
        metrics["dynamic_evolution_ms"] = metrics.get("dynamic_evolution_ms", 0.0) + self._elapsed_ms(stage_started_at)
        metrics["total_ms"] = float(metrics.get("total_ms", 0.0)) + self._elapsed_ms(started_at)

        return IngestResult(
            event=persistence.built_events[0],
            is_new=persistence.is_new_flags[0],
            merged_with=persistence.merged_targets[0],
            entities_created=persistence.entities_created_total,
            events=persistence.built_events,
            metrics=metrics,
        )

    def _init_metrics(self) -> dict[str, Any]:
        return {
            "save_episode_ms": 0.0,
            "extract_episode_ms": 0.0,
            "embedding_ms": 0.0,
            "persist_events_ms": 0.0,
            "inferred_relations_ms": 0.0,
            "dynamic_evolution_ms": 0.0,
            "total_ms": 0.0,
            "event_count": 0,
            "raw_event_count": 0,
            "subject_event_count": 0,
            "inline_context_count": 0,
            "orphan_context_count": 0,
            "episodes_with_orphan_contexts": 0,
            "eventless_orphan_episode_count": 0,
            "orphan_contexts": [],
            "deferred_evolution": bool(self.config.deferred_evolution),
        }

    def _elapsed_ms(self, started_at: float) -> float:
        return round((time.perf_counter() - started_at) * 1000.0, 3)

    def _ignored_ingest_result(
        self,
        episode: Episode,
        current_time: int,
        metrics: Optional[dict[str, Any]] = None,
    ) -> IngestResult:
        ignored_event = Event(
            id=f"ignored_{episode.id}",
            summary="",
            action="",
            causality="",
            time_range={
                "start": current_time,
                "end": current_time,
                "display_time_bucket": time_bucket_from_ts(current_time),
            },
            last_active=current_time,
            participants=[],
            evidence=[],
            timestamp=current_time,
            created_at=current_time,
            updated_at=current_time,
            valid_from=current_time,
            payload={
                "episode_id": episode.id,
                "episode_text": episode.content,
                "skip_reason": "no_effective_event_after_normalization",
            },
            status="ignored",
        )
        return IngestResult(
            event=ignored_event,
            is_new=False,
            merged_with=None,
            entities_created=0,
            events=[],
            metrics=dict(metrics or {}),
        )

    def _build_event_frame(
        self,
        data: dict[str, Any],
        episode: Episode,
        current_time: int,
        index: int = 0,
    ) -> Event:
        summary = data.get("summary", "")
        time_range = dict(data.get("time_range", {}) or {})
        if time_range.get("start", 0) == 0:
            time_range["start"] = current_time
        if time_range.get("end", 0) == 0:
            time_range["end"] = current_time
        if not time_range.get("display_time_bucket", ""):
            time_range["display_time_bucket"] = time_bucket_from_ts(current_time)

        payload = dict(data)
        payload["episode_id"] = episode.id
        payload["episode_text"] = episode.content
        payload["event_index"] = int(index)
        if episode.metadata:
            payload["episode_metadata"] = dict(episode.metadata)

        return Event(
            id=hash_summary(summary) if summary else "",
            summary=summary,
            action=data.get("action", ""),
            causality=data.get("causality", ""),
            time_range=time_range,
            last_active=current_time,
            participants=data.get("participants", []),
            evidence=data.get("evidence", []),
            timestamp=current_time,
            created_at=current_time,
            updated_at=current_time,
            valid_from=current_time,
            payload=payload,
            status=str(data.get("status", "active")),
        )

    def _collect_event_payloads(self, extraction: ExtractionResult) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        if isinstance(extraction.events_data, list):
            payloads.extend(item for item in extraction.events_data if isinstance(item, dict))
        if isinstance(extraction.event_data, dict) and extraction.event_data:
            signature = self._event_payload_signature(extraction.event_data)
            existing = {self._event_payload_signature(item) for item in payloads}
            if signature and signature not in existing:
                payloads.insert(0, extraction.event_data)
        return payloads

    def _event_payload_signature(self, payload: dict[str, Any]) -> str:
        parts = [
            str(payload.get("summary", "") or "").strip(),
            str(payload.get("action", "") or "").strip(),
            str(payload.get("causality", "") or "").strip(),
        ]
        return "|".join(parts) if any(parts) else ""

    def _record_extraction_metrics(
        self,
        metrics: dict[str, Any],
        extraction: ExtractionResult,
        event_payloads: list[dict[str, Any]],
    ) -> None:
        orphan_contexts = [
            item
            for item in (getattr(extraction, "orphan_contexts", []) or [])
            if isinstance(item, dict)
        ]
        metrics["raw_event_count"] = len(event_payloads)
        metrics["subject_event_count"] = self._count_subject_event_payloads(event_payloads)
        metrics["inline_context_count"] = self._count_inline_contexts(event_payloads)
        metrics["orphan_context_count"] = len(orphan_contexts)
        metrics["episodes_with_orphan_contexts"] = int(bool(orphan_contexts))
        metrics["eventless_orphan_episode_count"] = int(bool(orphan_contexts) and not event_payloads)
        metrics["orphan_contexts"] = orphan_contexts

    def _count_subject_event_payloads(self, event_payloads: list[dict[str, Any]]) -> int:
        count = 0
        for item in event_payloads:
            participants = item.get("participants")
            if not isinstance(participants, list):
                continue
            if any(isinstance(participant, dict) and str(participant.get("role", "")).strip() for participant in participants):
                count += 1
        return count

    def _count_inline_contexts(self, event_payloads: list[dict[str, Any]]) -> int:
        count = 0
        for item in event_payloads:
            contexts = item.get("contexts")
            if not isinstance(contexts, list):
                continue
            count += sum(
                1
                for context in contexts
                if isinstance(context, dict) and str(context.get("summary", "")).strip()
            )
        return count

    def _is_effective_event(self, event: Event) -> bool:
        return any(
            str(value or "").strip()
            for value in (event.summary, event.action, event.causality)
        )

    def _update_entity_relations(
        self,
        event_id: str,
        entities: list[str],
        current_time: int,
    ) -> int:
        del event_id, entities, current_time
        return 0

    def _prune_event_evidence(self, event_id: str) -> None:
        event = self.store.get_event(event_id)
        if not event or not event.evidence:
            return
        event.evidence = sorted(
            event.evidence,
            key=lambda item: float(item.get("confidence", 0.0)),
            reverse=True,
        )[: self.config.prune_top_k]
        self.store.update_event(event)

    def _get_embedding(self, text: str) -> list[float]:
        return self.llm_client.embed_text(text=text, model=self.embedding_model)

    def _get_embedding_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        embeddings = self.llm_client.embed_texts(model=self.embedding_model, texts=texts)
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"embedding batch size mismatch: expected {len(texts)}, got {len(embeddings)}"
            )
        return embeddings

    def _get_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        batches = [
            (start, texts[start : start + EMBEDDING_BATCH_SIZE])
            for start in range(0, len(texts), EMBEDDING_BATCH_SIZE)
        ]
        workers = max(1, min(int(self.config.llm_concurrency or 1), len(batches)))
        embeddings: list[Optional[list[float]]] = [None] * len(texts)

        def resolve_batch(batch_texts: list[str]) -> list[list[float]]:
            try:
                return self._get_embedding_batch(batch_texts)
            except Exception:
                if len(batch_texts) <= 1:
                    raise
                return [self._get_embedding(text) for text in batch_texts]

        if workers <= 1:
            for start, batch_texts in batches:
                batch_embeddings = resolve_batch(batch_texts)
                for offset, embedding in enumerate(batch_embeddings):
                    embeddings[start + offset] = embedding
            return [embedding or [] for embedding in embeddings]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(resolve_batch, batch_texts): start
                for start, batch_texts in batches
            }
            for future, start in futures.items():
                batch_embeddings = future.result()
                for offset, embedding in enumerate(batch_embeddings):
                    embeddings[start + offset] = embedding
        return [embedding or [] for embedding in embeddings]

    def _append_first_event_id(self, event: Event) -> str:
        base = event.id or hash_summary(event.summary or uuid.uuid4().hex)
        ts = event.timestamp or event.last_active or int(time.time())
        return f"{base[:20]}_{ts}_{uuid.uuid4().hex[:6]}"

    def _save_events(self, events: list[Event]) -> None:
        if not events:
            return
        save_batch = getattr(self.store, "save_events_batch", None)
        if callable(save_batch):
            save_batch(events)
            return
        for event in events:
            self.store.save_event(event)

    def _link_events_to_episode(self, event_ids: list[str], episode_id: str) -> None:
        if not event_ids:
            return
        link_batch = getattr(self.store, "link_events_to_episode_batch", None)
        if callable(link_batch):
            link_batch(event_ids, episode_id)
            return
        for event_id in event_ids:
            self.store.link_event_to_episode(event_id, episode_id)

    def _persist_pending_events(
        self,
        episode: Episode,
        current_time: int,
        pending_events: list[tuple[int, Event]],
        embeddings: list[list[float]],
    ) -> _PersistenceOutcome:
        entities: list[str] = []
        built_events: list[Event] = []
        for (_, event), embedding in zip(pending_events, embeddings):
            if self.config.append_first_mode:
                event.id = self._append_first_event_id(event)
            event.embedding = embedding
            event.timestamp = event.timestamp or current_time
            event.created_at = event.created_at or current_time
            event.updated_at = current_time
            event.valid_from = event.valid_from or current_time
            event.status = event.status or "active"
            built_events.append(event)

        self._save_events(built_events)
        self._link_events_to_episode([event.id for event in built_events], episode.id)

        entities_created_total = 0
        for event in built_events:
            entities_created_total += self._update_entity_relations(
                event_id=event.id,
                entities=entities,
                current_time=current_time,
            )

        return _PersistenceOutcome(
            built_events=built_events,
            is_new_flags=[True] * len(built_events),
            merged_targets=[None] * len(built_events),
            entities_created_total=entities_created_total,
        )

    def _extract_episode(self, episode: Episode) -> ExtractionResult:
        extract_fn = self.extractor.extract
        try:
            parameters = inspect.signature(extract_fn).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "metadata" in parameters:
            return extract_fn(episode.content, metadata=episode.metadata)
        return extract_fn(episode.content)
