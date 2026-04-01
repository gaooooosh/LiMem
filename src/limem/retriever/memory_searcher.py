# -*- coding: utf-8 -*-
"""MemorySearcher - 记忆搜索器

编排检索管道：
1. Context 图路径搜索 → 获取候选事件
2. 加权重排序
3. LLM总结（可选）
"""

from dataclasses import dataclass, field
from typing import Any, Optional
import json
import time

try:
    import dashscope
    from dashscope import Generation
except Exception:  # pragma: no cover - optional dependency for offline mode
    dashscope = None
    Generation = None

from ..core.memory import SearchResult
from ..core.event import RankedEvent
from ..config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    GENERATION_MODEL,
    SEARCH_TOP_K,
    SEARCH_MAX_TOKENS,
    SEARCH_TEMPERATURE,
    normalize_dashscope_base_url,
)
from ..llm import DashScopeClient
from ..utils import load_prompt, normalize_entity_candidates, robust_json_loads
from .entity_matcher import EntityMatcher, MatchResult
from .ranker import MemoryRanker, RankerConfig


@dataclass
class SearcherConfig:
    """搜索器配置

    Attributes:
        default_top_k: 默认Top-K数量
        max_tokens: LLM生成最大token
        temperature: LLM温度
        generate_answer: 是否生成LLM回答
    """

    default_top_k: int = SEARCH_TOP_K
    max_tokens: int = SEARCH_MAX_TOKENS
    temperature: float = SEARCH_TEMPERATURE
    generate_answer: bool = True


