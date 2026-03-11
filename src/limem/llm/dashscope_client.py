# -*- coding: utf-8 -*-
"""DashScope Client - 阿里云百炼 LLM 客户端

提供统一的 LLM 和 Embedding 服务接口。
"""

from typing import Optional, List, Any
import json

import dashscope
from dashscope import Generation, TextEmbedding

from ..config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    GENERATION_MODEL,
    EMBEDDING_MODEL,
    ENABLE_THINKING,
)
from ..utils import robust_json_loads


class DashScopeClient:
    """DashScope LLM 客户端

    提供统一的 LLM 调用接口。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        generation_model: Optional[str] = None,
        enable_thinking: bool = False,
    ):
        """初始化客户端

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

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        **kwargs,
    ) -> str:
        """生成文本

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            max_tokens: 最大生成token
            temperature: 温度参数

        Returns:
            生成的文本
        """
        if self.enable_thinking:
            print("⚠️ enable_thinking requires stream call; ignoring in non-stream mode.")

        resp = Generation.call(
            api_key=self.api_key,
            model=self.generation_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            result_format="message",
            enable_thinking=self.enable_thinking,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )

        if resp.status_code != 200:
            raise ValueError(f"LLM call failed: {resp.status_code} - {resp.message}")

        return resp.output.choices[0].message.content

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        default: Any = None,
        **kwargs,
    ) -> Any:
        """生成 JSON

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            default: 解析失败时的默认值

        Returns:
            解析后的 JSON 对象
        """
        content = self.generate(system_prompt, user_prompt, **kwargs)
        return robust_json_loads(content, default)


class EmbeddingClient:
    """嵌入向量客户端

    提供统一的文本嵌入接口。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        embedding_model: Optional[str] = None,
    ):
        """初始化客户端

        Args:
            api_key: DashScope API Key
            base_url: DashScope API URL
            embedding_model: 嵌入模型名称
        """
        self.api_key = api_key or DASHSCOPE_API_KEY
        self.base_url = base_url or DASHSCOPE_BASE_URL
        self.embedding_model = embedding_model or EMBEDDING_MODEL

        # 配置 DashScope
        dashscope.base_http_api_url = self.base_url
        if not self.api_key or self.api_key in {"YOUR_API_KEY", "sk-xxx"}:
            raise ValueError("Set DASHSCOPE_API_KEY in .env or environment.")
        dashscope.api_key = self.api_key

        # 缓存
        self._cache: dict[str, list[float]] = {}

    def get_embedding(self, text: str) -> list[float]:
        """获取单个文本的嵌入向量

        Args:
            text: 输入文本

        Returns:
            嵌入向量
        """
        if text in self._cache:
            return self._cache[text]

        resp = TextEmbedding.call(model=self.embedding_model, input=text)
        output = resp.output

        if isinstance(output, dict):
            embedding = output["embeddings"][0]["embedding"]
        else:
            embedding = output.embeddings[0].embedding

        self._cache[text] = embedding
        return embedding

    def get_embeddings(self, texts: List[str]) -> dict[str, list[float]]:
        """批量获取嵌入向量

        Args:
            texts: 文本列表

        Returns:
            文本到嵌入向量的映射
        """
        if not texts:
            return {}

        # 分离已缓存和未缓存的
        cached = {}
        uncached = []
        for text in texts:
            if text in self._cache:
                cached[text] = self._cache[text]
            else:
                uncached.append(text)

        if not uncached:
            return cached

        # 批量调用
        resp = TextEmbedding.call(model=self.embedding_model, input=uncached)
        output = resp.output

        if isinstance(output, dict):
            embeddings_list = output["embeddings"]
        else:
            embeddings_list = output.embeddings

        # 映射并缓存
        for i, text in enumerate(uncached):
            if i < len(embeddings_list):
                emb = embeddings_list[i]
                embedding = emb["embedding"] if isinstance(emb, dict) else emb.embedding
                cached[text] = embedding
                self._cache[text] = embedding

        return cached

    def clear_cache(self) -> None:
        """清除缓存"""
        self._cache.clear()
