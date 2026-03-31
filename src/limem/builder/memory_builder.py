# -*- coding: utf-8 -*-
"""MemoryBuilder - 记忆构建器

编排整个构建管道：
1. LLM提取（事件）
2. 相似度搜索
3. 合并/创建决策
4. 存储（Event）
5. 修剪/提升
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import inspect
from typing import Any, Optional
import uuid
import time

try:
    from dashscope import TextEmbedding
except Exception:  # pragma: no cover - optional dependency for offline mode
    TextEmbedding = None

from ..core.episode import Episode
from ..core.event import Event
from ..core.memory import IngestResult
from ..config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    EMBEDDING_MODEL,
    EMBEDDING_BATCH_SIZE,
    DEFAULT_USER_ID,
    APPEND_FIRST_MODE,
    DEFERRED_EVOLUTION,
    ENABLE_LEGACY_ONLINE_EVENT_MERGE,
    LLM_CONCURRENCY,
    PRUNE_C_VALID_THRESHOLD,
    PRUNE_EVIDENCE_TOP_K,
)
from ..utils import hash_summary, time_bucket_from_ts
from .extractor import LLMExtractor, ExtractionResult
from .consolidator import Consolidator
from .relationship_inferrer import RelationshipInferrer


@dataclass
class BuilderConfig:
    """构建器配置

    Attributes:
        prune_threshold: 修剪阈值（c_valid）
        prune_top_k: 证据修剪数量
        default_user_id: 默认用户ID
        append_first_mode: 是否启用append-first事件写入
    """

    prune_threshold: int = PRUNE_C_VALID_THRESHOLD
    prune_top_k: int = PRUNE_EVIDENCE_TOP_K
    default_user_id: str = DEFAULT_USER_ID
    append_first_mode: bool = APPEND_FIRST_MODE
    deferred_evolution: bool = DEFERRED_EVOLUTION
    enable_legacy_online_event_merge: bool = ENABLE_LEGACY_ONLINE_EVENT_MERGE
    llm_concurrency: int = LLM_CONCURRENCY


@dataclass
class _PersistenceOutcome:
    built_events: list[Event]
    is_new_flags: list[bool]
    merged_targets: list[Optional[str]]
    entities_created_total: int


@dataclass
class _ExtractionBundle:
    """Intermediate result of the extract-only phase (no DB writes)."""

    episode: Any  # Episode
    extraction: Any  # ExtractionResult
    pending_events: list  # list[tuple[int, Event]]
    embeddings: list  # list[list[float]]
    metrics: dict[str, Any]


class MemoryBuilder:
    """记忆构建器 - 编排完整的构建管道

    职责：协调提取、合并、存储的完整流程。

    管道流程：
    1. 保存原始Episode
    2. LLM提取（事件）
    3. 生成嵌入向量
    4. 相似度搜索
    5. 合并/创建决策
    6. 存储Event
    7. 创建Event → Episode链接
    8. 修剪/提升（可选）
    """

    def __init__(
        self,
        extractor: LLMExtractor,
        consolidator: Consolidator,
        store,  # GraphStore
        config: Optional[BuilderConfig] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        embedding_model: Optional[str] = None,
        dynamic_engine=None,
        relationship_inferrer: Optional[RelationshipInferrer] = None,
    ):
        """初始化记忆构建器

        Args:
            extractor: LLM提取器
            consolidator: 记忆合并器
            store: 图存储接口
            config: 构建器配置
            api_key: DashScope API Key
            base_url: DashScope API URL
            embedding_model: 嵌入模型名称
        """
        self.extractor = extractor
        self.consolidator = consolidator
        self.store = store
        self.config = config or BuilderConfig()

        # 配置嵌入服务
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.embedding_model = embedding_model or EMBEDDING_MODEL
        self.dynamic_engine = dynamic_engine
        self.relationship_inferrer = relationship_inferrer or RelationshipInferrer()

    def build(self, episode: Episode) -> IngestResult:
        """从Episode构建记忆

        这是最核心的方法，编排整个构建流程。

        Args:
            episode: 原始对话片段

        Returns:
            IngestResult 包含事件和构建信息
        """
        build_started_at = time.perf_counter()
        current_time = episode.timestamp
        metrics = self._init_metrics()

        # Step 1: 保存原始Episode
        stage_started_at = time.perf_counter()
        self.store.save_episode(episode)
        metrics["save_episode_ms"] = self._elapsed_ms(stage_started_at)
        print(f"📝 Saved Episode: {episode.id} at t={current_time}")

        # Step 2: LLM提取
        stage_started_at = time.perf_counter()
        extraction = self._extract_episode(episode)
        metrics["extract_episode_ms"] = self._elapsed_ms(stage_started_at)

        event_payloads = self._collect_event_payloads(extraction)
        print(f"🧠 Extracted Events: {len(event_payloads)}")

        print("🧩 Entities: []")
        pending_events: list[tuple[int, Event]] = []

        for idx, event_payload in enumerate(event_payloads):
            event = self._build_event_frame(event_payload, episode, current_time, index=idx)
            if not self._is_effective_event(event):
                continue
            print(f"   - Event[{idx}]: {event.summary}")
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
        self._persist_inferred_relations(persistence.built_events, episode)
        metrics["inferred_relations_ms"] = self._elapsed_ms(stage_started_at)

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
        # Do not persist fallback events. If normalization drops all extracted
        # candidates, this episode is treated as non-eventful for memory graph.
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
        """构建事件帧

        Args:
            extraction: 提取结果
            episode: 原始Episode
            current_time: 当前时间戳

        Returns:
            Event实例
        """
        # 仅使用提取结果中的摘要；空摘要应在上游被判定为无效事件。
        summary = data.get("summary", "")

        # 构建时间范围
        time_range = data.get("time_range", {})
        if self._should_reanchor_event_time(time_range, episode, current_time):
            time_range["start"] = current_time
            time_range["end"] = current_time
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

    def _should_reanchor_event_time(
        self,
        time_range: dict[str, Any],
        episode: Episode,
        current_time: int,
    ) -> bool:
        start_raw = int(time_range.get("start", 0) or 0) if isinstance(time_range, dict) else 0
        if start_raw <= 0 or current_time <= 0:
            return False
        # For telemetry-like episode streams, far-away extracted timestamps are usually noise
        # from copied payload text rather than the current trip record time.
        if abs(start_raw - current_time) <= 3 * 24 * 3600:
            return False
        metadata = episode.metadata if isinstance(episode.metadata, dict) else {}
        if not metadata.get("start_time"):
            return False
        episode_text = str(episode.content or "")
        retrospective_hints = ("上次", "之前", "去年", "前年", "历史", "回顾")
        return not any(token in episode_text for token in retrospective_hints)

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

    def _is_effective_event(self, event: Event) -> bool:
        if (event.summary or "").strip():
            return True
        if (event.action or "").strip():
            return True
        if (event.causality or "").strip():
            return True
        return False

    def _merge_event(
        self,
        event_id: str,
        incoming_event: Event,
        embedding: list[float],
        current_time: int,
    ) -> Event:
        """合并到现有事件

        Args:
            event_id: 目标事件ID
            incoming_event: 新事件
            embedding: 嵌入向量
            current_time: 当前时间戳

        Returns:
            合并后的事件
        """
        # 获取现有事件
        existing = self.store.get_event(event_id)
        if not existing:
            raise ValueError(f"Event not found: {event_id}")

        # 合并证据
        merged_evidence = existing.evidence + incoming_event.evidence

        # 更新事件
        existing.last_active = current_time
        existing.evidence = merged_evidence
        existing.embedding = embedding

        # 保存更新
        self.store.update_event(existing)

        return existing

    def _update_entity_relations(
        self,
        event_id: str,
        entities: list[str],
        current_time: int,
    ) -> int:
        """更新实体关系

        Args:
            event_id: 事件ID
            entities: 实体列表
            current_time: 当前时间戳

        Returns:
            新创建的实体数量
        """
        del event_id, entities, current_time
        return 0

    def _prune_and_promote(self, event_id: str, current_time: int) -> None:
        """修剪证据并提升为永久特征

        Args:
            event_id: 事件ID
            current_time: 当前时间戳
        """
        # 修剪证据
        self._prune_event_evidence(event_id)

        # 提升为永久特征
        self.store.promote_permanent_trait(
            user_id=self.config.default_user_id,
            event_id=event_id,
            t_created=current_time,
        )

    def _prune_event_evidence(self, event_id: str) -> None:
        """修剪事件证据

        保留置信度最高的 Top-K 证据。

        Args:
            event_id: 事件ID
        """
        event = self.store.get_event(event_id)
        if not event or not event.evidence:
            return

        # 按置信度排序
        sorted_evidence = sorted(
            event.evidence,
            key=lambda item: float(item.get("confidence", 0.0)),
            reverse=True,
        )

        # 保留 Top-K
        event.evidence = sorted_evidence[: self.config.prune_top_k]
        self.store.update_event(event)

    def _get_embedding(self, text: str) -> list[float]:
        """获取文本嵌入向量

        Args:
            text: 输入文本

        Returns:
            嵌入向量
        """
        if TextEmbedding is None:
            # Offline deterministic embedding fallback
            import hashlib
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            dim = 1536
            vec = [0.0] * dim
            for i, b in enumerate(digest):
                vec[i % dim] += (b / 255.0) * 2.0 - 1.0
            norm = sum(v * v for v in vec) ** 0.5
            return [v / norm for v in vec] if norm else vec

        import dashscope
        dashscope.base_http_api_url = self.base_url
        dashscope.api_key = self.api_key

        resp = TextEmbedding.call(model=self.embedding_model, input=text)
        output = resp.output

        if isinstance(output, dict):
            return output["embeddings"][0]["embedding"]
        return output.embeddings[0].embedding

    def _get_embedding_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if TextEmbedding is None:
            return [self._get_embedding(text) for text in texts]

        import dashscope
        dashscope.base_http_api_url = self.base_url
        dashscope.api_key = self.api_key

        resp = TextEmbedding.call(model=self.embedding_model, input=texts)
        output = resp.output
        raw_embeddings = output.get("embeddings", []) if isinstance(output, dict) else output.embeddings

        indexed_embeddings: list[tuple[int, list[float]]] = []
        for idx, item in enumerate(raw_embeddings or []):
            if isinstance(item, dict):
                text_index = item.get("text_index", item.get("textIndex", idx))
                embedding = item.get("embedding") or []
            else:
                text_index = getattr(item, "text_index", getattr(item, "textIndex", idx))
                embedding = getattr(item, "embedding", []) or []
            indexed_embeddings.append((int(text_index), list(embedding)))

        indexed_embeddings.sort(key=lambda pair: pair[0])
        embeddings = [embedding for _, embedding in indexed_embeddings]
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"embedding batch size mismatch: expected {len(texts)}, got {len(embeddings)}"
            )
        return embeddings

    def _get_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if TextEmbedding is None:
            return [self._get_embedding(text) for text in texts]

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

    def _get_entity_name(self, entity: Any) -> Optional[str]:
        """获取实体名称

        Args:
            entity: 实体（字符串或字典）

        Returns:
            实体名称
        """
        if isinstance(entity, dict):
            return entity.get("name")
        return str(entity) if entity else None

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
        entities: list[Any] = []
        built_events: list[Event] = []
        is_new_flags: list[bool] = []
        merged_targets: list[Optional[str]] = []
        entities_created_total = 0

        if self.config.append_first_mode or not self.config.enable_legacy_online_event_merge:
            for (_, event), embedding in zip(pending_events, embeddings):
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
            for event in built_events:
                entities_created_total += self._update_entity_relations(
                    event_id=event.id,
                    entities=entities,
                    current_time=current_time,
                )
            is_new_flags = [True] * len(built_events)
            merged_targets = [None] * len(built_events)
            return _PersistenceOutcome(
                built_events=built_events,
                is_new_flags=is_new_flags,
                merged_targets=merged_targets,
                entities_created_total=entities_created_total,
            )

        print("⚠️ Legacy online event merge compatibility mode enabled")
        for (_, event), embedding in zip(pending_events, embeddings):
            consolidation = self.consolidator.find_similar_event(
                embedding=embedding,
                entities=entities,
                action=event.action,
                current_time=current_time,
            )

            if consolidation.similarity_score > 0:
                print(f"🔎 Top match (combined={consolidation.similarity_score:.4f})")
                if consolidation.debug_info:
                    debug = consolidation.debug_info
                    print(
                        f"   └─ semantic={debug.get('semantic', 0):.3f}, "
                        f"entity={debug.get('entity', 0):.3f}, "
                        f"time={debug.get('time', 0):.3f}, "
                        f"action={debug.get('action', 0):.3f}"
                    )

            if consolidation.should_merge:
                event = self._merge_event(
                    event_id=consolidation.target_event_id,
                    incoming_event=event,
                    embedding=embedding,
                    current_time=current_time,
                )
                is_new = False
                print(f"🔍 Merged into existing memory: {consolidation.target_event_id[:8]}...")
            else:
                event.id = hash_summary(event.summary)
                event.embedding = embedding
                self.store.save_event(event)
                is_new = True
                print(f"🆕 Created new memory: {event.id[:8]}...")

            self.store.link_event_to_episode(event.id, episode.id)
            entities_created_total += self._update_entity_relations(
                event_id=event.id,
                entities=entities,
                current_time=current_time,
            )
            built_events.append(event)
            is_new_flags.append(is_new)
            merged_targets.append(consolidation.target_event_id if not is_new else None)

        return _PersistenceOutcome(
            built_events=built_events,
            is_new_flags=is_new_flags,
            merged_targets=merged_targets,
            entities_created_total=entities_created_total,
        )

    # ------------------------------------------------------------------
    # Two-phase ingest: extract (LLM, thread-safe) → persist (DB, serial)
    # ------------------------------------------------------------------

    def extract_only(self, episode: Episode) -> _ExtractionBundle:
        """Phase 1: LLM extraction + embedding. No DB writes, thread-safe."""
        started_at = time.perf_counter()
        current_time = episode.timestamp
        metrics = self._init_metrics()
        stage_started_at = time.perf_counter()
        extraction = self._extract_episode(episode)
        metrics["extract_episode_ms"] = self._elapsed_ms(stage_started_at)
        event_payloads = self._collect_event_payloads(extraction)

        pending_events: list[tuple[int, Event]] = []
        for idx, event_payload in enumerate(event_payloads):
            event = self._build_event_frame(event_payload, episode, current_time, index=idx)
            if self._is_effective_event(event):
                pending_events.append((idx, event))
        metrics["event_count"] = len(pending_events)

        stage_started_at = time.perf_counter()
        embeddings = (
            self._get_embeddings([event.summary for _, event in pending_events])
            if pending_events else []
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
        """Phase 2: write extracted results to DB. NOT thread-safe."""
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
        self._persist_inferred_relations(persistence.built_events, episode)
        metrics["inferred_relations_ms"] = metrics.get("inferred_relations_ms", 0.0) + self._elapsed_ms(stage_started_at)

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

    def _extract_episode(self, episode: Episode) -> ExtractionResult:
        extract_fn = self.extractor.extract
        try:
            parameters = inspect.signature(extract_fn).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "metadata" in parameters:
            return extract_fn(episode.content, metadata=episode.metadata)
        return extract_fn(episode.content)

    def _persist_inferred_relations(self, events: list[Event], episode: Episode) -> None:
        if not self.relationship_inferrer or len(events) < 2:
            return
        metadata = episode.metadata if isinstance(episode.metadata, dict) else {}
        session_id = str(
            metadata.get("session_id")
            or metadata.get("sessionId")
            or metadata.get("conversation_id")
            or ""
        )
        for relation in self.relationship_inferrer.infer(events):
            self.store.upsert_event_relation(
                from_event_id=relation.from_event_id,
                to_event_id=relation.to_event_id,
                relation_type=relation.relation_type,
                description=relation.description,
                confidence=relation.confidence,
                evidence_span=relation.evidence_span,
                source_episode_id=episode.id,
                source_session_id=session_id,
                timestamp=episode.timestamp,
            )