class MemorySearcher:
    """记忆搜索器 - 编排四阶段检索管道

    管道流程：
    1. 图路径搜索 → 获取候选事件
    2. 加权重排序
    3. LLM总结（可选）
    """

    def __init__(
        self,
        store,  # GraphStore
        entity_matcher: EntityMatcher,
        ranker: MemoryRanker,
        config: Optional[SearcherConfig] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        generation_model: Optional[str] = None,
        dynamic_engine=None,
        offline_mode: Optional[bool] = None,
    ):
        """初始化记忆搜索器

        Args:
            store: 图存储接口
            entity_matcher: 实体匹配器
            ranker: 记忆排序器
            config: 搜索器配置
            api_key: DashScope API Key
            base_url: DashScope API URL
            generation_model: 生成模型名称
        """
        self.store = store
        self.entity_matcher = entity_matcher
        self.ranker = ranker
        self.config = config or SearcherConfig()

        # 配置LLM
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = normalize_dashscope_base_url(base_url or DASHSCOPE_BASE_URL)
        self.generation_model = generation_model or GENERATION_MODEL
        self.dynamic_engine = dynamic_engine
        self.offline_mode = False if offline_mode is None else offline_mode
        self.llm_client = DashScopeClient(
            api_key=self.api_key,
            base_url=self.base_url,
            generation_api_resolver=lambda: Generation,
        )

        if (not self.offline_mode) and (not self.llm_client.has_valid_api_key()):
            raise ValueError("Set DASHSCOPE_API_KEY in .env or environment.")
        if (not self.offline_mode) and (not self.llm_client.has_generation_api()):
            raise ImportError("dashscope is required for online MemorySearcher mode.")

        # 加载提示词
        self._entity_extraction_system = load_prompt("entity_extraction_system.txt")
        self._entity_extraction_user = load_prompt("entity_extraction_user.txt")
        self._answer_generation_system = load_prompt("generate_answer_system.txt")
        self._answer_generation_user = load_prompt("generate_answer_user.txt")

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        generate_answer: Optional[bool] = None,
    ) -> SearchResult:
        """执行完整检索管道

        Args:
            query: 用户查询
            top_k: 返回的事件数量
            generate_answer: 是否生成LLM回答

        Returns:
            SearchResult
        """
        k = top_k or self.config.default_top_k
        should_generate = (
            generate_answer if generate_answer is not None
            else self.config.generate_answer
        )

        # Entity extraction is disabled; retrieval is context-first only.
        entities = self._extract_entities(query)
        print(f"🧩 Extracted entities: {entities}")

        # Stage 1/2: Context 语义路 + 排序
        candidate_bundle = self._collect_candidates(query, entities)
        raw_events = candidate_bundle["raw_events"]
        print(
            "🔍 Found "
            f"{candidate_bundle['debug']['merged_candidate_count']} merged candidates "
            f"(entity={candidate_bundle['debug']['entity_route_hit_count']}, "
            f"context={candidate_bundle['debug']['context_route_hit_count']})"
        )

        if not raw_events:
            return SearchResult(
                query=query,
                entities=entities,
                ranked_events=[],
                top_k_events=[],
                answer="抱歉，没有找到相关的事件记忆。",
            )

        # Stage 4: 重排序
        ranked_events, _ = self.ranker.rank(raw_events, top_k=len(raw_events))
        print(f"📊 Ranked {len(ranked_events)} events by weight")

        # Top-K
        top_k_events = ranked_events[:k]

        # Stage 5: LLM总结（可选）
        answer = None
        if should_generate and top_k_events:
            answer = self._generate_answer(query, top_k_events)

        return SearchResult(
            query=query,
            entities=entities,
            ranked_events=ranked_events,
            top_k_events=top_k_events,
            answer=answer,
        )

    def search_debug(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> dict[str, Any]:
        """执行检索并返回详细调试信息

        Args:
            query: 用户查询
            top_k: 返回的事件数量

        Returns:
            包含调试信息的字典
        """
        k = top_k or self.config.default_top_k

        # Entity extraction is disabled; retrieval is context-first only.
        entities = self._extract_entities(query)
        print(f"🧩 Extracted entities: {entities}")
        candidate_bundle = self._collect_candidates(query, entities)
        raw_events = candidate_bundle["raw_events"]
        print(
            "🔍 Found "
            f"{candidate_bundle['debug']['merged_candidate_count']} merged candidates "
            f"(entity={candidate_bundle['debug']['entity_route_hit_count']}, "
            f"context={candidate_bundle['debug']['context_route_hit_count']})"
        )

        # Stage 4: 重排序
        ranked_events, debug_details = self.ranker.rank(
            raw_events, top_k=len(raw_events)
        )
        print(f"📊 Ranked {len(ranked_events)} events by weight")

        # Top-K
        top_k_events = ranked_events[:k]

        # Stage 5: LLM总结
        answer = self._generate_answer(query, top_k_events) if top_k_events else ""

        return {
            "query": query,
            "entities": entities,
            "ranked_events": ranked_events,
            "top_k_events": top_k_events,
            "answer": answer,
            "debug": {
                "entity_count": len(entities),
                "match_results": {
                    key: {"matched": value.matched_entities, "type": value.match_type}
                    for key, value in candidate_bundle["match_results"].items()
                },
                "entity_route_hit_count": candidate_bundle["debug"]["entity_route_hit_count"],
                "context_route_hit_count": candidate_bundle["debug"]["context_route_hit_count"],
                "merged_candidate_count": candidate_bundle["debug"]["merged_candidate_count"],
                "raw_event_count": len(raw_events),
                "ranked_event_count": len(ranked_events),
                "weight_details": [
                    {
                        "event_id": d.event_id,
                        "weight": d.weight,
                        "base_weight": d.base_weight,
                        "temporal_factor": d.temporal_factor,
                        "entity_factor": d.entity_factor,
                    }
                    for d in debug_details[:k]
                ],
            },
        }

    def _collect_candidates(self, query: str, entities: list[str]) -> dict[str, Any]:
        match_results = self.entity_matcher.match(entities) if entities else {}
        entity_raw_events = self._fetch_events(match_results) if match_results else []

        if self.dynamic_engine:
            route_bundle = self.dynamic_engine.retrieve_candidate_events_for_query(
                query=query,
                query_entities=entities,
                limit=max(self.config.default_top_k * 4, 24),
            )
            context_raw_events = self._events_to_raw_rows(route_bundle["context_events"])
        else:
            route_bundle = {
                "entity_events": [],
                "context_events": [],
                "events": [],
            }
            context_raw_events = []

        merged_rows: dict[str, dict[str, Any]] = {}
        for row in entity_raw_events + context_raw_events:
            event_id = row.get("event_id", row.get("id", ""))
            if not event_id:
                continue
            if event_id not in merged_rows:
                merged_rows[event_id] = dict(row)
                continue
            existing = merged_rows[event_id]
            existing["entity_match_weights"] = {
                **existing.get("entity_match_weights", {}),
                **row.get("entity_match_weights", {}),
            }
            existing["c_valid"] = max(existing.get("c_valid", 1), row.get("c_valid", 1))
            existing["t_valid"] = max(existing.get("t_valid", 0), row.get("t_valid", 0))
            if existing.get("match_type") and row.get("match_type"):
                existing["match_type"] = "+".join(
                    sorted(set(existing["match_type"].split("+")) | set(row["match_type"].split("+")))
                )
            elif row.get("match_type"):
                existing["match_type"] = row["match_type"]

        raw_events = list(merged_rows.values())
        if self.dynamic_engine:
            raw_events = self.dynamic_engine.enrich_raw_events_for_retrieval(
                query=query,
                raw_events=raw_events,
                query_entities=entities,
            )

        return {
            "raw_events": raw_events,
            "match_results": match_results,
            "debug": {
                "entity_route_hit_count": len(entity_raw_events),
                "context_route_hit_count": len(route_bundle.get("context_events", [])),
                "merged_candidate_count": len(raw_events),
            },
        }

    def _events_to_raw_rows(self, events: list[Any]) -> list[dict[str, Any]]:
        rows = []
        for event in events:
            rows.append(
                {
                    "event_id": event.id,
                    "id": event.id,
                    "summary": event.summary,
                    "action": event.action,
                    "causality": event.causality,
                    "participants": event.participants,
                    "time_range": event.time_range,
                    "last_active": event.last_active,
                    "t_valid": event.last_active,
                    "c_valid": event.support_count,
                    "t_expired": None,
                    "t_invalid": None,
                    "status": event.status,
                    "support_count": event.support_count,
                    "entity_match_weights": {},
                    "match_type": "context",
                }
            )
        return rows

    def _extract_entities(self, query: str) -> list[str]:
        del query
        return []

    def _extract_entities_fallback(self, query: str) -> list[str]:
        import re
        tokens = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_]{1,20}|\d{2,}", query)
        return self._filter_entities(tokens, source_text=query)

    def _filter_entities(self, entities: Any, source_text: str = "") -> list[str]:
        """过滤并收敛实体粒度。"""
        return normalize_entity_candidates(entities, source_text=source_text)

    def _fetch_events(
        self, match_results: dict[str, MatchResult]
    ) -> list[dict[str, Any]]:
        """根据匹配结果获取事件

        Args:
            match_results: 实体匹配结果

        Returns:
            事件字典列表
        """
        # 获取所有匹配的数据库实体
        matched_db_entities = self.entity_matcher.get_matched_db_entities(match_results)

        if not matched_db_entities:
            return []

        # 从数据库获取事件
        events = self.store.get_events_by_entities(list(matched_db_entities.keys()))

        # 构建事件级别的实体匹配权重
        result = []
        for event in events:
            event_id = event.id
            event_entities = self.store.get_event_entities(event_id)
            relations = self.store.get_event_relations(event_id)
            relation_by_entity = {r.entity_id: r for r in relations}

            # 计算此事件的实体匹配权重
            weights = {}
            best_c_valid = 1
            best_t_valid = event.last_active
            t_expired = None
            t_invalid = None
            for entity_id in event_entities:
                if entity_id in matched_db_entities:
                    weights[entity_id] = matched_db_entities[entity_id]
                    rel = relation_by_entity.get(entity_id)
                    if rel:
                        best_c_valid = max(best_c_valid, rel.c_valid or 1)
                        best_t_valid = max(best_t_valid, rel.t_valid or event.last_active)
                        if rel.t_expired is not None:
                            t_expired = rel.t_expired
                        if rel.t_invalid is not None:
                            t_invalid = rel.t_invalid

            # 确定匹配类型
            match_type = self._determine_match_type(weights)

            result.append({
                "event_id": event_id,
                "id": event_id,
                "summary": event.summary,
                "action": event.action,
                "causality": event.causality,
                "participants": event.participants,
                "time_range": event.time_range,
                "last_active": event.last_active,
                "t_valid": best_t_valid,
                "c_valid": best_c_valid,
                "t_expired": t_expired,
                "t_invalid": t_invalid,
                "status": event.status,
                "support_count": event.support_count,
                "entity_match_weights": weights,
                "match_type": match_type,
            })

        return result

    def _determine_match_type(self, weights: dict[str, float]) -> str:
        """确定匹配类型

        Args:
            weights: 实体匹配权重

        Returns:
            匹配类型字符串
        """
        if not weights:
            return "unknown"

        types = set()
        for w in weights.values():
            if w >= 1.0:
                types.add("exact")
            elif w >= 0.9:
                types.add("containment")
            else:
                types.add("fuzzy")

        return "+".join(sorted(types))

    def _generate_answer(self, query: str, events: list[RankedEvent]) -> str:
        """使用LLM生成回答

        Args:
            query: 用户查询
            events: 排序后的事件列表

        Returns:
            生成的回答
        """
        if not events:
            return "抱歉，我没有找到相关的记忆来回答这个问题。"

        if self.offline_mode:
            bullets = [f"- {e.summary}" for e in events[:3]]
            return "根据当前记忆检索到的相关事件：\n" + "\n".join(bullets)

        # 格式化事件上下文
        events_context = []
        for i, event in enumerate(events):
            event_info = f"事件 {i} (权重: {event.weight:.4f}, 确认次数: {event.c_valid}):\n"
            event_info += f"- 摘要: {event.summary}\n"
            if event.action:
                event_info += f"- 动作: {event.action}\n"
            if event.causality:
                event_info += f"- 因果: {event.causality}\n"
            if event.time_range:
                event_info += f"- 时间: {event.time_range}\n"
            events_context.append(event_info)

        events_str = "\n".join(events_context)

        user_msg = self._answer_generation_user.format(
            events_context=events_str,
            query=query,
        )

        if self.offline_mode or (not self.llm_client.has_generation_api()):
            bullets = [f"- {e.summary}" for e in events[:3]]
            return "根据当前记忆检索到的相关事件：\n" + "\n".join(bullets)

        resp = self.llm_client.call_generation(
            model=self.generation_model,
            messages=self.llm_client.build_messages(
                self._answer_generation_system,
                user_msg,
            ),
            result_format="message",
            enable_thinking=False,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        if not self.llm_client.is_success(resp):
            return f"抱歉，生成回答时遇到问题：{self.llm_client.error_summary(resp)}"

        return self.llm_client.message_content(resp).strip()
