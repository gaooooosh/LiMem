# -*- coding: utf-8 -*-
"""Rule-first extractor for semi-structured text."""

from __future__ import annotations

import re
from typing import Any

from ..config import SKIP_DYNAMIC_CHANGE_FILTER
from ..utils import (
    _SKIP_DYNAMIC_CHECK,
    normalize_entity_candidates,
    normalize_event_payload,
)
from .structured_mapper import StructuredFieldMapper


_DIALOGUE_WITH_COLON_PATTERN = re.compile(
    r"(?:^|[\n|]|->)\s*"
    r"(?P<speaker>\[[^\]\n]{1,32}\]|<[^>\n]{1,32}>|[A-Za-z0-9_\-\u4e00-\u9fff]{1,24}(?:说|问|答|回复|回答)?)"
    r"\s*[：:]\s*(?P<content>.+?)"
    r"(?=(?:[\n|]|->)\s*(?:\[[^\]\n]{1,32}\]|<[^>\n]{1,32}>|[A-Za-z0-9_\-\u4e00-\u9fff]{1,24}(?:说|问|答|回复|回答)?)\s*[：:]|$)",
    re.DOTALL,
)
_DIALOGUE_BRACKET_PATTERN = re.compile(
    r"(?:^|[\n|]|->)\s*\[(?P<speaker>[^\]\n]{1,32})\]\s+(?P<content>.+?)"
    r"(?=(?:[\n|]|->)\s*\[[^\]\n]{1,32}\]\s+|$)",
    re.DOTALL,
)
_DIALOGUE_ANGLE_PATTERN = re.compile(
    r"(?:^|[\n|]|->)\s*<(?P<speaker>[^>\n]{1,32})>\s+(?P<content>.+?)"
    r"(?=(?:[\n|]|->)\s*<[^>\n]{1,32}>\s+|$)",
    re.DOTALL,
)
_KV_PATTERN = re.compile(
    r"(?:^|[\n|;,])\s*(?P<key>[\w\-.:\u4e00-\u9fff]{1,40})\s*(?:[:=])\s*(?P<value>[^:=\n|;,]{1,160})",
    re.MULTILINE,
)
_DATE_OR_TIME_PATTERN = re.compile(
    r"(\b\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\b|\b\d{1,2}:\d{2}(?::\d{2})?\b|\b1\d{9,12}\b)"
)
_QUOTED_ENTITY_PATTERNS = (
    re.compile(r"《([^》]{1,80})》"),
    re.compile(r"[\"“”]([^\"“”]{1,80})[\"“”]"),
    re.compile(r"[\'‘’]([^\'‘’]{1,80})[\'‘’]"),
    re.compile(r"\b(?:[A-Z][a-z0-9]+(?:\s+[A-Z][a-z0-9]+)+)\b"),
)


class SemiStructuredExtractor:
    """Prefer structural rules, then optionally fall back to one LLM call."""

    def __init__(
        self,
        *,
        field_mapper: StructuredFieldMapper | None = None,
        fallback_extractor: Any = None,
        skip_dynamic_change_filter: bool = SKIP_DYNAMIC_CHANGE_FILTER,
    ):
        self.field_mapper = field_mapper or StructuredFieldMapper(
            skip_dynamic_change_filter=skip_dynamic_change_filter
        )
        self.fallback_extractor = fallback_extractor
        self.skip_dynamic_change_filter = bool(skip_dynamic_change_filter)

    def extract(
        self,
        text: str,
        detected_patterns: tuple[str, ...] | None = None,
    ):
        from .extractor import ExtractionResult

        del detected_patterns
        events = self._extract_dialogue_events(text)
        entities = self._extract_pattern_entities(text)

        kv_result = self._extract_kv_record(text)
        if kv_result.events_data:
            events.extend(kv_result.events_data)
            entities.extend(kv_result.entities)

        deduped_events = self._dedupe_events(events)
        deduped_entities = normalize_entity_candidates(entities, source_text=text)
        if deduped_events:
            return ExtractionResult(
                event_data=deduped_events[0],
                events_data=deduped_events,
                entities=deduped_entities,
                confidence=0.9,
            )

        if self.fallback_extractor is not None:
            return self.fallback_extractor.extract(text)

        return ExtractionResult(event_data={}, events_data=[], entities=deduped_entities, confidence=0.0)

    def _extract_dialogue_events(self, text: str) -> list[dict[str, Any]]:
        raw_events: list[dict[str, Any]] = []
        for match in _DIALOGUE_WITH_COLON_PATTERN.finditer(text):
            raw_events.append(self._build_turn_event(match.group("speaker"), match.group("content")))
        for pattern in (_DIALOGUE_BRACKET_PATTERN, _DIALOGUE_ANGLE_PATTERN):
            for match in pattern.finditer(text):
                raw_events.append(self._build_turn_event(match.group("speaker"), match.group("content")))
        return [item for item in raw_events if self._has_event(item)]

    def _build_turn_event(self, speaker: str, content: str) -> dict[str, Any]:
        normalized_speaker = self._normalize_speaker(speaker)
        normalized_content = self._normalize_content(content)
        inline_time = self._extract_inline_time(content)
        return normalize_event_payload(
            {
                "event": {
                    "actor": {"role": normalized_speaker},
                    "action": normalized_content,
                    "time": {"text": inline_time} if inline_time else {},
                }
            },
            episode_text=normalized_content,
            dynamic_hints=_SKIP_DYNAMIC_CHECK if self.skip_dynamic_change_filter else None,
            telemetry_markers=(),
            passive_screen_prefix="",
            passive_screen_markers=(),
            passive_screen_dynamic_hints=(),
        )

    def _extract_kv_record(self, text: str):
        kv_pairs: dict[str, str] = {}
        for match in _KV_PATTERN.finditer(text):
            key = str(match.group("key") or "").strip()
            value = str(match.group("value") or "").strip()
            if not key or not value or key in kv_pairs:
                continue
            kv_pairs[key] = value
        if len(kv_pairs) < 2:
            from .extractor import ExtractionResult

            return ExtractionResult(event_data={}, events_data=[], entities=[], confidence=0.0)
        return self.field_mapper.extract(kv_pairs, source_text=text)

    def _extract_pattern_entities(self, text: str) -> list[str]:
        candidates: list[str] = []
        for pattern in _QUOTED_ENTITY_PATTERNS:
            for match in pattern.findall(text):
                if isinstance(match, tuple):
                    candidates.extend(item for item in match if item)
                else:
                    candidates.append(str(match).strip())
        return candidates

    def _extract_inline_time(self, text: str) -> str:
        match = _DATE_OR_TIME_PATTERN.search(text)
        return str(match.group(1)).strip() if match else ""

    def _normalize_speaker(self, speaker: str) -> str:
        cleaned = str(speaker or "").strip().strip("[]<>")
        cleaned = re.sub(r"(说|问|答|回复|回答)$", "", cleaned).strip()
        return cleaned or "speaker"

    def _normalize_content(self, content: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(content or "")).strip()
        return cleaned.strip(" ，,;；|")

    def _has_event(self, payload: dict[str, Any]) -> bool:
        return any(str(payload.get(field, "") or "").strip() for field in ("summary", "action", "causality"))

    def _dedupe_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in events:
            signature = (
                str(item.get("summary", "") or "").strip(),
                str(item.get("action", "") or "").strip(),
                str(item.get("causality", "") or "").strip(),
            )
            if signature in seen:
                continue
            seen.add(signature)
            deduped.append(item)
        return deduped
