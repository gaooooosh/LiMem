"""LLM-based unified event extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    ENABLE_THINKING,
    GENERATION_MODEL,
    normalize_dashscope_base_url,
)
from ..core.context import normalize_context_subtype
from ..llm import DashScopeClient
from ..utils import load_prompt, normalize_event_payload


@dataclass
class ExtractionResult:
    """Canonical extraction output."""

    event_data: dict[str, Any]
    events_data: list[dict[str, Any]] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    confidence: float = 1.0
    orphan_contexts: list[dict[str, Any]] = field(default_factory=list)

    def has_valid_event(self) -> bool:
        """检查是否有有效的事件数据"""
        if self.event_data and self.event_data.get("summary"):
            return True
        return any(isinstance(item, dict) and item.get("summary") for item in self.events_data)


class LLMExtractor(ABC):
    """Abstract extractor interface."""

    @abstractmethod
    def extract(
        self,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ExtractionResult:
        """执行提取

        Args:
            text: 原始文本（Episode内容）
            metadata: Episode元数据

        Returns:
            ExtractionResult 包含事件数据
        """
        pass


class UnifiedExtractor(LLMExtractor):
    """Single-call extractor that returns coarse events with inline contexts."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        generation_model: Optional[str] = None,
        enable_thinking: bool = False,
        llm_concurrency: int = 1,
        llm_client: Optional[DashScopeClient] = None,
    ):
        self.generation_model = generation_model or GENERATION_MODEL
        self.enable_thinking = enable_thinking or ENABLE_THINKING
        self.llm_concurrency = max(1, int(llm_concurrency or 1))
        if llm_client is not None:
            self.llm_client = llm_client
        else:
            self.api_key = api_key or DASHSCOPE_API_KEY
            self.base_url = normalize_dashscope_base_url(base_url or DASHSCOPE_BASE_URL)
            self.llm_client = DashScopeClient(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        self._system_prompt = load_prompt("extract_unified_system.txt")
        self._user_prompt = load_prompt("extract_unified_user.txt")

    def extract(
        self,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ExtractionResult:
        del metadata
        try:
            events_data, orphan_contexts = self._extract_events(text)
        except Exception:
            events_data, orphan_contexts = [], []
        return ExtractionResult(
            event_data=events_data[0] if events_data else {},
            events_data=events_data,
            entities=[],
            orphan_contexts=orphan_contexts,
        )

    def _extract_events(self, text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        data = self._call_generation_json(
            system_prompt=self._system_prompt,
            user_message=self._user_prompt.format(episode_text=text),
            default={},
        )
        return self._normalize_events(data, text)

    def _normalize_events(
        self,
        payload: Any,
        episode_text: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (valid_events, orphan_contexts)."""
        if not payload:
            return [], []

        orphan_contexts = self._collect_orphan_contexts(payload)
        normalized: list[dict[str, Any]] = []
        for item in self._collect_raw_event_items(payload):
            if not isinstance(item, dict):
                continue
            normalized_item = normalize_event_payload({"event": item}, episode_text=episode_text)
            self._normalize_inline_contexts(normalized_item)
            if self._has_event_semantics(normalized_item):
                normalized.append(normalized_item)
        return self._dedupe_events(normalized), orphan_contexts

    def _collect_orphan_contexts(self, payload: Any) -> list[dict[str, Any]]:
        """Collect orphan_contexts explicitly returned by the LLM without reclassification."""
        if not isinstance(payload, dict):
            return []
        raw = payload.get("orphan_contexts", [])
        if not isinstance(raw, list):
            return []
        return [
            context
            for item in raw
            if isinstance(item, dict)
            for context in [self._normalize_context_payload(item)]
            if context is not None
        ]

    def _normalize_inline_contexts(self, event_payload: dict[str, Any]) -> None:
        raw_contexts = event_payload.get("contexts")
        if not isinstance(raw_contexts, list):
            return
        contexts = [
            context
            for item in raw_contexts
            if isinstance(item, dict)
            for context in [self._normalize_context_payload(item)]
            if context is not None
        ]
        event_payload["contexts"] = contexts

    def _normalize_context_payload(self, item: dict[str, Any]) -> Optional[dict[str, Any]]:
        summary = str(item.get("summary", "") or "").strip()
        if not summary:
            return None
        if self._looks_like_current_intent(item):
            return None
        normalized = dict(item)
        normalized["subtype"] = normalize_context_subtype(item.get("subtype", "situation"))
        normalized["summary"] = summary
        return normalized

    def _looks_like_current_intent(self, item: dict[str, Any]) -> bool:
        text = " ".join(
            str(item.get(field, "") or "")
            for field in ("summary", "evidence_span", "evidence")
        ).lower()
        intent_markers = (
            "想", "希望", "打算", "计划", "准备", "请求", "要求", "需要",
            "我要", "我想", "帮我", "请", "为了", "目标", "意图",
            "want", "wants", "hope", "hopes", "plan", "plans", "intend",
            "intends", "request", "requests", "need", "needs", "goal",
        )
        stable_markers = (
            "长期", "稳定", "习惯", "偏好", "经常", "通常", "常常", "画像",
            "身份", "角色", "能力", "关系", "long-term", "stable", "habit",
            "usually",
        )
        return any(marker in text for marker in intent_markers) and not any(
            marker in text for marker in stable_markers
        )

    def _has_event_semantics(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        return any(str(payload.get(field, "") or "").strip() for field in ("summary", "action", "causality"))

    def _dedupe_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in events:
            parts = (
                str(item.get("summary", "") or "").strip(),
                str(item.get("action", "") or "").strip(),
                str(item.get("causality", "") or "").strip(),
            )
            signature = parts if any(parts) else None
            if signature is not None and signature in seen:
                continue
            if signature is not None:
                seen.add(signature)
            deduped.append(item)
        return deduped

    def _call_generation_json(
        self,
        system_prompt: str,
        user_message: str,
        default: Any,
    ) -> Any:
        return self.llm_client.call_generation_json(
            system_prompt=system_prompt,
            user_message=user_message,
            default=default,
            model=self.generation_model,
        )

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

        return [payload]
