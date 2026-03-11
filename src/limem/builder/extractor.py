# -*- coding: utf-8 -*-
"""LLM Extractor - LLM 提取器抽象

从原始文本中提取结构化信息（事件和实体）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import dashscope
from dashscope import Generation

from ..config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    GENERATION_MODEL,
    ENABLE_THINKING,
)
from ..utils import load_prompt, robust_json_loads


@dataclass
class ExtractionResult:
    """提取结果

    Attributes:
        event_data: 事件数据字典
        entities: 实体名称列表
        confidence: 提取置信度
    """

    event_data: dict[str, Any]
    entities: list[str] = field(default_factory=list)
    confidence: float = 1.0

    def has_valid_event(self) -> bool:
        """检查是否有有效的事件数据"""
        return bool(self.event_data and self.event_data.get("summary"))


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

    Stage 1: 提取事件语义（summary, action, causality等）
    Stage 2: 提取实体列表（entities）

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
        event_data = self._extract_event(text)

        # Stage 2: 提取实体
        entities = self._extract_entities(text)

        return ExtractionResult(
            event_data=event_data,
            entities=entities,
        )

    def _extract_event(self, text: str) -> dict[str, Any]:
        """Stage 1: 提取事件信息

        Args:
            text: 原始文本

        Returns:
            事件数据字典
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

        if not data or not isinstance(data, dict):
            raise ValueError(f"Failed to parse event data from LLM output: {content[:200]}")

        # 处理嵌套的 event 字段
        if "event" in data and isinstance(data["event"], dict):
            data = data["event"]

        return data

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

        # 确保返回列表
        if not isinstance(entities, list):
            entities = []

        return entities
