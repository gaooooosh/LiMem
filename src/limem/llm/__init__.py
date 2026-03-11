# -*- coding: utf-8 -*-
"""LLM - LLM 服务层

提供 LLM 客户端和嵌入向量服务的抽象。
"""

from .dashscope_client import DashScopeClient, EmbeddingClient

__all__ = [
    "DashScopeClient",
    "EmbeddingClient",
]
