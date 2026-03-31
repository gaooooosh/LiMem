# -*- coding: utf-8 -*-
"""Structured JSON mapper for adaptive extraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

from ..config import SKIP_DYNAMIC_CHANGE_FILTER
from ..utils import (
    _SKIP_DYNAMIC_CHECK,
    normalize_entity_candidates,
    normalize_event_payload,
)


_SUMMARY_KEYS = ("summary", "description", "detail", "details", "headline", "subject", "cal_evt")
_EMPTYISH_TEXT_VALUES = frozenset({"none", "null", "nil", "n/a", "na", "unknown", "无", "空"})


@dataclass
class FieldMappingConfig:
    """Configurable field-name patterns for semantic slot mapping."""

    action_keys: tuple[str, ...] = (
        "query", "action", "command", "request", "input",
        "operation", "message", "utterance", "question", "instruction",
    )
    response_keys: tuple[str, ...] = (
        "response", "answer", "result", "output", "tts",
        "reply", "completion", "text", "content",
    )
    actor_keys: tuple[str, ...] = (
        "user", "actor", "speaker", "role", "agent",
        "from", "sender", "author", "source", "who",
    )
    time_keys: tuple[str, ...] = (
        "time", "timestamp", "date", "start", "end",
        "created_at", "start_time", "end_time", "occurred_at",
    )
    entity_keys: tuple[str, ...] = (
        "name", "title", "location", "destination", "app",
        "product", "target", "object", "item", "place", "person",
    )


class StructuredFieldMapper:
    """Extract events/entities from structured records without LLMs."""

    def __init__(
        self,
        config: FieldMappingConfig | None = None,
        *,
        skip_dynamic_change_filter: bool = SKIP_DYNAMIC_CHANGE_FILTER,
    ):
        self.config = config or FieldMappingConfig()
        self.skip_dynamic_change_filter = bool(skip_dynamic_change_filter)

    def extract(self, payload: Any, source_text: str = ""):
        from .extractor import ExtractionResult

        events: list[dict[str, Any]] = []
        entities: list[str] = []

        for record in self._collect_records(payload):
            mapped_event, mapped_entities = self._map_record(record, source_text=source_text)
            if mapped_event and self._has_event(mapped_event):
                events.append(mapped_event)
            entities.extend(mapped_entities)

        deduped_events = self._dedupe_events(events)
        deduped_entities = self._dedupe_entities(entities)
        return ExtractionResult(
            event_data=deduped_events[0] if deduped_events else {},
            events_data=deduped_events,
            entities=deduped_entities,
            confidence=1.0 if deduped_events else 0.75,
        )

    def _collect_records(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            records = [item for item in payload if isinstance(item, dict)]
            if records:
                return records
            return [{"items": payload}] if payload else []

        if not isinstance(payload, dict):
            return []

        records: list[dict[str, Any]] = [payload]
        for value in payload.values():
            if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
                records.extend(item for item in value if isinstance(item, dict))
        return records

    def _map_record(
        self,
        record: dict[str, Any],
        *,
        source_text: str,
    ) -> tuple[dict[str, Any], list[str]]:
        flattened = self._flatten(record)

        actor_value = self._select_best_value(flattened, self.config.actor_keys)
        if actor_value in (None, "", [], {}):
            actor_value = self._pick_fallback_value(flattened, ("participants", "participant"))

        action_value = self._select_best_value(flattened, self.config.action_keys)
        response_value = self._select_best_value(flattened, self.config.response_keys)
        summary_value = self._select_best_value(flattened, _SUMMARY_KEYS)
        time_value = self._build_time_value(flattened)
        entity_values = self._collect_entity_values(flattened)

        raw_event = {
            "summary": summary_value,
            "actor": actor_value,
            "participants": actor_value,
            "action": action_value,
            "outcome": response_value,
            "time": time_value,
        }

        normalized = normalize_event_payload(
            {"event": raw_event},
            episode_text=source_text,
            dynamic_hints=_SKIP_DYNAMIC_CHECK if self.skip_dynamic_change_filter else None,
            telemetry_markers=(),
            passive_screen_prefix="",
            passive_screen_markers=(),
            passive_screen_dynamic_hints=(),
        )

        entities = normalize_entity_candidates(entity_values, source_text=source_text)
        return normalized, entities

    def _flatten(
        self,
        payload: dict[str, Any],
        *,
        prefix: str = "",
        depth: int = 0,
        max_depth: int = 2,
    ) -> list[tuple[str, Any]]:
        items: list[tuple[str, Any]] = []
        for key, value in payload.items():
            full_key = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict) and depth < max_depth:
                items.extend(self._flatten(value, prefix=full_key, depth=depth + 1, max_depth=max_depth))
                continue
            if isinstance(value, list) and depth < max_depth and value and all(isinstance(item, dict) for item in value):
                for index, item in enumerate(value[:4]):
                    items.extend(
                        self._flatten(
                            item,
                            prefix=f"{full_key}.{index}",
                            depth=depth + 1,
                            max_depth=max_depth,
                        )
                    )
                continue
            items.append((full_key, value))
        return items

    def _build_time_value(self, flattened: list[tuple[str, Any]]) -> Any:
        time_payload: dict[str, Any] = {}
        for key, value in flattened:
            if self._is_emptyish(value):
                continue
            normalized_key = self._normalize_key(key)
            if not self._matches_any(normalized_key, self.config.time_keys):
                continue
            if any(token in normalized_key for token in ("end", "finish")):
                time_payload["end"] = value
            elif any(token in normalized_key for token in ("start", "begin")):
                time_payload["start"] = value
            elif any(token in normalized_key for token in ("timestamp", "occurred_at", "created_at")):
                time_payload.setdefault("timestamp", value)
            else:
                time_payload.setdefault("text", value)
        return time_payload

    def _collect_entity_values(self, flattened: list[tuple[str, Any]]) -> list[Any]:
        values: list[Any] = []
        for key, value in flattened:
            if self._is_emptyish(value):
                continue
            if self._matches_any(self._normalize_key(key), self.config.entity_keys):
                values.append(value)
        return values

    def _select_best_value(self, flattened: list[tuple[str, Any]], patterns: tuple[str, ...]) -> Any:
        best_score = -1
        best_value: Any = ""
        for key, value in flattened:
            if self._is_emptyish(value):
                continue
            score = self._match_score(self._normalize_key(key), patterns)
            if score > best_score:
                best_score = score
                best_value = value
        return best_value if best_score >= 0 else ""

    def _pick_fallback_value(self, flattened: list[tuple[str, Any]], patterns: tuple[str, ...]) -> Any:
        for key, value in flattened:
            if self._is_emptyish(value):
                continue
            normalized_key = self._normalize_key(key)
            if any(pattern in normalized_key for pattern in patterns):
                return value
        return ""

    def _matches_any(self, normalized_key: str, patterns: tuple[str, ...]) -> bool:
        return self._match_score(normalized_key, patterns) >= 0

    def _match_score(self, normalized_key: str, patterns: tuple[str, ...]) -> int:
        key_tokens = [token for token in re.split(r"[^a-z0-9]+", normalized_key) if token]
        for pattern in patterns:
            token = pattern.lower()
            if normalized_key == token:
                return 5
            if key_tokens and key_tokens[-1] == token:
                return 4
            if token in key_tokens:
                return 3
            if normalized_key.endswith(f"_{token}") or normalized_key.startswith(f"{token}_"):
                return 2
            if normalized_key.endswith(f".{token}") or normalized_key.startswith(f"{token}."):
                return 2
            if normalized_key.endswith(token) or normalized_key.startswith(token):
                return 1
        return -1

    def _normalize_key(self, key: str) -> str:
        return re.sub(r"[^a-z0-9.]+", "_", str(key).lower()).strip("_.")

    def _is_emptyish(self, value: Any) -> bool:
        if value in (None, "", [], {}):
            return True
        if isinstance(value, str):
            normalized = value.strip().lower()
            if not normalized:
                return True
            return normalized in _EMPTYISH_TEXT_VALUES
        return False

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

    def _dedupe_entities(self, entities: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for entity in entities:
            normalized = str(entity or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(normalized)
        return unique
