# -*- coding: utf-8 -*-
"""Rule-first extractor for semi-structured text."""

from __future__ import annotations

import re
from typing import Any

from ..config import SKIP_DYNAMIC_CHANGE_FILTER
from ..utils import _SKIP_DYNAMIC_CHECK, normalize_event_payload
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
_MEDIA_PLAY_PATTERN = re.compile(
    r"在(?P<app>\S{1,20})播放(?:(?P<descriptor>[^《「\",，。\n]{0,24})[《「\"](?P<quoted_title>[^》」\"\n]{1,60})[》」\"]|(?P<title>[^,，。\n]{1,60}))"
)
_NAVIGATION_PATTERN = re.compile(
    r"(?:从(?P<origin>[^,，。\n]{1,20}))?导航到(?P<dest>[^,，。\n]{1,40})"
)
_GENERIC_NARRATIVE_PATTERN = re.compile(
    r"(?P<actor>用户|车主|司机|乘客|主驾|副驾|后排乘客|系统|车机|助手|User|user|Assistant|assistant|System|system|Driver|driver|Passenger|passenger|[A-Z][a-z]{1,12})"
    r"(?P<verb>发起|开始|打开|关闭|设置|搜索|选择|切换|调整|启动|停止|暂停)"
    r"(?P<object>[^,，。！？!? \n][^,，。！？!\?\n]{0,39})"
)
_ACTION_GATE_HINTS = (
    "说", "问", "播放", "导航", "打开", "关闭", "设置", "搜索",
    "选择", "发起", "开始", "停止", "暂停", "切换", "提醒", "预约",
    "参加", "前往", "创建", "更新", "said", "asked", "play", "played",
    "navigate", "navigated", "open", "opened", "close", "closed", "create",
    "created", "start", "started", "stop", "stopped", "pause", "paused",
    "appointment", "meeting",
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

        events = self._extract_dialogue_events(text)

        kv_result = self._extract_kv_record(text)
        if kv_result.events_data:
            events.extend(kv_result.events_data)

        narrative_events, _ = self._extract_narrative_events(text)
        if narrative_events:
            events.extend(narrative_events)

        deduped_events = self._dedupe_events(events)
        if deduped_events:
            return ExtractionResult(
                event_data=deduped_events[0],
                events_data=deduped_events,
                entities=[],
                confidence=0.9,
            )

        if detected_patterns and not self._has_actionable_content(text):
            return ExtractionResult(
                event_data={},
                events_data=[],
                entities=[],
                confidence=0.0,
            )

        if self.fallback_extractor is not None:
            return self.fallback_extractor.extract(text)

        return ExtractionResult(event_data={}, events_data=[], entities=[], confidence=0.0)

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

    def _extract_narrative_events(self, text: str) -> tuple[list[dict[str, Any]], list[str]]:
        events: list[dict[str, Any]] = []
        entities: list[str] = []

        for match in _MEDIA_PLAY_PATTERN.finditer(text):
            event, event_entities = self._build_media_event(text, match)
            if self._has_event(event):
                events.append(event)
                entities.extend(event_entities)

        for match in _NAVIGATION_PATTERN.finditer(text):
            event, event_entities = self._build_navigation_event(text, match)
            if self._has_event(event):
                events.append(event)
                entities.extend(event_entities)

        if events:
            return events, entities

        for match in _GENERIC_NARRATIVE_PATTERN.finditer(text):
            event, event_entities = self._build_generic_narrative_event(text, match)
            if self._has_event(event):
                events.append(event)
                entities.extend(event_entities)
        return events, entities

    def _build_media_event(
        self,
        text: str,
        match: re.Match[str],
    ) -> tuple[dict[str, Any], list[str]]:
        app = self._clean_narrative_value(match.group("app"))
        descriptor = self._clean_narrative_value(match.group("descriptor"))
        quoted_title = self._clean_narrative_value(match.group("quoted_title"))
        plain_title = self._clean_narrative_value(match.group("title"))
        title = quoted_title or plain_title
        quoted = bool(quoted_title)

        action = "播放"
        if descriptor and title:
            title_text = f"《{title}》" if quoted else title
            action = f"播放{descriptor}{title_text}"
        elif title:
            title_text = f"《{title}》" if quoted else title
            action = f"播放{title_text}"

        return self._build_narrative_event(
            text=text,
            summary=match.group(0),
            action=action,
            entities=[app, title],
        )

    def _build_navigation_event(
        self,
        text: str,
        match: re.Match[str],
    ) -> tuple[dict[str, Any], list[str]]:
        origin = self._clean_narrative_value(match.group("origin"))
        dest = self._clean_narrative_value(match.group("dest"))
        action = f"导航到{dest}" if dest else "导航"
        if origin and dest:
            action = f"从{origin}导航到{dest}"

        return self._build_narrative_event(
            text=text,
            summary=match.group(0),
            action=action,
            entities=[origin, dest],
        )

    def _build_generic_narrative_event(
        self,
        text: str,
        match: re.Match[str],
    ) -> tuple[dict[str, Any], list[str]]:
        actor = self._clean_narrative_value(match.group("actor"))
        verb = self._clean_narrative_value(match.group("verb"))
        obj = self._clean_narrative_value(match.group("object"))
        action = f"{verb}{obj}" if obj else verb

        return self._build_narrative_event(
            text=text,
            summary=f"{actor}{action}",
            action=action,
            actor=actor,
            entities=[obj],
        )

    def _build_narrative_event(
        self,
        *,
        text: str,
        summary: str,
        action: str,
        actor: str = "",
        entities: list[str] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        inline_time = self._extract_inline_time(text)
        payload = normalize_event_payload(
            {
                "event": {
                    "summary": self._normalize_content(summary),
                    "participants": [{"role": actor}] if actor else [],
                    "action": self._normalize_content(action),
                    "time": {"text": inline_time} if inline_time else {},
                }
            },
            episode_text=text,
            dynamic_hints=_SKIP_DYNAMIC_CHECK if self.skip_dynamic_change_filter else None,
            telemetry_markers=(),
            passive_screen_prefix="",
            passive_screen_markers=(),
            passive_screen_dynamic_hints=(),
        )
        return payload, [item for item in (entities or []) if item]

    def _clean_narrative_value(self, value: Any) -> str:
        cleaned = self._normalize_content(str(value or ""))
        return cleaned.strip("《》「」\"'“”‘’")

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

    def _has_actionable_content(self, text: str) -> bool:
        lowered = str(text or "").lower()
        return any(hint in text or hint in lowered for hint in _ACTION_GATE_HINTS)

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
