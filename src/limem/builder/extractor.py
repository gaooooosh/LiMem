# -*- coding: utf-8 -*-
"""LLM Extractor - LLM 提取器抽象

从原始文本中提取结构化信息（事件和实体）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
import time

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
from ..utils import load_prompt, normalize_event_payload, robust_json_loads


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

        if dashscope is None or Generation is None:
            raise ImportError("dashscope is required for TwoStageExtractor. Use HeuristicExtractor in offline mode.")

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
        return normalize_event_payload(data, episode_text=text)

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


class HeuristicExtractor(LLMExtractor):
    """轻量规则提取器（离线模式）。

    用于端侧/测试环境，避免依赖外部 LLM 服务。
    """

    def extract(self, text: str) -> ExtractionResult:
        import re
        now_ts = int(time.time())

        # 规则摘要：优先截取“用户说/车机回答”片段
        summary = text.strip()
        if "->" in summary:
            left, right = summary.split("->", 1)
            summary = f"{left.strip()} -> {right.strip()}"
        summary = summary[:180]

        # 粗粒度 action 判定
        action = "interaction"
        action_hints = [
            ("导航", "navigation"),
            ("播放", "media_play"),
            ("暂停", "media_pause"),
            ("开会", "meeting_mode"),
            ("勿扰", "do_not_disturb"),
            ("温度", "climate"),
            ("空调", "climate"),
            ("风量", "climate"),
        ]
        for token, action_name in action_hints:
            if token in text:
                action = action_name
                break

        # 轻量实体提取：中文连续词 + 英文词 + 数字短词
        candidates = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_]{1,20}|\d{2,}", text)
        stop = {"用户", "车机", "回答", "左右", "时间", "时候"}
        entities = []
        for c in candidates:
            c = c.strip()
            if not c or c in stop:
                continue
            if c not in entities:
                entities.append(c)

        event_data = {
            "summary": summary,
            "action": action,
            "causality": "",
            "time_range": {
                "start": now_ts,
                "end": now_ts,
                "display_time_bucket": "",
            },
            "participants": [{"role": "用户", "seat": ""}],
            "location": {"geo_context": "车内", "digital_context": "车机"},
            "evidence": [{"source": "heuristic", "snippet": text[:160], "timestamp": now_ts, "confidence": 0.7}],
            "consistency": "uncertain",
            "salience": 0.5,
            "confidence": 0.65,
            "source": "heuristic_extractor",
            "event_type": action,
        }
        return ExtractionResult(event_data=event_data, entities=entities, confidence=0.65)
