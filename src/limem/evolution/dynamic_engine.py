# -*- coding: utf-8 -*-
"""Dynamic evolution engine for long-term memory graph."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypedDict
import json
import logging
import math
import os
import re
import time
import uuid

from ..config import (
    APPEND_FIRST_MODE,
    ARCHIVE_EVENT_SECONDS,
    BULK_INGEST_MODE,
    CONSOLIDATION_LOG_PATH,
    CONSOLIDATION_MIN_INTERVAL_SECONDS,
    CONTEXT_CANDIDATE_LIMIT,
    CONTEXT_CONFLICT_THRESHOLD,
    CONTEXT_EXTRACTION_BATCH_SIZE,
    REG_ENTITY_AMBIGUOUS_MARGIN,
    REG_ENTITY_EVENT_SUMMARY_MAX,
    REG_ENTITY_LLM_BUDGET_PER_INGEST,
    REG_ENTITY_STRATEGY_VERSION,
    REG_ENTITY_VECTOR_HIGH,
    REG_ENTITY_VECTOR_LOW,
    CONTEXT_REUSE_ALLOW_CROSS_SUBTYPE,
    CONTEXT_REUSE_CANDIDATE_LIMIT,
    CONTEXT_REUSE_GATING_ENABLED,
    CONTEXT_REUSE_MIN_EVIDENCE_OVERLAP,
    CONTEXT_REUSE_MIN_SUMMARY_OVERLAP,
    CONTEXT_REUSE_REQUIRE_EVIDENCE,
    CONTEXT_REUSE_SCORE_THRESHOLD,
    CONTEXT_QUERY_CANDIDATE_LIMIT,
    CONTEXT_REUSE_THRESHOLD,
    CONTEXT_REUSE_WEIGHT_ENTITY,
    CONTEXT_REUSE_WEIGHT_EVIDENCE,
    CONTEXT_REUSE_WEIGHT_SUBTYPE,
    CONTEXT_REUSE_WEIGHT_SUMMARY,
    CONTEXT_REUSE_WEIGHT_TEMPORAL,
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    DECAY_RATE,
    DECAY_STEP,
    ENABLE_AUTO_CONSOLIDATION,
    ENABLE_EVENT_RELATIONS,
    EVENT_CONSOLIDATION_CANDIDATE_LIMIT,
    EVENT_CONSOLIDATION_EMBEDDING_CANDIDATE_THRESHOLD,
    EVENT_CONSOLIDATION_EMBEDDING_TOP_K,
    EVENT_CONSOLIDATION_THRESHOLD,
    EVENT_CONSOLIDATION_WINDOW_SECONDS,
    EVENT_MERGE_TRACE_LOG_PATH,
    EVENT_MERGE_TRACE_STRATEGY_VERSION,
    GENERATION_MODEL,
    LLM_CONCURRENCY,
    REINFORCEMENT_STEP,
    RECALL_ENTITY_LIMIT,
    RECALL_MAX_CANDIDATES,
    RECALL_MIN_AGGREGATE_SCORE,
    RECALL_REFERENCE_LIMIT,
    RECALL_SEMANTIC_LIMIT,
    RECALL_SEMANTIC_THRESHOLD,
    RECALL_STATE_LIMIT,
    RECALL_TEMPORAL_LIMIT,
    RECALL_TEMPORAL_WINDOW,
    RECALL_WEIGHT_ENTITY,
    RECALL_WEIGHT_REFERENCE,
    RECALL_WEIGHT_SEMANTIC,
    RECALL_WEIGHT_STATE,
    RECALL_WEIGHT_TEMPORAL,
    RELATION_CLASSIFICATION_BATCH_SIZE,
    RELATION_MAX_LINKS_PER_EVENT,
    RELATION_MIN_CONFIDENCE,
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
from ..core.context import Context, ContextDraft, render_context_description
from ..core.entity import Entity
from ..core.event import Event
from ..llm import DashScopeClient
from ..utils import hash_summary, load_prompt, robust_json_loads, safe_json_dumps, safe_json_loads
from .recall_pipeline import RecallPipeline
from .relation_processor import ProcessResult, RelationProcessor

logger = logging.getLogger(__name__)


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
    context_reuse_gating_enabled: bool = CONTEXT_REUSE_GATING_ENABLED
    context_reuse_candidate_limit: int = CONTEXT_REUSE_CANDIDATE_LIMIT
    context_reuse_score_threshold: float = CONTEXT_REUSE_SCORE_THRESHOLD
    context_reuse_min_summary_overlap: float = CONTEXT_REUSE_MIN_SUMMARY_OVERLAP
    context_reuse_min_evidence_overlap: float = CONTEXT_REUSE_MIN_EVIDENCE_OVERLAP
    context_reuse_require_evidence: bool = CONTEXT_REUSE_REQUIRE_EVIDENCE
    context_reuse_allow_cross_subtype: bool = CONTEXT_REUSE_ALLOW_CROSS_SUBTYPE
    context_reuse_weight_summary: float = CONTEXT_REUSE_WEIGHT_SUMMARY
    context_reuse_weight_evidence: float = CONTEXT_REUSE_WEIGHT_EVIDENCE
    context_reuse_weight_entity: float = CONTEXT_REUSE_WEIGHT_ENTITY
    context_reuse_weight_temporal: float = CONTEXT_REUSE_WEIGHT_TEMPORAL
    context_reuse_weight_subtype: float = CONTEXT_REUSE_WEIGHT_SUBTYPE

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
    event_merge_trace_strategy_version: str = EVENT_MERGE_TRACE_STRATEGY_VERSION
    event_merge_trace_log_path: str = EVENT_MERGE_TRACE_LOG_PATH
    enable_event_relations: bool = ENABLE_EVENT_RELATIONS
    recall_max_candidates: int = RECALL_MAX_CANDIDATES
    recall_min_aggregate_score: float = RECALL_MIN_AGGREGATE_SCORE
    recall_temporal_window: int = RECALL_TEMPORAL_WINDOW
    recall_temporal_limit: int = RECALL_TEMPORAL_LIMIT
    recall_entity_limit: int = RECALL_ENTITY_LIMIT
    recall_semantic_limit: int = RECALL_SEMANTIC_LIMIT
    recall_semantic_threshold: float = RECALL_SEMANTIC_THRESHOLD
    recall_state_limit: int = RECALL_STATE_LIMIT
    recall_reference_limit: int = RECALL_REFERENCE_LIMIT
    recall_weight_temporal: float = RECALL_WEIGHT_TEMPORAL
    recall_weight_entity: float = RECALL_WEIGHT_ENTITY
    recall_weight_semantic: float = RECALL_WEIGHT_SEMANTIC
    recall_weight_state: float = RECALL_WEIGHT_STATE
    recall_weight_reference: float = RECALL_WEIGHT_REFERENCE
    relation_classification_batch_size: int = RELATION_CLASSIFICATION_BATCH_SIZE
    relation_min_confidence: float = RELATION_MIN_CONFIDENCE
    relation_max_links_per_event: int = RELATION_MAX_LINKS_PER_EVENT

    def __post_init__(self) -> None:
        self.llm_base_url = normalize_dashscope_base_url(self.llm_base_url)
        self.context_reuse_candidate_limit = max(1, int(self.context_reuse_candidate_limit or 1))
        for field_name in (
            "context_reuse_score_threshold",
            "context_reuse_min_summary_overlap",
            "context_reuse_min_evidence_overlap",
            "context_reuse_weight_summary",
            "context_reuse_weight_evidence",
            "context_reuse_weight_entity",
            "context_reuse_weight_temporal",
            "context_reuse_weight_subtype",
        ):
            setattr(self, field_name, max(0.0, min(1.0, float(getattr(self, field_name, 0.0) or 0.0))))


class EvolutionReport(TypedDict):
    context_links: int
    next_links: int
    event_relation_links: int
    updates: int
    extensions: int
    derivations: int
    merges: int
    links: int
    skipped: int
    recall_candidates: int


class WriteBatchReport(EvolutionReport):
    event_count: int


class DynamicEvolutionEngine:
    """Incremental dynamic graph update and retrieval engine."""

    def __init__(
        self,
        store: Any,
        config: Optional[DynamicEvolutionConfig] = None,
        llm_client: Optional[DashScopeClient] = None,
        recall_pipeline: Optional[RecallPipeline] = None,
        relation_processor: Optional[RelationProcessor] = None,
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
            llm_client=self.llm_client,
        )
        self.recall_pipeline = recall_pipeline or RecallPipeline(
            store=self.store,
            config=self.config,
        )
        self.relation_processor = relation_processor or RelationProcessor(
            store=self.store,
            llm_client=self.llm_client,
            config=self.config,
        )
        bind_engine = getattr(self.relation_processor, "bind_engine", None)
        if callable(bind_engine):
            bind_engine(self)
        self._context_embedding_cache: dict[str, list[float]] = {}
        self._context_candidates_cache: dict[tuple[str, str], list[Context]] = {}
        self._context_summary_index_cache: dict[str, list[tuple[str, str]]] = {}
        self._rewrite_merged_event_system_prompt = load_prompt("evolution/rewrite_merged_event_system.txt")
        self._rewrite_merged_event_user_prompt = load_prompt("evolution/rewrite_merged_event_user.txt")
        self._reg_entity_match_system_prompt = load_prompt("entity/registration_match_system.txt")
        self._reg_entity_match_user_prompt = load_prompt("entity/registration_match_user.txt")
        # batch 级缓存：仅在 _create_entities_for_events 调用期间填充。
        self._reg_entity_index: Optional[list[dict[str, Any]]] = None
        self._reg_entity_resolution_cache: dict[str, Optional[tuple[str, str, float]]] = {}
        self._reg_entity_extraction_embedding_cache: dict[str, list[float]] = {}
        self._reg_entity_llm_budget_used: int = 0

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

    def _empty_evolution_report(self) -> EvolutionReport:
        return {
            "context_links": 0,
            "next_links": 0,
            "event_relation_links": 0,
            "updates": 0,
            "extensions": 0,
            "derivations": 0,
            "merges": 0,
            "links": 0,
            "skipped": 0,
            "recall_candidates": 0,
        }

    def _merge_evolution_reports(
        self,
        base: EvolutionReport,
        update: EvolutionReport,
    ) -> EvolutionReport:
        merged = self._empty_evolution_report()
        for key in merged:
            merged[key] = int(base.get(key, 0) or 0) + int(update.get(key, 0) or 0)
        return merged

    def _coerce_relation_report(self, raw: Any) -> EvolutionReport:
        if isinstance(raw, dict):
            report = self._empty_evolution_report()
            for key in report:
                report[key] = int(raw.get(key, 0) or 0)
            return report
        report = self._empty_evolution_report()
        try:
            report["event_relation_links"] = int(raw or 0)
        except (TypeError, ValueError):
            report["event_relation_links"] = 0
        return report

    def write_event_batch(
        self,
        events: list[Event],
        record: Optional[Any] = None,
        entities_by_event: Optional[dict[str, list[str]]] = None,
    ) -> WriteBatchReport:
        if not events:
            return {
                "event_count": 0,
                **self._empty_evolution_report(),
            }

        new_events: list[Event] = []
        report = self._empty_evolution_report()
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

        self._create_entities_for_events(new_events)
        resolved_context_batches = self._resolve_context_pairs_for_event_batch(
            events=new_events,
            record=record,
        )
        for event, resolved_contexts in zip(new_events, resolved_context_batches):
            report["context_links"] += self.attach_contexts_to_event(event, resolved_contexts)
        if self.config.enable_event_relations:
            relation_report = self._coerce_relation_report(
                self.extract_event_event_relations(
                    events=new_events,
                    record=record,
                )
            )
            report = self._merge_evolution_reports(report, relation_report)
        if self._should_run_auto_consolidation():
            ts = int(time.time())
            if ts - self._last_consolidation_at >= self.config.consolidation_min_interval_seconds:
                self.run_consolidation(current_time=ts)

        self._context_embedding_cache.clear()
        self._invalidate_context_query_caches()
        return {
            "event_count": len(new_events),
            **report,
        }

    def evolve_existing_events(
        self,
        events: list[Event],
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> EvolutionReport:
        """Apply local dynamic updates for already-persisted events.

        Args:
            events: Already-persisted events to evolve.
            progress_cb: Optional callback(stage, current, total) for progress reporting.
        """
        if not events:
            return self._empty_evolution_report()

        report = self._empty_evolution_report()
        if progress_cb:
            progress_cb("entities", 0, len(events))
        self._create_entities_for_events(events)
        if progress_cb:
            progress_cb("contexts", 0, len(events))
        resolved_context_batches = self._resolve_context_pairs_for_event_batch(
            events=events,
            record=None,
        )
        for event, resolved_contexts in zip(events, resolved_context_batches):
            report["context_links"] += self.attach_contexts_to_event(event, resolved_contexts)
        if self.config.enable_event_relations:
            if progress_cb:
                progress_cb("relations", 0, len(events))
            relation_kwargs: dict[str, Any] = {
                "events": events,
                "record": None,
            }
            if progress_cb is not None:
                relation_kwargs["progress_cb"] = progress_cb
            relation_report = self._coerce_relation_report(
                self.extract_event_event_relations(**relation_kwargs)
            )
            report = self._merge_evolution_reports(report, relation_report)
        if self._should_run_auto_consolidation():
            ts = int(time.time())
            if ts - self._last_consolidation_at >= self.config.consolidation_min_interval_seconds:
                self.run_consolidation(current_time=ts)

        self._context_embedding_cache.clear()
        self._invalidate_context_query_caches()
        return report

    def _create_entities_for_events(self, events: list[Event]) -> int:
        """为事件批量创建实体并建立 INVOLVES 关系。

        增强：在写入 INVOLVES 前先通过 _resolve_to_registered_entity
        判断每个抽取实体是否指向某个注册实体，若是则把 INVOLVES
        指向注册实体（必要时执行 Case B 合并）。
        """
        if not events:
            return 0

        # 构建本批次的注册实体解析上下文（只加载一次）
        self._begin_registered_resolution_batch()

        # 收集 (entity_name, entity_type) → 用作上下文的事件 summary
        # 同名实体可能出现在多个事件中：取首次遇到的事件 summary 作上下文
        unique_entities: dict[tuple[str, str], str] = {}
        for event in events:
            if not event or not event.id or event.status in {"merged", "archived"}:
                continue
            participants = event.participants if isinstance(event.participants, list) else []
            summary = str(getattr(event, "summary", "") or "")
            for participant in participants:
                entity_ref = self._participant_entity_ref(participant)
                if not entity_ref:
                    continue
                unique_entities.setdefault(entity_ref, summary)

        # name_to_canonical: 把抽取出的实体名映射到最终该写入 INVOLVES 的实体 id
        # 若解析到注册实体 → 指向注册 id；否则确保抽取节点存在并指向自身。
        name_to_canonical: dict[str, str] = {}
        merge_now = int(time.time())
        for (entity_name, entity_type), event_summary in unique_entities.items():
            resolved = self._resolve_to_registered_entity(
                name=entity_name,
                etype=entity_type,
                event_summary=event_summary,
            )
            if resolved is not None:
                canonical_id, reason, score = resolved
                # 别名记入注册实体（surface form != canonical id 时）
                if entity_name != canonical_id:
                    try:
                        self.store.add_entity_alias(canonical_id, entity_name)
                    except Exception:
                        pass
                # 若抽取节点之前已物化存在 → 触发 Case B 合并
                existing = self._get_entity_safe(entity_name)
                if (
                    existing is not None
                    and existing.id != canonical_id
                    and existing.status == "active"
                ):
                    try:
                        self.merge_entity(
                            canonical_id=canonical_id,
                            merged_id=existing.id,
                            merge_reason=reason,
                            similarity_score=float(score or 0.0),
                            merged_at=merge_now,
                        )
                    except Exception as exc:
                        logger.warning(
                            "merge_entity failed for %s -> %s: %s",
                            existing.id,
                            canonical_id,
                            exc,
                        )
                name_to_canonical[entity_name] = canonical_id
            else:
                # 没有注册命中：照旧物化抽取节点，并做 merged 重定向保险
                self.store.ensure_entity(entity_name, entity_type)
                try:
                    redirected = self.store.resolve_canonical_entity_id(entity_name)
                except NotImplementedError:
                    redirected = entity_name
                name_to_canonical[entity_name] = redirected or entity_name

        linked = 0
        fallback_now = int(time.time())
        for event in events:
            if not event or not event.id or event.status in {"merged", "archived"}:
                continue
            current_time = (
                event.last_active
                or event.timestamp
                or event.updated_at
                or event.created_at
                or fallback_now
            )
            seen_entities: set[str] = set()
            participants = event.participants if isinstance(event.participants, list) else []
            for participant in participants:
                entity_ref = self._participant_entity_ref(participant)
                if not entity_ref:
                    continue
                entity_name, _ = entity_ref
                target_id = name_to_canonical.get(entity_name) or entity_name
                if target_id in seen_entities:
                    continue
                seen_entities.add(target_id)
                relation = self.store.get_involves_relation(event.id, target_id)
                if relation:
                    continue
                self.store.create_involves_relation(
                    event_id=event.id,
                    entity_id=target_id,
                    t_created=current_time,
                    t_valid=current_time,
                    c_valid=1,
                )
                linked += 1

        self._end_registered_resolution_batch()
        return linked

    # ---------------- Registered Entity 解析与合并 ----------------

    def _begin_registered_resolution_batch(self) -> None:
        """加载注册实体索引；清空 batch 级缓存与预算计数。"""
        try:
            registered_entities = self.store.list_registered_entities_with_embeddings()
        except NotImplementedError:
            registered_entities = []
        except Exception as exc:
            logger.warning("list_registered_entities_with_embeddings failed: %s", exc)
            registered_entities = []
        index: list[dict[str, Any]] = []
        for ent in registered_entities or []:
            if not getattr(ent, "id", None):
                continue
            alias_set = {str(ent.id).strip().lower()}
            for alias in (ent.aliases or []):
                a = str(alias).strip().lower()
                if a:
                    alias_set.add(a)
            index.append(
                {
                    "entity": ent,
                    "id": ent.id,
                    "type": (ent.type or "UNKNOWN"),
                    "description": ent.description or "",
                    "description_embedding": ent.description_embedding,
                    "alias_set": alias_set,
                }
            )
        self._reg_entity_index = index
        self._reg_entity_resolution_cache = {}
        self._reg_entity_extraction_embedding_cache = {}
        self._reg_entity_llm_budget_used = 0

    def _end_registered_resolution_batch(self) -> None:
        self._reg_entity_index = None
        self._reg_entity_resolution_cache = {}
        self._reg_entity_extraction_embedding_cache = {}
        self._reg_entity_llm_budget_used = 0

    def _get_entity_safe(self, entity_id: str) -> Optional[Entity]:
        getter = getattr(self.store, "get_entity", None)
        if not callable(getter):
            return None
        try:
            return getter(entity_id)
        except NotImplementedError:
            return None
        except Exception as exc:
            logger.debug("store.get_entity(%s) failed: %s", entity_id, exc)
            return None

    def _resolve_to_registered_entity(
        self,
        name: str,
        etype: str,
        event_summary: str,
    ) -> Optional[tuple[str, str, float]]:
        """三阶段解析：精确/别名 → 向量相似度 → LLM 仲裁。

        返回 (canonical_id, reason, score)；不命中返回 None。
        结果按 surface name 缓存以避免同 batch 内重复解析。
        """
        if not self._reg_entity_index:
            return None
        name_norm = (name or "").strip()
        if not name_norm:
            return None
        cache_key = name_norm.lower()
        if cache_key in self._reg_entity_resolution_cache:
            return self._reg_entity_resolution_cache[cache_key]

        # Stage 1: 精确 / 别名匹配
        stage1_hits = [
            entry for entry in self._reg_entity_index
            if cache_key in entry["alias_set"]
        ]
        if len(stage1_hits) == 1:
            result = (stage1_hits[0]["id"], "exact_match", 1.0)
            self._reg_entity_resolution_cache[cache_key] = result
            return result
        # 多命中：交由 Stage 2 用上下文向量裁决（仅在这些候选里选）
        stage2_candidates = stage1_hits if stage1_hits else self._reg_entity_index

        # Stage 2: 向量相似度
        etype_norm = (etype or "UNKNOWN").strip() or "UNKNOWN"
        viable: list[tuple[float, dict[str, Any]]] = []
        ext_text = self._build_extraction_context_text(name_norm, event_summary)
        ext_emb = self._embed_extraction_context(ext_text)
        if not ext_emb:
            # 没有 embedding：仅 Stage 1 已经决定；Stage 2/3 跳过
            self._reg_entity_resolution_cache[cache_key] = None
            return None
        for entry in stage2_candidates:
            reg_type = (entry.get("type") or "UNKNOWN").upper()
            if (
                etype_norm.upper() != "UNKNOWN"
                and reg_type != "UNKNOWN"
                and etype_norm.upper() != reg_type
            ):
                continue
            reg_emb = entry.get("description_embedding")
            if not reg_emb:
                continue
            score = self._event_embedding_similarity(ext_emb, reg_emb)
            if score <= 0.0:
                continue
            viable.append((score, entry))

        if not viable:
            self._reg_entity_resolution_cache[cache_key] = None
            return None

        viable.sort(key=lambda x: x[0], reverse=True)
        top_score, top_entry = viable[0]
        second_score = viable[1][0] if len(viable) > 1 else 0.0
        margin = top_score - second_score

        if top_score >= REG_ENTITY_VECTOR_HIGH and margin > REG_ENTITY_AMBIGUOUS_MARGIN:
            result = (top_entry["id"], "vector_high", top_score)
            self._reg_entity_resolution_cache[cache_key] = result
            return result
        if top_score < REG_ENTITY_VECTOR_LOW:
            self._reg_entity_resolution_cache[cache_key] = None
            return None

        # Stage 3: LLM 仲裁（含预算控制）
        if self._reg_entity_llm_budget_used >= REG_ENTITY_LLM_BUDGET_PER_INGEST:
            # 预算耗尽 → 按 uncertain 处理：保守不链接
            self._reg_entity_resolution_cache[cache_key] = None
            return None
        self._reg_entity_llm_budget_used += 1
        decision = self._llm_adjudicate_registration_match(
            entry=top_entry,
            ext_name=name_norm,
            ext_type=etype_norm,
            event_summary=event_summary,
        )
        if decision == "yes":
            result = (top_entry["id"], "llm_yes", top_score)
            self._reg_entity_resolution_cache[cache_key] = result
            return result
        self._reg_entity_resolution_cache[cache_key] = None
        return None

    def _build_extraction_context_text(self, name: str, event_summary: str) -> str:
        snippet = (event_summary or "").strip()
        if len(snippet) > REG_ENTITY_EVENT_SUMMARY_MAX:
            snippet = snippet[:REG_ENTITY_EVENT_SUMMARY_MAX]
        if snippet:
            return f"{name} | {snippet}"
        return name

    def _embed_extraction_context(self, text: str) -> Optional[list[float]]:
        if not text:
            return None
        key = text.strip()
        cached = self._reg_entity_extraction_embedding_cache.get(key)
        if cached is not None:
            return cached
        emb = self._maybe_embed_context(key)
        if emb:
            self._reg_entity_extraction_embedding_cache[key] = emb
        return emb

    def _llm_adjudicate_registration_match(
        self,
        entry: dict[str, Any],
        ext_name: str,
        ext_type: str,
        event_summary: str,
    ) -> str:
        """调用 LLM 仲裁；返回 'yes' / 'no' / 'uncertain'（异常默认 'uncertain'）。"""
        if not self._reg_entity_match_system_prompt or not self._reg_entity_match_user_prompt:
            return "uncertain"
        try:
            user_msg = self._reg_entity_match_user_prompt.format(
                reg_id=entry.get("id", ""),
                reg_type=entry.get("type", "UNKNOWN") or "UNKNOWN",
                reg_description=entry.get("description", "") or "",
                reg_aliases=", ".join(
                    sorted(
                        (entry["entity"].aliases or [])[:5]
                        if isinstance(entry.get("entity"), Entity) else []
                    )
                ),
                ext_name=ext_name,
                ext_type=ext_type or "UNKNOWN",
                event_summary=(event_summary or "")[:REG_ENTITY_EVENT_SUMMARY_MAX],
            )
        except KeyError:
            return "uncertain"
        try:
            result = self.llm_client.call_generation_json(
                system_prompt=self._reg_entity_match_system_prompt,
                user_message=user_msg,
                default={"decision": "uncertain"},
                model=self.config.llm_model,
            )
        except Exception as exc:
            logger.warning("registered entity LLM adjudication failed: %s", exc)
            return "uncertain"
        decision = ""
        if isinstance(result, dict):
            decision = str(result.get("decision", "") or "").strip().lower()
        if decision not in {"yes", "no", "uncertain"}:
            return "uncertain"
        return decision

    def merge_entity(
        self,
        canonical_id: str,
        merged_id: str,
        merge_reason: str = "manual_merge",
        similarity_score: float = 1.0,
        merged_at: Optional[int] = None,
    ) -> dict[str, Any]:
        """把 merged 实体节点合并到 canonical 实体节点。

        步骤镜像 merge_contexts：
            1. INVOLVES 边迁移并去重；
            2. 更新 canonical 的 merged_from / aliases；
            3. 标记 merged 节点 status="merged" + canonical_id；
            4. 写一条 ENTITY_MERGE_TRACE。
        """
        if not canonical_id or not merged_id:
            raise ValueError("canonical_id and merged_id are required")
        if canonical_id == merged_id:
            raise ValueError("Cannot merge the same entity")
        canonical = self._get_entity_safe(canonical_id)
        merged = self._get_entity_safe(merged_id)
        if canonical is None:
            raise ValueError(f"Canonical entity not found: {canonical_id}")
        if merged is None:
            raise ValueError(f"Merged entity not found: {merged_id}")
        ts = int(merged_at or time.time())

        moved = 0
        try:
            moved = self.store.relink_entity_references(merged_id, canonical_id, ts)
        except NotImplementedError:
            moved = 0

        # canonical 更新：合并 aliases / merged_from
        canonical_aliases = list(canonical.aliases or [])
        if merged.id not in canonical_aliases and merged.id != canonical.id:
            canonical_aliases.append(merged.id)
        for alias in (merged.aliases or []):
            if alias and alias not in canonical_aliases and alias != canonical.id:
                canonical_aliases.append(alias)
        new_merged_from = sorted(
            set(list(canonical.merged_from or []) + [merged.id] + list(merged.merged_from or []))
        )
        try:
            self.store.update_entity_attributes(
                canonical.id,
                add_aliases=canonical_aliases,
                updated_at=ts,
            )
        except NotImplementedError:
            pass
        # merged_from 单独通过低阶 cypher 写回，避免 update_entity_attributes 扩面
        try:
            self.store.conn.execute(  # type: ignore[attr-defined]
                "MATCH (e:Entity {id: $id}) SET e.merged_from = $mf, e.updated_at = $ts",
                {"id": canonical.id, "mf": safe_json_dumps(new_merged_from), "ts": ts},
            )
        except Exception:
            pass

        try:
            self.store.mark_entity_merged(merged.id, canonical.id, ts)
        except NotImplementedError:
            pass

        try:
            self.store.save_entity_merge_trace(
                source_entity_id=merged.id,
                target_entity_id=canonical.id,
                merge_reason=merge_reason or "manual_merge",
                similarity_score=float(similarity_score or 0.0),
                merged_at=ts,
                strategy_version=REG_ENTITY_STRATEGY_VERSION,
            )
        except NotImplementedError:
            pass

        return {
            "canonical_entity_id": canonical.id,
            "merged_entity_id": merged.id,
            "merged_at": ts,
            "moved_involves": int(moved or 0),
            "merge_reason": merge_reason,
            "similarity_score": float(similarity_score or 0.0),
            "strategy_version": REG_ENTITY_STRATEGY_VERSION,
        }

    def extract_event_event_relations(
        self,
        events: list[Event],
        record: Optional[Any] = None,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> EvolutionReport:
        if not events:
            return self._empty_evolution_report()
        return self._run_relation_pipeline(events=events, record=record, progress_cb=progress_cb)

    def _run_relation_pipeline(
        self,
        events: list[Event],
        record: Optional[Any] = None,
        progress_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> EvolutionReport:
        report = self._empty_evolution_report()
        source_text = self._extract_relation_source_text(record=record, events=events)
        if not source_text:
            source_text = " ".join(event.summary for event in events if event.summary).strip()
        total = len(events)
        self.recall_pipeline.begin_batch()
        try:
            for idx, event in enumerate(events):
                if not event or event.status in {"merged", "archived"}:
                    continue
                if progress_cb:
                    progress_cb("relations", idx + 1, total)
                candidate_set = self.recall_pipeline.recall(event=event)
                report["recall_candidates"] += len(candidate_set.candidates)
                result = self.relation_processor.process(
                    e_new=event,
                    candidates=candidate_set,
                    source_text=source_text,
                )
                report = self._merge_evolution_reports(report, self._process_result_to_report(result))
        finally:
            self.recall_pipeline.end_batch()
        return report

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


    def _process_result_to_report(self, result: ProcessResult) -> EvolutionReport:
        report = self._empty_evolution_report()
        report["event_relation_links"] = int(result.total_links or 0)
        report["updates"] = int(result.updates or 0)
        report["extensions"] = int(result.extensions or 0)
        report["derivations"] = int(result.derivations or 0)
        report["merges"] = int(result.merges or 0)
        report["links"] = int(result.links or 0)
        report["skipped"] = int(result.skipped or 0)
        return report

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

    def _build_context_drafts_from_payload(self, event: Event) -> list[ContextDraft]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        raw_contexts = payload.get("contexts", [])
        if not isinstance(raw_contexts, list) or not raw_contexts:
            return []

        event_timestamp = int(event.valid_from or event.timestamp or event.last_active or 0)
        drafts: list[ContextDraft] = []
        for item in raw_contexts:
            if not isinstance(item, dict):
                continue
            condition = str(item.get("condition", item.get("summary", "")) or "").strip()
            summary = str(item.get("summary", condition) or "").strip()
            subject = str(item.get("subject", "") or "").strip()
            facts = item.get("facts", {})
            if not isinstance(facts, dict):
                facts = {}
            applies_when = str(item.get("applies_when", "") or "").strip()
            description = str(item.get("description", "") or "").strip()
            evidence_span = str(item.get("evidence_span", "") or "").strip()
            try:
                confidence = float(item.get("confidence", 0.6) or 0.6)
            except (TypeError, ValueError):
                confidence = 0.6
            drafts.append(
                ContextDraft(
                    subtype=str(item.get("subtype", "situation") or "situation"),
                    summary=summary,
                    description=render_context_description(
                        condition=condition,
                        facts=facts,
                        applies_when=applies_when,
                        fallback=description,
                    ),
                    subject=subject,
                    condition=condition or summary,
                    facts=facts,
                    applies_when=applies_when,
                    confidence=confidence,
                    evidence_span=evidence_span or summary,
                    source_refs=[
                        {
                            "source": "event_payload_context",
                            "evidence_span": evidence_span or summary,
                            "signal": "event_payload_context",
                            "event_id": event.id,
                            "timestamp": event_timestamp,
                            "subject": subject,
                            "condition": condition or summary,
                            "facts": facts,
                            "applies_when": applies_when,
                        }
                    ],
                    valid_from=event_timestamp,
                )
            )

        validator = getattr(self.context_extractor, "validate_context_drafts", None)
        if callable(validator):
            record_text = str(payload.get("episode_text", "") or event.summary or event.action or "").strip()
            drafts = validator(drafts, record_text, event)

        canonicalize = getattr(self.context_extractor, "canonicalize_context", None)
        if callable(canonicalize):
            drafts = [
                canonicalize(draft)
                for draft in drafts
                if isinstance(draft, ContextDraft)
            ]

        dedupe = getattr(self.context_extractor, "_dedupe_drafts", None)
        if callable(dedupe):
            drafts = dedupe(drafts)

        return self._filter_valid_context_drafts(drafts)

    def _resolve_context_pairs_for_event_batch(
        self,
        events: list[Event],
        record: Optional[Any],
    ) -> list[list[tuple[Context, ContextDraft]]]:
        if not events:
            return []

        drafts_by_index: dict[int, list[ContextDraft]] = {}
        for idx, event in enumerate(events):
            payload = event.payload if isinstance(event.payload, dict) else {}
            raw_contexts = payload.get("contexts")
            if isinstance(raw_contexts, list) and raw_contexts:
                drafts_by_index[idx] = self._build_context_drafts_from_payload(event)

        if len(drafts_by_index) == len(events):
            resolved_batches: list[list[tuple[Context, ContextDraft]]] = []
            for idx, event in enumerate(events):
                drafts = drafts_by_index.get(idx, [])
                resolved_batches.append(
                    [(self.resolve_context(draft, event=event), draft) for draft in drafts]
                )
            return resolved_batches

        batched_extract = getattr(self.context_extractor, "extract_batch", None)
        max_batch_size = max(1, int(self.config.context_extraction_batch_size or 1))
        missing_indices = [idx for idx in range(len(events)) if idx not in drafts_by_index]
        if callable(batched_extract) and len(missing_indices) > 1:
            for start in range(0, len(missing_indices), max_batch_size):
                batch_indices = missing_indices[start : start + max_batch_size]
                batch_events = [events[idx] for idx in batch_indices]
                batch_records = [record if record is not None else events[idx] for idx in batch_indices]
                try:
                    batch_drafts = batched_extract(
                        records=batch_records,
                        events=batch_events,
                        existing_contexts_by_index=None,
                    )
                    if len(batch_drafts) != len(batch_events):
                        raise ValueError(
                            f"expected {len(batch_events)} batch context results, got {len(batch_drafts)}"
                        )
                    for offset, drafts in enumerate(batch_drafts):
                        drafts_by_index[batch_indices[offset]] = self._filter_valid_context_drafts(drafts)
                except Exception as exc:
                    logger.warning(
                        "batch context extraction failed for events[%s:%s]; "
                        "falling back to per-event extraction for this slice: %s",
                        batch_indices[0],
                        batch_indices[-1] + 1,
                        exc,
                    )

        missing_indices = [idx for idx in range(len(events)) if idx not in drafts_by_index]
        if missing_indices:
            workers = self._llm_workers(task_count=len(missing_indices))
            if workers <= 1:
                for idx in missing_indices:
                    drafts_by_index[idx] = self.extract_context_drafts(
                        events[idx],
                        record=record,
                    )
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(
                            self.extract_context_drafts,
                            events[idx],
                            record,
                        ): idx
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
        existing_contexts: Optional[list[dict[str, Any]]] = None,
    ) -> list[ContextDraft]:
        del existing_contexts
        drafts = self.context_extractor.extract(
            record=record or event,
            event=event,
            existing_contexts=None,
        )
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
        match = self.match_existing_context(context_draft, event=event)
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

    def _cached_find_context_candidates(
        self, context_type: str, subtype: str,
    ) -> list[Context]:
        cache_key = (context_type, subtype)
        cached = self._context_candidates_cache.get(cache_key)
        if cached is not None:
            return cached
        result = self.store.find_context_candidates(
            context_type=context_type,
            subtype=subtype,
            limit=self.config.context_candidate_limit,
            only_active=True,
        )
        self._context_candidates_cache[cache_key] = result
        return result

    def _cached_find_contexts_summary_index(
        self, context_type: str,
    ) -> list[tuple[str, str]]:
        cached = self._context_summary_index_cache.get(context_type)
        if cached is not None:
            return cached
        result = self.store.find_contexts_summary_index(
            context_type=context_type,
            only_active=True,
        )
        self._context_summary_index_cache[context_type] = result
        return result

    def _invalidate_context_query_caches(self) -> None:
        self._context_candidates_cache.clear()
        self._context_summary_index_cache.clear()

    def match_existing_context(
        self,
        context_draft: ContextDraft,
        event: Optional[Event] = None,
    ) -> Optional[Context]:
        all_candidates = self._cached_find_context_candidates(
            context_type=context_draft.context_type, subtype="",
        )
        draft_summary_key = self._normalized_context_text(self._context_summary(context_draft))
        seen_candidate_ids = {candidate.id for candidate in all_candidates}
        exact_matches = [
            candidate for candidate in all_candidates
            if self._normalized_context_text(candidate.summary) == draft_summary_key
        ]
        if exact_matches:
            return self._pick_reusable_exact_context(exact_matches, context_draft, event)

        if draft_summary_key:
            global_exact_matches: list[Context] = []
            for context_id, summary in self._cached_find_contexts_summary_index(
                context_type=context_draft.context_type,
            ):
                if context_id in seen_candidate_ids:
                    continue
                if self._normalized_context_text(summary) != draft_summary_key:
                    continue
                context = self.store.get_context(context_id)
                if context is not None:
                    global_exact_matches.append(context)
            if global_exact_matches:
                return self._pick_reusable_exact_context(global_exact_matches, context_draft, event)

        candidates = self._cached_find_context_candidates(
            context_type=context_draft.context_type, subtype=context_draft.subtype,
        )[: self.config.context_reuse_candidate_limit]
        best: Optional[Context] = None
        best_score = -1.0
        for candidate in candidates:
            score = self._context_reuse_score(candidate, context_draft, event)
            if score > best_score:
                best = candidate
                best_score = score
        if best is not None and best_score >= self.config.context_reuse_score_threshold:
            return best

        if not self.config.context_reuse_allow_cross_subtype:
            return None

        # Secondary pass: optionally allow cross-subtype reuse for highly similar contexts.
        cross_candidates = self._cached_find_context_candidates(
            context_type=context_draft.context_type, subtype="",
        )[: self.config.context_reuse_candidate_limit]
        same_subtype_candidate_ids = {item.id for item in candidates}
        for candidate in cross_candidates:
            if candidate.id in same_subtype_candidate_ids:
                continue
            score = self._context_reuse_score(candidate, context_draft, event)
            if score > best_score:
                best = candidate
                best_score = score
        if best is None:
            return None
        return best if best_score >= self.config.context_reuse_score_threshold else None

    def _pick_reusable_exact_context(
        self,
        candidates: list[Context],
        draft: ContextDraft,
        event: Optional[Event],
    ) -> Optional[Context]:
        ranked = sorted(candidates, key=self._context_rank_key, reverse=True)
        if not self.config.context_reuse_gating_enabled or event is None:
            return ranked[0] if ranked else None
        for candidate in ranked:
            if not self._context_reuse_hard_reject(candidate, draft, event):
                return candidate
        return None

    def _context_reuse_score(
        self,
        candidate: Context,
        draft: ContextDraft,
        event: Optional[Event],
    ) -> float:
        if not self.config.context_reuse_gating_enabled:
            return self._context_similarity(candidate, draft)
        if self._context_reuse_hard_reject(candidate, draft, event):
            return 0.0

        summary_overlap = self._context_text_overlap(
            self._context_summary(candidate),
            self._context_summary(draft),
        )
        evidence_overlap = self._context_evidence_overlap(candidate, draft)
        entity_overlap = self._context_event_entity_overlap(candidate, event)
        temporal_score = self._context_temporal_compatibility(candidate, draft)
        subtype_score = 1.0 if self._context_subtype(candidate) == self._context_subtype(draft) else 0.0

        score = (
            self.config.context_reuse_weight_summary * summary_overlap
            + self.config.context_reuse_weight_evidence * evidence_overlap
            + self.config.context_reuse_weight_entity * entity_overlap
            + self.config.context_reuse_weight_temporal * temporal_score
            + self.config.context_reuse_weight_subtype * subtype_score
        )
        weight_sum = (
            self.config.context_reuse_weight_summary
            + self.config.context_reuse_weight_evidence
            + self.config.context_reuse_weight_entity
            + self.config.context_reuse_weight_temporal
            + self.config.context_reuse_weight_subtype
        )
        if weight_sum <= 0.0:
            return 0.0
        return max(0.0, min(1.0, score / weight_sum))

    def _context_reuse_hard_reject(
        self,
        candidate: Context,
        draft: ContextDraft,
        event: Optional[Event],
    ) -> bool:
        if self._context_status(candidate) != "active":
            return True
        if (
            not self.config.context_reuse_allow_cross_subtype
            and self._context_subtype(candidate) != self._context_subtype(draft)
        ):
            return True
        summary_overlap = self._context_text_overlap(
            self._context_summary(candidate),
            self._context_summary(draft),
        )
        if summary_overlap < self.config.context_reuse_min_summary_overlap:
            return True
        if self._has_negation_conflict(candidate, draft):
            return True
        if self.config.context_reuse_require_evidence and event is not None:
            evidence = str(draft.evidence_span or "").strip()
            if not evidence:
                return True
            event_text = self._event_observation_text(event)
            if event_text and not self._context_evidence_matches_event(evidence, event_text):
                return True
            if self._context_evidence_overlap(candidate, draft) < self.config.context_reuse_min_evidence_overlap:
                return True
        return False

    def update_context_with_evidence(
        self,
        context_node: Context,
        evidence: ContextDraft,
        event: Optional[Event],
    ) -> Context:
        del event
        now = max(evidence.valid_from or 0, int(time.time()))
        if not context_node.summary or len(evidence.summary) > len(context_node.summary):
            context_node.summary = evidence.summary
        if not context_node.description:
            context_node.description = evidence.description
        elif evidence.description and len(evidence.description) > len(context_node.description):
            context_node.description = evidence.description
        if evidence.subject and not context_node.subject:
            context_node.subject = evidence.subject
        if evidence.condition and (not context_node.condition or len(evidence.condition) > len(context_node.condition)):
            context_node.condition = evidence.condition
            context_node.summary = context_node.summary or evidence.condition
        if evidence.applies_when and (
            not context_node.applies_when or len(evidence.applies_when) > len(context_node.applies_when)
        ):
            context_node.applies_when = evidence.applies_when
        if evidence.facts:
            context_node.facts = self._merge_context_facts(context_node.facts, evidence.facts)
        context_node.description = render_context_description(
            condition=context_node.condition,
            facts=context_node.facts,
            applies_when=context_node.applies_when,
            fallback=context_node.description,
        )
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
        context_node.embedding = self._maybe_embed_context(self._context_embedding_text(context_node))
        self.store.update_context(context_node)
        return context_node

    def create_context(self, context_draft: ContextDraft) -> Context:
        now = context_draft.valid_from or int(time.time())
        context = context_draft.to_node(
            context_id=self._new_context_id(context_draft),
            timestamp=now,
            embedding=self._maybe_embed_context(self._context_embedding_text(context_draft)),
        )
        self.store.save_context(context)
        self._invalidate_context_query_caches()
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
        llm_gate = self.config.event_consolidation_threshold

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
                    source="embedding_preselect",
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
        llm_gate = self.config.event_consolidation_threshold
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
                    source="embedding_preselect",
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
        return self._context_conflict_ratio(node, new_evidence) >= self.config.context_conflict_threshold

    def handle_context_conflict(self, context_node: Context, evidence: ContextDraft) -> Context:
        now = evidence.valid_from or int(time.time())
        sibling = evidence.to_node(
            context_id=f"{self._new_context_id(evidence)}_sib_{uuid.uuid4().hex[:6]}",
            timestamp=now,
            embedding=self._maybe_embed_context(self._context_embedding_text(evidence)),
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
        llm_min_score = 0.58
        llm_cross_subtype_min_score = 0.62
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
        base_embedding = self._ensure_event_embedding(event)
        if not base_embedding:
            return []
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
        ranked.sort(key=lambda item: (item[0], item[1].last_active or item[1].timestamp or now), reverse=True)
        top_k = max(
            1,
            min(
                int(self.config.event_consolidation_embedding_top_k or 1),
                int(self.config.event_consolidation_candidate_limit or 1),
            ),
        )
        return [candidate for _, candidate in ranked[:top_k]]

    def _event_merge_similarity(
        self,
        event_a: Event,
        event_b: Event,
        now: int,
    ) -> tuple[float, str]:
        embedding_similarity = self._event_embedding_similarity(
            self._ensure_event_embedding(event_a),
            self._ensure_event_embedding(event_b),
        )
        ts_a = event_a.last_active or event_a.timestamp or now
        ts_b = event_b.last_active or event_b.timestamp or now
        diff = abs(ts_a - ts_b)
        window = max(1, int(self.config.event_consolidation_window_seconds or 1))
        temporal_bonus = 0.05 * math.exp(-diff / window)
        score = min(1.0, embedding_similarity + temporal_bonus)
        reasons = ["event_embedding_similarity"]
        if temporal_bonus >= 0.02:
            reasons.append("temporal_proximity")
        return score, ",".join(reasons)

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

    def _context_similarity(self, a: Any, b: Any, *, precomputed_summary_sim: Optional[float] = None) -> float:
        summary_sim = (
            precomputed_summary_sim
            if precomputed_summary_sim is not None
            else self._summary_semantic_similarity(a, b)
        )
        embedding_sim = self._context_embedding_similarity(a, b)
        base = max(summary_sim, embedding_sim)
        if self._normalized_context_text(self._context_summary(a)) == self._normalized_context_text(self._context_summary(b)):
            base = max(base, 1.0)
        subtype_bonus = 0.05 if self._context_subtype(a) == self._context_subtype(b) else 0.0
        if self._context_status(a) != "active" or self._context_status(b) != "active":
            base *= 0.85
        return max(0.0, min(1.0, base + subtype_bonus))

    def _context_merge_score(self, a: Context, b: Context) -> float:
        summary_sim = self._summary_semantic_similarity(a, b)
        return max(
            summary_sim,
            self._context_similarity(a, b, precomputed_summary_sim=summary_sim),
        )

    def _pick_canonical_context(self, left: Context, right: Context) -> tuple[Context, Context]:
        if self._context_rank_key(left) >= self._context_rank_key(right):
            return left, right
        return right, left

    def _context_rank_key(self, context: Context) -> tuple[int, int, int, int]:
        return (
            int(context.support_count or 1),
            1 if self._context_description(context) else 0,
            int(context.last_seen_at or context.updated_at or 0),
            int(context.created_at or 0),
        )

    def _context_conflict_ratio(self, a: Any, b: Any) -> float:
        summary_overlap = self._context_text_overlap(
            self._context_summary(a),
            self._context_summary(b),
        )
        if summary_overlap >= 0.6:
            return 0.0

        description_a = self._context_description(a)
        description_b = self._context_description(b)
        if not description_a or not description_b:
            return 0.0

        description_overlap = self._context_text_overlap(description_a, description_b)
        semantic_similarity = self._context_embedding_similarity(a, b)
        agreement = max(summary_overlap, description_overlap, semantic_similarity)
        if agreement >= self.config.context_reuse_threshold:
            return 0.0
        return max(0.0, 1.0 - agreement)

    def _context_subtype_similarity(self, left: str, right: str) -> float:
        left_norm = str(left or "").strip().lower()
        right_norm = str(right or "").strip().lower()
        return 1.0 if left_norm and left_norm == right_norm else 0.0

    def _normalized_context_text(self, value: Any) -> str:
        return re.sub(r"[\s，,。；;：:、/\\-]+", "", str(value or "").strip().lower())

    def _context_description(self, node: Any) -> str:
        return str(getattr(node, "description", "") or "").strip()

    def _context_subject(self, node: Any) -> str:
        return str(getattr(node, "subject", "") or "").strip()

    def _context_condition(self, node: Any) -> str:
        return str(getattr(node, "condition", "") or getattr(node, "summary", "") or "").strip()

    def _context_applies_when(self, node: Any) -> str:
        return str(getattr(node, "applies_when", "") or "").strip()

    def _context_facts(self, node: Any) -> dict[str, Any]:
        facts = getattr(node, "facts", {}) or {}
        return facts if isinstance(facts, dict) else {}

    def _context_card_text(self, node: Any) -> str:
        parts = [
            f"主体：{self._context_subject(node)}" if self._context_subject(node) else "",
            f"背景条件：{self._context_condition(node)}" if self._context_condition(node) else "",
        ]
        facts = self._context_facts(node)
        if facts:
            parts.append("实况：" + "；".join(f"{key}：{value}" for key, value in facts.items()))
        applies_when = self._context_applies_when(node)
        if applies_when:
            parts.append(f"适用：{applies_when}")
        description = self._context_description(node)
        if description:
            parts.append(description)
        return " ".join(part for part in parts if part).strip()

    def _context_embedding_text(self, context: Any) -> str:
        parts = [self._context_summary(context), self._context_card_text(context)]
        return " ".join(part for part in parts if part).strip()

    def _context_text_overlap(self, left: str, right: str) -> float:
        left_norm = self._normalized_context_text(left)
        right_norm = self._normalized_context_text(right)
        if not left_norm or not right_norm:
            return 0.0
        if left_norm == right_norm:
            return 1.0
        shorter, longer = (left_norm, right_norm) if len(left_norm) <= len(right_norm) else (right_norm, left_norm)
        if shorter and shorter in longer:
            return len(shorter) / max(1, len(longer))
        left_tokens = set(re.findall(r"[\u4e00-\u9fff]{1,4}|[a-z0-9_]+", str(left or "").lower()))
        right_tokens = set(re.findall(r"[\u4e00-\u9fff]{1,4}|[a-z0-9_]+", str(right or "").lower()))
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    def _context_evidence_overlap(self, candidate: Context, draft: ContextDraft) -> float:
        evidence = str(draft.evidence_span or "").strip()
        if not evidence:
            return 0.0
        candidate_parts = [
            self._context_summary(candidate),
            self._context_description(candidate),
            self._context_card_text(candidate),
        ]
        for ref in candidate.source_refs or []:
            if not isinstance(ref, dict):
                continue
            candidate_parts.append(str(ref.get("evidence_span", "") or ""))
            candidate_parts.append(str(ref.get("source", "") or ""))
        candidate_text = " ".join(part for part in candidate_parts if part).strip()
        evidence_norm = self._normalized_context_text(evidence)
        candidate_norm = self._normalized_context_text(candidate_text)
        if evidence_norm and evidence_norm in candidate_norm:
            return 1.0
        return self._context_text_overlap(candidate_text, evidence)

    def _event_observation_text(self, event: Optional[Event]) -> str:
        if event is None:
            return ""
        payload = event.payload if isinstance(event.payload, dict) else {}
        parts = [
            str(payload.get("episode_text", "") or ""),
            str(payload.get("content", "") or ""),
            str(payload.get("text", "") or ""),
            str(event.summary or ""),
            str(event.action or ""),
            str(event.causality or ""),
        ]
        for evidence in event.evidence or []:
            if isinstance(evidence, dict):
                parts.append(str(evidence.get("text", "") or evidence.get("evidence_span", "") or ""))
            else:
                parts.append(str(evidence or ""))
        return " ".join(part for part in parts if part).strip()

    def _context_evidence_matches_event(self, evidence: str, event_text: str) -> bool:
        evidence_norm = self._normalized_context_text(evidence)
        event_norm = self._normalized_context_text(event_text)
        if not evidence_norm:
            return False
        if evidence_norm in event_norm:
            return True
        return self._context_text_overlap(evidence, event_text) >= self.config.context_reuse_min_evidence_overlap

    def _context_event_entity_overlap(self, candidate: Context, event: Optional[Event]) -> float:
        if event is None:
            return 1.0
        event_entities = set(self._participant_labels(event.participants))
        if not event_entities:
            return 1.0
        candidate_text = " ".join(
            part for part in [
                self._context_summary(candidate),
                self._context_description(candidate),
                self._context_card_text(candidate),
            ] if part
        )
        for ref in candidate.source_refs or []:
            if not isinstance(ref, dict):
                continue
            candidate_text += " " + " ".join(str(value or "") for value in ref.values())
        candidate_norm = self._normalized_context_text(candidate_text)
        if not candidate_norm:
            return 1.0
        candidate_mentions_any_entity = any(
            self._normalized_context_text(entity) in candidate_norm
            for entity in event_entities
        )
        if not candidate_mentions_any_entity:
            return 1.0
        matched = [entity for entity in event_entities if self._normalized_context_text(entity) in candidate_norm]
        return len(matched) / max(1, len(event_entities))

    def _has_negation_conflict(self, candidate: Context, draft: ContextDraft) -> bool:
        left = f"{self._context_summary(candidate)} {self._context_card_text(candidate)}"
        right = f"{self._context_summary(draft)} {self._context_card_text(draft)} {draft.evidence_span}"
        left_has_negation = self._contains_negation(left)
        right_has_negation = self._contains_negation(right)
        if left_has_negation == right_has_negation:
            return False
        return self._context_text_overlap(left, right) >= self.config.context_reuse_min_summary_overlap

    def _contains_negation(self, text: str) -> bool:
        return bool(re.search(r"不|没|无|非|否|停止|取消|避免|without|not|no|never", str(text or "").lower()))

    def _context_summary(self, node: Any) -> str:
        return str(getattr(node, "summary", "") or "").strip()

    def _context_subtype(self, node: Any) -> str:
        return str(getattr(node, "subtype", "") or "").strip()

    def _context_status(self, node: Any) -> str:
        return str(getattr(node, "status", "active") or "active").strip()

    def _context_embedding_similarity(self, left: Any, right: Any) -> float:
        left_embedding = getattr(left, "embedding", None)
        right_embedding = getattr(right, "embedding", None)
        if not left_embedding:
            left_embedding = self._maybe_embed_context(self._context_embedding_text(left))
            if left_embedding is not None and hasattr(left, "embedding"):
                try:
                    left.embedding = left_embedding
                except Exception:
                    pass
        if not right_embedding:
            right_embedding = self._maybe_embed_context(self._context_embedding_text(right))
            if right_embedding is not None and hasattr(right, "embedding"):
                try:
                    right.embedding = right_embedding
                except Exception:
                    pass
        if not left_embedding or not right_embedding:
            return 0.0
        return self._event_embedding_similarity(left_embedding, right_embedding)

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

    def _merge_context_facts(
        self,
        existing: Optional[dict[str, Any]],
        incoming: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        merged = dict(existing or {})
        for key, value in dict(incoming or {}).items():
            text_key = str(key or "").strip()
            if not text_key or value in (None, "", [], {}):
                continue
            if text_key not in merged or merged.get(text_key) in (None, "", [], {}):
                merged[text_key] = value
        return merged

    def _maybe_embed_context(self, text: str) -> Optional[list[float]]:
        if not text:
            return None
        cache_key = text.strip()
        cached = self._context_embedding_cache.get(cache_key)
        if cached is not None:
            return cached
        embedding_client = getattr(self.store, "embedding_client", None)
        try:
            if embedding_client is not None and hasattr(embedding_client, "get_embedding"):
                result = embedding_client.get_embedding(text)
            else:
                result = self.llm_client.embed_text(text=text)
        except Exception:
            return None
        if result is not None:
            self._context_embedding_cache[cache_key] = result
        return result

    def _participant_labels(self, participants: list[Any]) -> list[str]:
        if not isinstance(participants, list):
            return []
        labels = []
        for item in participants:
            seat = ""
            if isinstance(item, dict):
                label = str(
                    item.get("role")
                    or item.get("name")
                    or item.get("id")
                    or item.get("value")
                    or item.get("actor")
                    or item.get("label")
                    or ""
                ).strip()
                seat = str(item.get("seat", "") or "").strip()
            else:
                label = str(item or "").strip()
            label = "/".join([part for part in [label, seat] if part])
            if label:
                labels.append(label)
        return sorted(set(labels))

    def _participant_entity_ref(self, participant: Any) -> Optional[tuple[str, str]]:
        if isinstance(participant, dict):
            entity_name = str(
                participant.get("id")
                or participant.get("name")
                or participant.get("role")
                or participant.get("value")
                or participant.get("actor")
                or participant.get("label")
                or ""
            ).strip()
            entity_type = str(participant.get("type", "UNKNOWN") or "UNKNOWN").strip() or "UNKNOWN"
        else:
            entity_name = str(participant or "").strip()
            entity_type = "UNKNOWN"
        if not entity_name:
            return None
        return entity_name, entity_type

    def _summary_semantic_similarity(self, a: Any, b: Any) -> float:
        left = self._normalized_context_text(self._context_summary(a))
        right = self._normalized_context_text(self._context_summary(b))
        if left and left == right:
            return 1.0
        left_embedding = self._maybe_embed_context(self._context_summary(a))
        right_embedding = self._maybe_embed_context(self._context_summary(b))
        if not left_embedding or not right_embedding:
            return 0.0
        return self._event_embedding_similarity(left_embedding, right_embedding)

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
        return self.relation_processor.merge_events(
            canonical_event_id=canonical_event_id,
            merged_event_id=merged_event_id,
            merged_at=merged_at,
            similarity_score=similarity_score,
            merge_reason=merge_reason,
        )

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
        original_canonical_summary = str(canonical.summary or "").strip()
        original_merged_summary = str(merged.summary or "").strip()
        original_canonical_description = self._context_description(canonical)
        original_merged_description = self._context_description(merged)
        if (rewrite_strategy or "rewrite").strip().lower() == "rewrite":
            canonical.summary = self._rewrite_merged_context_summary(canonical, merged)
        else:
            canonical.summary = canonical.summary or merged.summary
        if not canonical.description:
            canonical.description = merged.description
        elif merged.description and len(merged.description) > len(canonical.description):
            canonical.description = merged.description
        if merged.subject and not canonical.subject:
            canonical.subject = merged.subject
        if merged.condition and (not canonical.condition or len(merged.condition) > len(canonical.condition)):
            canonical.condition = merged.condition
        if merged.applies_when and (not canonical.applies_when or len(merged.applies_when) > len(canonical.applies_when)):
            canonical.applies_when = merged.applies_when
        canonical.facts = self._merge_context_facts(canonical.facts, merged.facts)
        canonical.description = render_context_description(
            condition=canonical.condition,
            facts=canonical.facts,
            applies_when=canonical.applies_when,
            fallback=canonical.description,
        )
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
                    "canonical_condition_after": canonical.condition,
                    "canonical_facts_after": canonical.facts,
                    "canonical_applies_when_after": canonical.applies_when,
                    "canonical_description_before": original_canonical_description,
                    "merged_description_before": original_merged_description,
                    "canonical_description_after": canonical.description,
                    "merged_at": ts,
                }
            ],
        )
        canonical.merged_from = sorted(set(canonical.merged_from + [merged.id] + merged.merged_from))
        canonical.embedding = self._maybe_embed_context(self._context_embedding_text(canonical))
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
        candidates = [
            str(canonical.summary or "").strip(),
            str(merged.summary or "").strip(),
        ]
        candidates = [item for item in candidates if item]
        if not candidates:
            return (self._context_subtype(canonical) or self._context_subtype(merged) or "situation")[:128]
        return max(candidates, key=len)[:128]

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
        return "llm"

    def _llm_merge_available(self) -> bool:
        return True

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
            "description": context.description,
            "subject": context.subject,
            "condition": context.condition,
            "facts": context.facts,
            "applies_when": context.applies_when,
            "memory_card": self._context_card_text(context),
            "confidence": context.confidence,
            "support_count": context.support_count,
            "last_seen_at": context.last_seen_at,
            "source_refs": context.source_refs[:3],
        }

    def _new_context_id(self, context: Any) -> str:
        summary = self._context_summary(context)
        subtype = self._context_subtype(context) or "situation"
        signature = f"context|{subtype}|{summary}"
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
        query_embedding = self._maybe_embed_context(query)
        event_embedding = event_data.get("embedding")
        if not event_embedding and event_id and hasattr(self.store, "get_event"):
            event = self.store.get_event(event_id)
            if event is not None:
                event_embedding = self._ensure_event_embedding(event)
        if not event_embedding and summary:
            event_embedding = self._maybe_embed_context(summary)
        event_similarity = self._event_embedding_similarity(query_embedding, event_embedding)

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

        evolution_score = (
            self.config.retrieval_weight_event_sim * event_similarity
            + self.config.retrieval_weight_context * context_match
            + self.config.retrieval_weight_recency * recency
            + self.config.retrieval_weight_validity * validity
            + self.config.retrieval_weight_support * support_norm
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
            "decay_penalty": 1.0 - recency,
            "drift_penalty": 0.0,
            "compressed_contexts": [c.summary for c in contexts[:2]],
        }

    def _context_query_match(self, contexts: list[Context], query_entities: list[str], query: str) -> float:
        del query_entities
        if not contexts:
            return 0.0
        query_embedding = self._maybe_embed_context(query)
        if not query_embedding:
            return 0.0
        best = 0.0
        for c in contexts:
            context_embedding = c.embedding or self._maybe_embed_context(self._context_embedding_text(c))
            score = self._event_embedding_similarity(query_embedding, context_embedding)
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
            RETURN c.id, c.context_type, c.subtype, c.summary, c.description,
                   c.subject, c.condition, c.facts, c.applies_when,
                   c.confidence, c.support_count, c.created_at, c.updated_at,
                   c.valid_from, c.valid_to, c.last_seen_at, c.status, c.embedding
            """
        )
        cols = [
            "id", "context_type", "subtype", "summary", "description",
            "subject", "condition", "facts", "applies_when",
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
