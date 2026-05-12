# -*- coding: utf-8 -*-
"""Unified LLM client using OpenAI-compatible API for DashScope."""

from __future__ import annotations

from typing import Any, Optional

try:
    from openai import OpenAI as _OpenAI
except Exception:  # pragma: no cover - optional dependency
    _OpenAI = None

from ..config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL
from ..utils import robust_json_loads
from .adapters import LLMRequestAdapter


class DashScopeClient:
    """Unified wrapper using OpenAI-compatible API for generation and embedding."""

    _PLACEHOLDER_API_KEYS = frozenset({"YOUR_API_KEY", "sk-xxx"})

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        generation_model: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = (base_url or DASHSCOPE_BASE_URL).rstrip("/")
        self.generation_model = generation_model
        self.embedding_model = embedding_model
        self._openai_client: Optional[_OpenAI] = None
        self.request_adapter = LLMRequestAdapter()

    def _get_openai_client(self) -> _OpenAI:
        if self._openai_client is None:
            if _OpenAI is None:
                raise ImportError(
                    "openai package is required. Install with: pip install openai"
                )
            self._openai_client = _OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=120.0,
                max_retries=2,
            )
        return self._openai_client

    @staticmethod
    def build_messages(system_prompt: str, user_message: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

    def has_valid_api_key(self) -> bool:
        api_key = str(self.api_key or "").strip()
        return bool(api_key and api_key not in self._PLACEHOLDER_API_KEYS)

    def has_generation_api(self) -> bool:
        return _OpenAI is not None

    def has_embedding_api(self) -> bool:
        return _OpenAI is not None

    def get_embedding(self, text: str) -> list[float]:
        """Convenience method compatible with the old EmbeddingClient interface."""
        return self.embed_text(text=text)

    def call_generation(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        **kwargs,
    ) -> Any:
        resolved_model = model or self.generation_model
        if not resolved_model:
            raise ValueError("No generation model specified.")
        kwargs = self.request_adapter.adapt_chat_completion_kwargs(
            base_url=self.base_url,
            model=resolved_model,
            kwargs=kwargs,
        )
        client = self._get_openai_client()
        response = client.chat.completions.create(
            model=resolved_model,
            messages=messages,
            **kwargs,
        )
        return response

    def call_generation_from_prompts(
        self,
        system_prompt: str,
        user_message: str,
        model: Optional[str] = None,
        **kwargs,
    ) -> Any:
        return self.call_generation(
            model=model,
            messages=self.build_messages(system_prompt, user_message),
            **kwargs,
        )

    def call_generation_json(
        self,
        system_prompt: str,
        user_message: str,
        default: Any,
        model: Optional[str] = None,
        **kwargs,
    ) -> Any:
        try:
            response = self.call_generation_from_prompts(
                model=model,
                system_prompt=system_prompt,
                user_message=user_message,
                **kwargs,
            )
        except Exception as exc:
            self._log_generation_error(exc)
            return default
        return robust_json_loads(self.message_content(response), default)

    def call_embedding(
        self,
        input_data: str | list[str],
        model: Optional[str] = None,
        **kwargs,
    ) -> Any:
        resolved_model = model or self.embedding_model
        if not resolved_model:
            raise ValueError("No embedding model specified.")
        client = self._get_openai_client()
        response = client.embeddings.create(
            model=resolved_model,
            input=input_data,
            **kwargs,
        )
        return response

    def embed_text(self, text: str, model: Optional[str] = None) -> list[float]:
        embeddings = self.embed_texts(texts=[text], model=model)
        return embeddings[0] if embeddings else []

    def embed_texts(self, texts: list[str], model: Optional[str] = None) -> list[list[float]]:
        response = self.call_embedding(input_data=texts, model=model)
        return self.embedding_vectors(response)

    @staticmethod
    def is_success(response: Any) -> bool:
        """Check if an OpenAI response is successful.

        OpenAI client raises exceptions on failure, so if we have a response
        object it's always successful. Kept for backward compatibility.
        """
        return response is not None

    def _log_generation_error(self, exc: Exception) -> None:
        print(f"\u26a0\ufe0f LLM call failed: {exc}")
        print(f"\u26a0\ufe0f base_url={self.base_url}")

    @staticmethod
    def error_summary(response: Any) -> str:
        return str(response)

    @staticmethod
    def message_content(response: Any, default: str = "") -> str:
        try:
            choices = response.choices
            if not choices:
                return default
            content = choices[0].message.content
            return str(content or default)
        except (AttributeError, IndexError):
            return default

    @staticmethod
    def embedding_vectors(response: Any) -> list[list[float]]:
        try:
            data = response.data
            if not data:
                return []
            sorted_data = sorted(data, key=lambda item: item.index)
            return [list(item.embedding) for item in sorted_data]
        except (AttributeError, TypeError):
            return []
