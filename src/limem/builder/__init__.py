# -*- coding: utf-8 -*-
"""Builder - 记忆构建层

提供记忆构建的核心组件：
- LLMExtractor: LLM提取器抽象
- TwoStageExtractor: 两阶段提取器实现
- Consolidator: 记忆合并器
- MemoryBuilder: 构建管道编排
"""

from .extractor import (
    LLMExtractor,
    TwoStageExtractor,
    HeuristicExtractor,
    ExtractionResult,
)
from .context_extractor import ContextExtractionPipeline
from .consolidator import Consolidator, ConsolidationResult
from .memory_builder import MemoryBuilder, BuilderConfig

__all__ = [
    # Extractor
    "LLMExtractor",
    "TwoStageExtractor",
    "HeuristicExtractor",
    "ExtractionResult",
    "ContextExtractionPipeline",
    # Consolidator
    "Consolidator",
    "ConsolidationResult",
    # Builder
    "MemoryBuilder",
    "BuilderConfig",
]
