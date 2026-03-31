# -*- coding: utf-8 -*-
"""Plugin interface for adaptive extractor enhancements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .extractor import ExtractionResult
    from .input_classifier import ClassificationResult


class ExtractorPlugin(ABC):
    @abstractmethod
    def can_handle(
        self,
        content: str,
        metadata: dict[str, Any] | None,
        classification: "ClassificationResult",
    ) -> bool:
        pass

    @abstractmethod
    def enhance(
        self,
        content: str,
        metadata: dict[str, Any] | None,
        base_result: "ExtractionResult",
    ) -> "ExtractionResult":
        pass
