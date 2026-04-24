# -*- coding: utf-8 -*-
"""LTMemoryImpl - 长时记忆系统实现

整合 Builder + Evolution + Store 的完整实现。
"""

from typing import Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from .core.episode import Episode
from .core.event import Event
from .core.memory import LTMemory, IngestResult
from .builder.memory_builder import MemoryBuilder
from .storage.graph_store import GraphStore
from .evolution.dynamic_engine import EvolutionReport
from .config import EPISODE_TTL, DECAY_RATE
from .ops import MemoryGraphOps


class LTMemoryImpl(LTMemory):
    """长时记忆系统实现

    职责：整合 Builder + Evolution + Store，提供统一的记忆系统接口。

    核心功能：
    - ingest: 记忆构建（Episode → Event）
    - retrieve_memories: 动态演化检索压缩输出
    - cleanup: 过期数据清理
    """

    def __init__(
        self,
        store: GraphStore,
        builder: MemoryBuilder,
        episode_ttl: int = EPISODE_TTL,
        decay_rate: float = DECAY_RATE,
        dynamic_engine=None,
    ):
        """初始化长时记忆系统

        Args:
            store: 图存储接口
            builder: 记忆构建器
            episode_ttl: Episode生存时间（秒）
            decay_rate: 权重衰减率
        """
        self.store = store
        self.builder = builder
        self.episode_ttl = episode_ttl
        self.decay_rate = decay_rate
        self.dynamic_engine = dynamic_engine
        self.ops = MemoryGraphOps(store=store, dynamic_engine=dynamic_engine)

    def ingest(self, episode: Episode) -> IngestResult:
        """摄入Episode

        这是记忆构建的核心入口。

        Args:
            episode: 原始对话片段

        Returns:
            IngestResult 包含事件和构建信息
        """
        return self.builder.build(episode)

    def evolve_events(self, events: list[Event], progress_cb=None) -> EvolutionReport:
        """Run dynamic evolution for already-persisted events."""
        if not self.dynamic_engine or not events:
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
        return self.dynamic_engine.evolve_existing_events(events, progress_cb=progress_cb)

    def ingest_batch(
        self,
        episodes: list[Episode],
        concurrency: int = 4,
        progress_cb=None,
    ) -> list[IngestResult | Exception]:
        """Batch ingest with parallel LLM extraction and serial DB writes.

        Args:
            episodes: Episodes to ingest.
            concurrency: Max parallel LLM extraction workers.
            progress_cb: Optional callback(idx, total, result_or_error) per episode.

        Returns:
            List of IngestResult or Exception (one per episode, same order).
        """
        if not episodes:
            return []

        workers = max(1, min(concurrency, len(episodes)))

        # Phase 1: parallel LLM extraction (no DB writes)
        bundles = [None] * len(episodes)
        errors = [None] * len(episodes)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self.builder.extract_only, ep): idx
                for idx, ep in enumerate(episodes)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    bundles[idx] = future.result()
                except Exception as exc:
                    errors[idx] = exc

        # Phase 2: serial DB persistence (thread-safe for single connection)
        results: list[IngestResult | Exception] = [
            RuntimeError("ingest_batch result not set") for _ in episodes
        ]
        for idx in range(len(episodes)):
            if errors[idx] is not None:
                results[idx] = errors[idx]
                if progress_cb:
                    progress_cb(idx, len(episodes), errors[idx])
                continue
            try:
                result = self.builder.persist_extraction(bundles[idx])
                if result is None:
                    raise RuntimeError("persist_extraction returned None")
                results[idx] = result
            except Exception as exc:
                errors[idx] = exc
                results[idx] = exc
            if progress_cb:
                progress_cb(idx, len(episodes), results[idx] or errors[idx])

        return results

    def get_event(self, event_id: str) -> Optional[Event]:
        """获取单个事件

        Args:
            event_id: 事件ID

        Returns:
            Event实例，如果不存在则返回None
        """
        return self.store.get_event(event_id)

    def get_related_entities(self, event_id: str) -> list[str]:
        """获取事件关联的实体

        Args:
            event_id: 事件ID

        Returns:
            实体ID列表
        """
        return self.store.get_event_entities(event_id)

    def cleanup(self, current_time: int) -> int:
        """清理过期的临时数据

        Args:
            current_time: 当前时间戳

        Returns:
            清理的数据数量
        """
        count = self.store.delete_expired_episodes(current_time, self.episode_ttl)
        if count > 0:
            print(f"🗑️ Cleaned up {count} old episodes")
        return count

    def get_stats(self) -> dict[str, Any]:
        """获取系统统计信息

        Returns:
            包含事件数、实体数等统计信息的字典
        """
        return self.store.get_stats()

    def retrieve_memories(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Evolution-aware retrieval output for edge-side small models."""
        if not self.dynamic_engine:
            return []
        return self.dynamic_engine.retrieve_memories(query=query, top_k=top_k)

    def run_consolidation(self, dry_run: bool = False, strategy: str = "auto") -> dict[str, int]:
        if not self.dynamic_engine:
            return {}
        return self.dynamic_engine.run_consolidation(dry_run=dry_run, strategy=strategy)

    def write(
        self,
        item: Any,
        kind: str = "",
        evolve: bool = True,
        entity_ids: Optional[list[Any]] = None,
    ) -> dict[str, Any]:
        return self.ops.write(
            item=item,
            kind=kind,
            evolve=evolve,
            entity_ids=entity_ids,
        )

    def remove(
        self,
        memory_id: str,
        kind: str = "event",
        hard_delete: bool = False,
        removed_at: Optional[int] = None,
    ) -> dict[str, Any]:
        return self.ops.remove(
            memory_id=memory_id,
            kind=kind,
            hard_delete=hard_delete,
            removed_at=removed_at,
        )

    def merge_event(
        self,
        canonical_event_id: str,
        merged_event_id: str,
        merged_at: Optional[int] = None,
        similarity_score: float = 1.0,
        merge_reason: str = "manual_merge",
    ) -> dict[str, Any]:
        return self.ops.merge_event(
            canonical_event_id=canonical_event_id,
            merged_event_id=merged_event_id,
            merged_at=merged_at,
            similarity_score=similarity_score,
            merge_reason=merge_reason,
        )

    def merge_context(
        self,
        canonical_context_id: str,
        merged_context_id: str,
        merged_at: Optional[int] = None,
        rewrite_strategy: str = "rewrite",
    ) -> dict[str, Any]:
        return self.ops.merge_context(
            canonical_context_id=canonical_context_id,
            merged_context_id=merged_context_id,
            merged_at=merged_at,
            rewrite_strategy=rewrite_strategy,
        )

    def query(
        self,
        text: str = "",
        limit: int = 20,
        include_graph: bool = True,
        include_inactive: bool = False,
    ) -> dict[str, Any]:
        return self.ops.query(
            text=text,
            limit=limit,
            include_graph=include_graph,
            include_inactive=include_inactive,
        )

    def snapshot(
        self,
        limit: int = 20,
        include_inactive: bool = False,
        text: str = "",
    ) -> dict[str, Any]:
        return self.ops.snapshot(
            limit=limit,
            include_inactive=include_inactive,
            text=text,
        )

    def auto_merge(
        self,
        scope: str = "all",
        strategy: str = "auto",
        dry_run: bool = False,
        max_pairs: int = 10,
        focus_event_ids: Optional[list[str]] = None,
        event_same_scope_only: bool = False,
    ) -> dict[str, Any]:
        return self.ops.auto_merge(
            scope=scope,
            strategy=strategy,
            dry_run=dry_run,
            max_pairs=max_pairs,
            focus_event_ids=focus_event_ids,
            event_same_scope_only=event_same_scope_only,
        )

    # ==================== 便捷方法 ====================

    def ingest_text(self, text: str, timestamp: Optional[int] = None) -> IngestResult:
        """从文本创建并摄入Episode

        便捷方法，自动创建Episode对象。

        Args:
            text: 对话文本
            timestamp: 时间戳（默认使用当前时间）

        Returns:
            IngestResult
        """
        ts = timestamp or int(time.time())
        episode = Episode(content=text, timestamp=ts)
        return self.ingest(episode)

    def visualize(
        self,
        output_path: Optional[str] = None,
        title: str = "LiMem 图拓扑可视化",
        max_events: int = 100,
        max_entities: int = 50,
        max_contexts: int = 20,
    ) -> str:
        """生成图拓扑可视化

        Args:
            output_path: 输出 HTML 文件路径，None 则使用默认路径 (<db_dir>/viz/graph_topology.html)
            title: 页面标题
            max_events: 最大事件节点数
            max_entities: 最大实体节点数
            max_contexts: 最大上下文节点数

        Returns:
            输出文件的绝对路径

        Example:
            ltm = create_ltm(db_path="./my_memory.kz")
            html_path = ltm.visualize("./viz/graph.html")
            print(f"可视化已保存: {html_path}")
        """
        from .visualization import GraphVisualizer, VisualizationConfig

        config = VisualizationConfig(
            max_events=max_events,
            max_entities=max_entities,
            max_contexts=max_contexts,
        )

        visualizer = GraphVisualizer(self.store.db_path, config=config)

        if output_path is None:
            from pathlib import Path
            output_path = str(Path(self.store.db_path).parent / "viz" / "graph_topology.html")

        return visualizer.export_html(output_path, title=title)

    def get_visualization_stats(self) -> dict[str, Any]:
        """获取可视化相关的图统计信息

        Returns:
            包含节点和边统计的字典
        """
        from .visualization import GraphVisualizer

        visualizer = GraphVisualizer(self.store.db_path)
        return visualizer.get_stats()
