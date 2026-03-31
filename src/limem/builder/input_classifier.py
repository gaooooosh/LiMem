# -*- coding: utf-8 -*-
"""Input classifier for adaptive extraction routing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


_DIALOGUE_COLON_PATTERN = re.compile(
    r"(?:^|[\n\r|]|->)\s*"
    r"(?:\[[^\]\n]{1,32}\]|<[^>\n]{1,32}>|[A-Za-z0-9_\-\u4e00-\u9fff]{1,24}(?:说|问|答|回复|回答)?)"
    r"\s*[：:]\s*\S",
    re.MULTILINE,
)
_DIALOGUE_BRACKET_PATTERN = re.compile(
    r"(?:^|[\n\r|]|->)\s*(?:\[[^\]\n]{1,32}\]|<[^>\n]{1,32}>)\s+\S",
    re.MULTILINE,
)
_DATE_PATTERN = re.compile(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b")
_CLOCK_PATTERN = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_UNIX_TS_PATTERN = re.compile(r"\b1\d{9,12}\b")
_KV_PATTERN = re.compile(
    r"(?:^|[\n\r|;,])\s*[\w\-.:\u4e00-\u9fff]{1,40}\s*(?:[:=])\s*[^:=\n\r|;,]{1,160}",
    re.MULTILINE,
)


class StructureLevel(Enum):
    STRUCTURED = 1
    SEMI_STRUCTURED = 2
    UNSTRUCTURED = 3


@dataclass
class ClassificationResult:
    level: StructureLevel
    parsed_json: Any = None
    detected_patterns: tuple[str, ...] = field(default_factory=tuple)
    score: int = 0


class InputClassifier:
    """Pure-rule input classifier based on structural features."""

    def classify(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> ClassificationResult:
        content = str(text or "").strip()
        parsed_json = self._try_parse_json(content)
        if parsed_json is not None:
            return ClassificationResult(
                level=StructureLevel.STRUCTURED,
                parsed_json=parsed_json,
                detected_patterns=("json",),
                score=3,
            )

        score = 0
        detected_patterns: list[str] = []

        dialogue_hits = self._count_dialogue_hits(content)
        if dialogue_hits:
            score += 2 if dialogue_hits >= 2 else 1
            detected_patterns.append("dialogue")

        timestamp_hits = self._count_timestamp_hits(content, metadata)
        if timestamp_hits:
            score += 1
            detected_patterns.append("timestamp")

        kv_hits = self._count_kv_hits(content, metadata)
        if kv_hits:
            score += 2 if kv_hits >= 2 else 1
            detected_patterns.append("kv")

        if score >= 2:
            return ClassificationResult(
                level=StructureLevel.SEMI_STRUCTURED,
                detected_patterns=tuple(detected_patterns),
                score=score,
            )

        return ClassificationResult(
            level=StructureLevel.UNSTRUCTURED,
            detected_patterns=tuple(detected_patterns),
            score=score,
        )

    def _try_parse_json(self, content: str) -> Any:
        if not content:
            return None
        stripped = content.lstrip()
        if not stripped.startswith(("{", "[")):
            return None
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None

    def _count_dialogue_hits(self, content: str) -> int:
        hits = len(_DIALOGUE_COLON_PATTERN.findall(content))
        hits += len(_DIALOGUE_BRACKET_PATTERN.findall(content))
        return hits

    def _count_timestamp_hits(self, content: str, metadata: dict[str, Any] | None) -> int:
        hits = 0
        if _DATE_PATTERN.search(content):
            hits += 1
        if _CLOCK_PATTERN.search(content):
            hits += 1
        if _UNIX_TS_PATTERN.search(content):
            hits += 1
        if isinstance(metadata, dict):
            for key in ("timestamp", "time", "created_at", "start_time", "end_time"):
                value = metadata.get(key)
                if value and (_DATE_PATTERN.search(str(value)) or _UNIX_TS_PATTERN.search(str(value))):
                    hits += 1
                    break
        return hits

    def _count_kv_hits(self, content: str, metadata: dict[str, Any] | None) -> int:
        hits = len(_KV_PATTERN.findall(content))
        if isinstance(metadata, dict):
            scalar_items = sum(
                1
                for value in metadata.values()
                if isinstance(value, (str, int, float, bool)) and str(value).strip()
            )
            if scalar_items >= 2:
                hits += 1
        return hits
