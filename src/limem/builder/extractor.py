# -*- coding: utf-8 -*-
"""LLM Extractor - LLM 提取器抽象

从原始文本中提取结构化信息（事件和实体）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import dashscope
    from dashscope import Generation
except Exception:  # pragma: no cover - optional dependency for offline mode
    dashscope = None
    Generation = None

from ..config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    GENERATION_MODEL,
    ENABLE_THINKING,
)
from ..utils import (
    load_prompt,
    normalize_entity_candidates,
    normalize_event_payload,
    robust_json_loads,
)


@dataclass
class ExtractionResult:
    """提取结果

    Attributes:
        event_data: 事件数据字典
        entities: 实体名称列表
        confidence: 提取置信度
    """

    event_data: dict[str, Any]
    events_data: list[dict[str, Any]] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    confidence: float = 1.0

    def has_valid_event(self) -> bool:
        """检查是否有有效的事件数据"""
        if self.event_data and self.event_data.get("summary"):
            return True
        return any(isinstance(item, dict) and item.get("summary") for item in self.events_data)


class LLMExtractor(ABC):
    """LLM 提取器抽象接口

    职责：从原始文本中提取结构化信息。
    """

    @abstractmethod
    def extract(self, text: str) -> ExtractionResult:
        """执行提取

        Args:
            text: 原始文本（Episode内容）

        Returns:
            ExtractionResult 包含事件数据和实体列表
        """
        pass


class TwoStageExtractor(LLMExtractor):
    """两阶段提取器

    Stage 1: 提取最小动态变化事件
    Stage 2: 提取用于索引的核心实体

    这种分离防止上下文溢出，并允许独立优化。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        generation_model: Optional[str] = None,
        enable_thinking: bool = False,
    ):
        """初始化两阶段提取器

        Args:
            api_key: DashScope API Key
            base_url: DashScope API URL
            generation_model: 生成模型名称
            enable_thinking: 是否启用思维链
        """
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.generation_model = generation_model or GENERATION_MODEL
        self.enable_thinking = enable_thinking or ENABLE_THINKING

        if dashscope is None or Generation is None:
            raise ImportError("dashscope is required for TwoStageExtractor.")

        # 配置 DashScope
        dashscope.base_http_api_url = self.base_url
        if not self.api_key or self.api_key in {"YOUR_API_KEY", "sk-xxx"}:
            raise ValueError("Set DASHSCOPE_API_KEY in .env or environment.")
        dashscope.api_key = self.api_key

        # 加载提示词
        self._event_system_prompt = load_prompt("extract_event_only_system.txt")
        self._event_user_prompt = load_prompt("extract_event_only_user.txt")
        self._entity_system_prompt = load_prompt("extract_entities_only_system.txt")
        self._entity_user_prompt = load_prompt("extract_entities_only_user.txt")

    def extract(self, text: str) -> ExtractionResult:
        """执行两阶段提取

        Args:
            text: 原始文本

        Returns:
            ExtractionResult
        """
        # Stage 1: 提取事件
        events_data = self._extract_events(text)
        event_data = events_data[0] if events_data else {}

        # Stage 2: 提取实体
        entities = self._extract_entities(text)

        return ExtractionResult(
            event_data=event_data,
            events_data=events_data,
            entities=entities,
        )

    def _extract_events(self, text: str) -> list[dict[str, Any]]:
        """Stage 1: 提取事件信息（支持单次输入多事件）

        Args:
            text: 原始文本

        Returns:
            事件数据列表
        """
        user_msg = self._event_user_prompt.format(episode_text=text)

        if self.enable_thinking:
            print("⚠️ enable_thinking requires stream call; ignoring in non-stream mode.")

        resp = Generation.call(
            api_key=self.api_key,
            model=self.generation_model,
            messages=[
                {"role": "system", "content": self._event_system_prompt},
                {"role": "user", "content": user_msg},
            ],
            result_format="message",
            enable_thinking=self.enable_thinking,
        )

        if resp.status_code != 200:
            print(f"⚠️ LLM call failed: status={resp.status_code}")
            print(f"⚠️ code={resp.code} message={resp.message}")
            raise ValueError("LLM call failed. Check model name and API key.")

        content = resp.output.choices[0].message.content
        data = robust_json_loads(content, {})
        if not data:
            raise ValueError(f"Failed to parse event data from LLM output: {content[:200]}")

        normalized: list[dict[str, Any]] = []
        raw_events = self._collect_raw_event_items(data)
        if not raw_events and isinstance(data, dict):
            raw_events = [data]

        for item in raw_events:
            if not isinstance(item, dict):
                continue
            normalized_item = normalize_event_payload({"event": item}, episode_text=text)
            if normalized_item:
                normalized.append(normalized_item)

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in normalized:
            signature = "|".join(
                [
                    str(item.get("summary", "") or "").strip(),
                    str(item.get("action", "") or "").strip(),
                    str(item.get("causality", "") or "").strip(),
                ]
            )
            if signature and signature in seen:
                continue
            if signature:
                seen.add(signature)
            deduped.append(item)
        return deduped

    def _collect_raw_event_items(self, payload: Any) -> list[dict[str, Any]]:
        if payload is None:
            return []
        if isinstance(payload, list):
            result: list[dict[str, Any]] = []
            for item in payload:
                result.extend(self._collect_raw_event_items(item))
            return result
        if not isinstance(payload, dict):
            return []

        result: list[dict[str, Any]] = []
        raw_events = payload.get("events")
        if isinstance(raw_events, list):
            for item in raw_events:
                if isinstance(item, dict):
                    result.append(item)
        raw_event = payload.get("event")
        if isinstance(raw_event, dict):
            result.append(raw_event)
        if result:
            return result

        # Fallback: treat this dict as an event object itself when no wrapper keys exist.
        return [payload]

    def _extract_entities(self, text: str) -> list[str]:
        """Stage 2: 提取实体列表

        Args:
            text: 原始文本

        Returns:
            实体名称列表
        """
        user_msg = self._entity_user_prompt.format(episode_text=text)

        if self.enable_thinking:
            print("⚠️ enable_thinking requires stream call; ignoring in non-stream mode.")

        resp = Generation.call(
            api_key=self.api_key,
            model=self.generation_model,
            messages=[
                {"role": "system", "content": self._entity_system_prompt},
                {"role": "user", "content": user_msg},
            ],
            result_format="message",
            enable_thinking=self.enable_thinking,
        )

        if resp.status_code != 200:
            print(f"⚠️ LLM call failed: status={resp.status_code}")
            print(f"⚠️ code={resp.code} message={resp.message}")
            raise ValueError("LLM call failed. Check model name and API key.")

        content = resp.output.choices[0].message.content
        entities = robust_json_loads(content, [])

        return normalize_entity_candidates(entities, source_text=text)
