# -*- coding: utf-8 -*-
"""Provider-specific request adapters for OpenAI-compatible LLM APIs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional


class LLMRequestAdapter:
    """Normalize chat-completion kwargs for provider-specific model APIs."""

    DEEPSEEK_THINKING_TYPES = {
        "enable": "enabled",
        "enabled": "enabled",
        "true": "enabled",
        "on": "enabled",
        "disable": "disabled",
        "disabled": "disabled",
        "false": "disabled",
        "off": "disabled",
    }

    def adapt_chat_completion_kwargs(
        self,
        *,
        base_url: str,
        model: str,
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        adapted = dict(kwargs)
        adapted.pop("result_format", None)

        enable_thinking = adapted.pop("enable_thinking", None)
        extra_body = deepcopy(adapted.pop("extra_body", {}) or {})
        enable_thinking = self._resolve_enable_thinking(
            base_url=base_url,
            model=model,
            enable_thinking=enable_thinking,
            extra_body=extra_body,
        )

        if enable_thinking is not None:
            if self._uses_deepseek_thinking(model=model, base_url=base_url):
                extra_body["thinking"] = {
                    "type": "enabled" if enable_thinking else "disabled"
                }
            elif self._uses_dashscope_thinking(model=model, base_url=base_url):
                extra_body["enable_thinking"] = bool(enable_thinking)

        if extra_body:
            adapted["extra_body"] = extra_body
        return adapted

    def _resolve_enable_thinking(
        self,
        *,
        base_url: str,
        model: str,
        enable_thinking: Optional[bool],
        extra_body: dict[str, Any],
    ) -> Optional[bool]:
        normalized = self._extract_thinking_from_extra_body(extra_body)
        if normalized is not None:
            return normalized
        if enable_thinking is not None:
            return bool(enable_thinking)
        if self._defaults_to_thinking(model=model, base_url=base_url):
            return False
        return None

    def _extract_thinking_from_extra_body(self, extra_body: dict[str, Any]) -> Optional[bool]:
        thinking = extra_body.pop("thinking", None)
        if isinstance(thinking, dict):
            thinking_type = str(thinking.get("type") or "").strip().lower()
            normalized = self.DEEPSEEK_THINKING_TYPES.get(thinking_type)
            if normalized:
                return normalized == "enabled"
            if thinking_type:
                extra_body["thinking"] = {"type": thinking_type}
        elif thinking is not None:
            normalized = self.DEEPSEEK_THINKING_TYPES.get(str(thinking).strip().lower())
            if normalized:
                return normalized == "enabled"

        dashscope_value = extra_body.pop("enable_thinking", None)
        if dashscope_value is not None:
            return bool(dashscope_value)
        return None

    def _defaults_to_thinking(self, *, model: str, base_url: str) -> bool:
        return self._uses_deepseek_thinking(
            model=model,
            base_url=base_url,
        ) or self._uses_dashscope_thinking(model=model, base_url=base_url)

    @staticmethod
    def _uses_deepseek_thinking(*, model: str, base_url: str) -> bool:
        return "api.deepseek.com" in base_url.lower() and model.lower().startswith("deepseek-v4")

    @staticmethod
    def _uses_dashscope_thinking(*, model: str, base_url: str) -> bool:
        del base_url
        m = model.lower()
        return "qwen3" in m or "qwq" in m
