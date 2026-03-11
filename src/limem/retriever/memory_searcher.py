# -*- coding: utf-8 -*-
"""MemorySearcher - 记忆搜索器

编排四阶段检索管道：
1. 实体提取（LLM）
2. 实体匹配（精确 + 模糊）
3. 图路径搜索 → 获取候选事件
4. 加权重排序
5. LLM总结（可选）
"""

from dataclasses import dataclass, field
from typing import Any, Optional
import json
import time

import dashscope
from dashscope import Generation

from ..core.memory import SearchResult
from ..core.event import RankedEvent
from ..config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    GENERATION_MODEL,
    SEARCH_TOP_K,
    SEARCH_MAX_TOKENS,
    SEARCH_TEMPERATURE,
)
from ..utils import load_prompt, robust_json_loads
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
    1. 实体提取（LLM）
    2. 实体匹配（精确 + 包含 + 模糊）
    3. 图路径搜索 → 获取候选事件
    4. 加权重排序
    5. LLM总结（可选）
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
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.generation_model = generation_model or GENERATION_MODEL

        # 配置DashScope
        dashscope.base_http_api_url = self.base_url
        if not self.api_key or self.api_key in {"YOUR_API_KEY", "sk-xxx"}:
            raise ValueError("Set DASHSCOPE_API_KEY in .env or environment.")
        dashscope.api_key = self.api_key

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

        # Stage 1: 实体提取
        entities = self._extract_entities(query)
        print(f"🧩 Extracted entities: {entities}")

        if not entities:
            return SearchResult(
                query=query,
                entities=[],
                ranked_events=[],
                top_k_events=[],
                answer="抱歉，我无法从问题中提取到关键信息。",
            )

        # Stage 2: 实体匹配
        match_results = self.entity_matcher.match(entities)

        if not match_results:
            return SearchResult(
                query=query,
                entities=entities,
                ranked_events=[],
                top_k_events=[],
                answer="抱歉，没有找到匹配的记忆。",
            )

        # Stage 3: 图路径搜索
        raw_events = self._fetch_events(match_results)
        print(f"🔍 Found {len(raw_events)} events from graph")

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

        # Stage 1: 实体提取
        entities = self._extract_entities(query)
        print(f"🧩 Extracted entities: {entities}")

        if not entities:
            return {
                "query": query,
                "entities": [],
                "ranked_events": [],
                "top_k_events": [],
                "answer": "抱歉，我无法从问题中提取到关键信息。",
                "debug": {
                    "entity_count": 0,
                    "raw_event_count": 0,
                    "ranked_event_count": 0,
                },
            }

        # Stage 2: 实体匹配
        match_results = self.entity_matcher.match(entities)

        if not match_results:
            return {
                "query": query,
                "entities": entities,
                "ranked_events": [],
                "top_k_events": [],
                "answer": "抱歉，没有找到匹配的记忆。",
                "debug": {
                    "entity_count": len(entities),
                    "raw_event_count": 0,
                    "ranked_event_count": 0,
                },
            }

        # Stage 3: 图路径搜索
        raw_events = self._fetch_events(match_results)
        print(f"🔍 Found {len(raw_events)} events from graph")

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
                "match_results": {k: {"matched": v.matched_entities, "type": v.match_type}
                                  for k, v in match_results.items()},
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

    def _extract_entities(self, query: str) -> list[str]:
        """从查询中提取实体（LLM）

        Args:
            query: 用户查询

        Returns:
            实体名称列表
        """
        user_msg = self._entity_extraction_user.format(query=query)

        resp = Generation.call(
            api_key=self.api_key,
            model=self.generation_model,
            messages=[
                {"role": "system", "content": self._entity_extraction_system},
                {"role": "user", "content": user_msg},
            ],
            result_format="message",
            enable_thinking=False,
        )

        if resp.status_code != 200:
            print(f"⚠️ Entity extraction failed: {resp.message}")
            return []

        content = resp.output.choices[0].message.content.strip()

        # 处理Markdown代码块
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        # 解析JSON
        entities = robust_json_loads(content, [])

        if not isinstance(entities, list):
            entities = [str(entities)] if entities else []

        # 过滤停用词和单字符
        entities = self._filter_entities(entities)

        return entities

    def _filter_entities(self, entities: list[str]) -> list[str]:
        """过滤实体

        移除停用词和无效的单字符。

        Args:
            entities: 原始实体列表

        Returns:
            过滤后的实体列表
        """
        stop_words = {
            'w', 'sha', 'a', 'an', '的', '了', '吗', '呢', '啊',
            '是', '在', '有', '和', '与', '或', '但', '而', '如', '让', '给', '把', '被'
        }

        valid_entities = []
        for e in entities:
            e_str = str(e).strip()
            if not e_str or e_str in stop_words:
                continue

            # 过滤单字符
            if len(e_str) == 1:
                # 保留数字、英文字母、特定有意义的单字
                if e_str.isdigit() or (e_str.isalpha() and e_str.isascii()):
                    valid_entities.append(e_str)
                elif e_str in {'歌', '书', '车', '家', '去', '听', '看', '放'}:
                    valid_entities.append(e_str)
            else:
                valid_entities.append(e_str)

        # 去重并保持顺序
        seen = set()
        unique = []
        for entity in valid_entities:
            if entity not in seen:
                seen.add(entity)
                unique.append(entity)

        return unique

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

            # 计算此事件的实体匹配权重
            weights = {}
            for entity_id in event_entities:
                if entity_id in matched_db_entities:
                    weights[entity_id] = matched_db_entities[entity_id]

            # 确定匹配类型
            match_type = self._determine_match_type(weights)

            result.append({
                "event_id": event_id,
                "id": event_id,
                "summary": event.summary,
                "action": event.action,
                "causality": event.causality,
                "participants": event.participants,
                "location": event.location,
                "time_range": event.time_range,
                "last_active": event.last_active,
                "t_valid": event.last_active,  # 使用 last_active 作为 t_valid
                "c_valid": 1,  # 默认值，实际应该从关系获取
                "t_expired": None,
                "t_invalid": None,
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

        resp = Generation.call(
            api_key=self.api_key,
            model=self.generation_model,
            messages=[
                {"role": "system", "content": self._answer_generation_system},
                {"role": "user", "content": user_msg},
            ],
            result_format="message",
            enable_thinking=False,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
        )

        if resp.status_code != 200:
            return f"抱歉，生成回答时遇到问题：{resp.message}"

        return resp.output.choices[0].message.content.strip()
