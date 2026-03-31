# -*- coding: utf-8 -*-
"""Builder - 记忆构建层

提供记忆构建的核心组件：
- LLMExtractor: LLM提取器抽象
- TwoStageExtractor: 两阶段提取器实现
- Consolidator: 记忆合并器
- MemoryBuilder: 构建管道编排
"""

from .extractor import (
    AdaptiveExtractor,
    LLMExtractor,
    TwoStageExtractor,
    ExtractionResult,
)
from .context_extractor import ContextExtractionPipeline
from .consolidator import Consolidator, ConsolidationResult
from .memory_builder import MemoryBuilder, BuilderConfig
from .relationship_inferrer import RelationshipInferrer
from .structured_mapper import FieldMappingConfig, StructuredFieldMapper
from .input_classifier import InputClassifier, StructureLevel
from .semi_structured_extractor import SemiStructuredExtractor
from .unstructured_extractor import UnstructuredExtractor
from .plugin import ExtractorPlugin

__all__ = [
    # Extractor
    "AdaptiveExtractor",
    "LLMExtractor",
    "TwoStageExtractor",
    "ExtractionResult",
    "ContextExtractionPipeline",
    "InputClassifier",
    "StructureLevel",
    "StructuredFieldMapper",
    "FieldMappingConfig",
    "SemiStructuredExtractor",
    "UnstructuredExtractor",
    "RelationshipInferrer",
    "ExtractorPlugin",
    # Consolidator
    "Consolidator",
    "ConsolidationResult",
    # Builder
    "MemoryBuilder",
    "BuilderConfig",
]
