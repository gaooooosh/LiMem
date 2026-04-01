# -*- coding: utf-8 -*-
"""Shared DashScope client used across generation and embedding call sites."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

try:
    from dashscope import Generation, TextEmbedding
except Exception:  # pragma: no cover - optional dependency
    Generation = None
    TextEmbedding = None

from ..config import DASHSCOPE_API_KEY, DASHSCOPE_BASE_URL, normalize_dashscope_base_url
from ..utils import robust_json_loads


class DashScopeClient:
    """Unified wrapper for DashScope generation and embedding calls."""

    _PLACEHOLDER_API_KEYS = frozenset({"YOUR_API_KEY", "sk-xxx"})

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        generation_api: Any = Generation,
        embedding_api: Any = TextEmbedding,
        generation_api_resolver: Optional[Callable[[], Any]] = None,
        embedding_api_resolver: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = normalize_dashscope_base_url(base_url or DASHSCOPE_BASE_URL)
        self._generation_api = generation_api
        self._embedding_api = embedding_api
        self._generation_api_resolver = generation_api_resolver
        self._embedding_api_resolver = embedding_api_resolver

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
        return self._resolve_generation_api() is not None

    def has_embedding_api(self) -> bool:
        return self._resolve_embedding_api() is not None

    def call_generation(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **kwargs,
    ) -> Any:
        generation_api = self._resolve_generation_api()
        if generation_api is None:
            raise ImportError("dashscope Generation API is unavailable.")
        return self._invoke_api(
            generation_api,
            api_key=self.api_key,
            base_address=self.base_url,
            model=model,
            messages=messages,
            **kwargs,
        )

    def call_generation_from_prompts(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        **kwargs,
    ) -> Any:
        return self.call_generation(
            model=model,
            messages=self.build_messages(system_prompt, user_message),
            **kwargs,
        )

    def call_generation_json(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        default: Any,
        **kwargs,
    ) -> Any:
        response = self.call_generation_from_prompts(
            model=model,
            system_prompt=system_prompt,
            user_message=user_message,
            **kwargs,
        )
        if not self.is_success(response):
            return default
        return robust_json_loads(self.message_content(response), default)

    def call_embedding(
        self,
        model: str,
        input_data: str | list[str],
        **kwargs,
    ) -> Any:
        embedding_api = self._resolve_embedding_api()
        if embedding_api is None:
            raise ImportError("dashscope TextEmbedding API is unavailable.")
        return self._invoke_api(
            embedding_api,
            api_key=self.api_key,
            base_address=self.base_url,
            model=model,
            input=input_data,
            **kwargs,
        )

    def embed_text(self, model: str, text: str) -> list[float]:
        embeddings = self.embed_texts(model=model, texts=[text])
        return embeddings[0] if embeddings else []

    def embed_texts(self, model: str, texts: list[str]) -> list[list[float]]:
        response = self.call_embedding(model=model, input_data=texts)
        return self.embedding_vectors(response)

    @staticmethod
    def is_success(response: Any) -> bool:
        return getattr(response, "status_code", None) == 200

    @staticmethod
    def error_summary(response: Any) -> str:
        return (
            f"status={getattr(response, 'status_code', None)} "
            f"code={getattr(response, 'code', None)} "
            f"message={getattr(response, 'message', None)}"
        )

    @staticmethod
    def message_content(response: Any, default: str = "") -> str:
        output = getattr(response, "output", None)
        if isinstance(output, dict):
            choices = output.get("choices", []) or []
        else:
            choices = getattr(output, "choices", []) or []
        if not choices:
            return default
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            message = first_choice.get("message", {})
        else:
            message = getattr(first_choice, "message", None)
        if isinstance(message, dict):
            content = message.get("content", default)
        else:
            content = getattr(message, "content", default)
        return str(content or default)

    @staticmethod
    def embedding_vectors(response: Any) -> list[list[float]]:
        output = getattr(response, "output", None)
        raw_embeddings = output.get("embeddings", []) if isinstance(output, dict) else getattr(output, "embeddings", [])

        indexed_embeddings: list[tuple[int, list[float]]] = []
        for idx, item in enumerate(raw_embeddings or []):
            if isinstance(item, dict):
                text_index = item.get("text_index", item.get("textIndex", idx))
                embedding = item.get("embedding") or []
            else:
                text_index = getattr(item, "text_index", getattr(item, "textIndex", idx))
                embedding = getattr(item, "embedding", []) or []
            indexed_embeddings.append((int(text_index), list(embedding)))

        indexed_embeddings.sort(key=lambda pair: pair[0])
        return [embedding for _, embedding in indexed_embeddings]

    def _resolve_generation_api(self) -> Any:
        if self._generation_api_resolver is not None:
            return self._generation_api_resolver()
        return self._generation_api

    def _resolve_embedding_api(self) -> Any:
        if self._embedding_api_resolver is not None:
            return self._embedding_api_resolver()
        return self._embedding_api

    @staticmethod
    def _invoke_api(api: Any, **kwargs) -> Any:
        call = getattr(api, "call", None)
        if call is None:
            raise AttributeError(f"{api!r} does not expose a call method.")
        signature = inspect.signature(call)
        supports_var_kwargs = any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in signature.parameters.values()
        )
        if supports_var_kwargs:
            filtered_kwargs = kwargs
        else:
            filtered_kwargs = {
                key: value for key, value in kwargs.items() if key in signature.parameters
            }
        return call(**filtered_kwargs)
