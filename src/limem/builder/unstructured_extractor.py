# -*- coding: utf-8 -*-
"""Single-pass LLM extractor for unstructured text."""

from __future__ import annotations

from typing import Any, Callable

from ..utils import normalize_event_payload, robust_json_loads


class UnstructuredExtractor:
    """Collapse unstructured text into one best-effort event extraction call."""

    def __init__(
        self,
        *,
        llm_caller: Callable[[str, str, Any], Any] | None = None,
        system_prompt: str = "",
        user_prompt: str = "{episode_text}",
    ):
        self.llm_caller = llm_caller
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt

    def extract(self, text: str):
        from .extractor import ExtractionResult

        if self.llm_caller is None or not self.system_prompt or not self.user_prompt:
            return ExtractionResult(event_data={}, events_data=[], entities=[], confidence=0.0)

        user_message = self.user_prompt.format(episode_text=text)
        try:
            payload = self.llm_caller(self.system_prompt, user_message, {})
        except Exception:
            return ExtractionResult(event_data={}, events_data=[], entities=[], confidence=0.0)

        if isinstance(payload, str):
            payload = robust_json_loads(payload, {})
        if not payload:
            return ExtractionResult(event_data={}, events_data=[], entities=[], confidence=0.0)

        events: list[dict[str, Any]] = []
        for item in self._collect_raw_event_items(payload):
            normalized = normalize_event_payload({"event": item}, episode_text=text)
            if self._has_event(normalized):
                events.append(normalized)

        deduped_events = self._dedupe_events(events)
        return ExtractionResult(
            event_data=deduped_events[0] if deduped_events else {},
            events_data=deduped_events,
            entities=[],
            confidence=0.7 if deduped_events else 0.0,
        )

    def _collect_raw_event_items(self, payload: Any) -> list[dict[str, Any]]:
        if payload is None:
            return []
        if isinstance(payload, list):
            result: list[dict[str, Any]] = []
            for item in payload:
                result.extend(self._collect_raw_event_items(item))
            return result
        if not isinstance(payload, dict):
            return []

        result: list[dict[str, Any]] = []
        raw_events = payload.get("events")
        if isinstance(raw_events, list):
            for item in raw_events:
                if isinstance(item, dict):
                    result.append(item)
        raw_event = payload.get("event")
        if isinstance(raw_event, dict):
            result.append(raw_event)
        if result:
            return result
        return [payload]

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
