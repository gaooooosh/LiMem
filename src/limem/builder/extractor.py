"""LLM-based event extraction."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import (
    DASHSCOPE_API_KEY,
    DASHSCOPE_BASE_URL,
    ENABLE_THINKING,
    GENERATION_MODEL,
    normalize_dashscope_base_url,
)
from ..llm import DashScopeClient
from ..utils import load_prompt, normalize_event_payload


@dataclass
class ExtractionResult:
    """Canonical extraction output."""

    event_data: dict[str, Any]
    events_data: list[dict[str, Any]] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    confidence: float = 1.0

    def has_valid_event(self) -> bool:
        """检查是否有有效的事件数据"""
        if self.event_data and self.event_data.get("summary"):
            return True
        return any(isinstance(item, dict) and item.get("summary") for item in self.events_data)


class LLMExtractor(ABC):
    """Abstract extractor interface."""

    @abstractmethod
    def extract(
        self,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ExtractionResult:
        """执行提取

        Args:
            text: 原始文本（Episode内容）
            metadata: Episode元数据

        Returns:
            ExtractionResult 包含事件数据
        """
        pass


class TwoStageExtractor(LLMExtractor):
    """Segment first, then structure each event chunk."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        generation_model: Optional[str] = None,
        enable_thinking: bool = False,
        llm_concurrency: int = 1,
        llm_client: Optional[DashScopeClient] = None,
    ):
        self.generation_model = generation_model or GENERATION_MODEL
        self.enable_thinking = enable_thinking or ENABLE_THINKING
        self.llm_concurrency = max(1, int(llm_concurrency or 1))
        if llm_client is not None:
            self.llm_client = llm_client
        else:
            self.api_key = api_key or DASHSCOPE_API_KEY
            self.base_url = normalize_dashscope_base_url(base_url or DASHSCOPE_BASE_URL)
            self.llm_client = DashScopeClient(
                api_key=self.api_key,
                base_url=self.base_url,
            )
        self._event_segment_system_prompt = load_prompt("extract_event_segments_system.txt")
        self._event_segment_user_prompt = load_prompt("extract_event_segments_user.txt")
        self._event_struct_system_prompt = load_prompt("extract_event_struct_system.txt")
        self._event_struct_user_prompt = load_prompt("extract_event_struct_user.txt")
        self._event_struct_batch_user_prompt = load_prompt("extract_event_struct_batch_user.txt")
        self._event_system_prompt = load_prompt("extract_event_only_system.txt")
        self._event_user_prompt = load_prompt("extract_event_only_user.txt")
        self._entity_system_prompt = load_prompt("extract_entities_only_system.txt")
        self._entity_user_prompt = load_prompt("extract_entities_only_user.txt")

    def extract(
        self,
        text: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ExtractionResult:
        del metadata
        events_data = self._extract_events(text)
        event_data = events_data[0] if events_data else {}

        return ExtractionResult(
            event_data=event_data,
            events_data=events_data,
            entities=[],
        )

    _TWO_STAGE_TEXT_THRESHOLD = 4000

    def _extract_events(self, text: str) -> list[dict[str, Any]]:
        if len(text) <= self._TWO_STAGE_TEXT_THRESHOLD:
            return self._extract_events_single_pass(text)

        try:
            segments = self._extract_event_segments(text)
        except Exception as exc:
            print(f"⚠️ Segment stage failed, fallback to single-pass event extraction: {exc}")
            segments = []

        if not segments:
            return self._extract_events_single_pass(text)

        if len(segments) > 1:
            try:
                return self._extract_events_batched(text=text, segments=segments)
            except Exception as exc:
                print(f"⚠️ Batched segment structuring failed, fallback to per-segment extraction: {exc}")

        normalized: list[dict[str, Any]] = []
        for idx, segment in enumerate(segments):
            raw_payload = self._extract_event_from_segment(
                episode_text=text,
                segment_text=segment,
                segment_index=idx,
                segment_total=len(segments),
            )
            raw_events = self._collect_raw_event_items(raw_payload)
            if not raw_events and isinstance(raw_payload, dict):
                raw_events = [raw_payload]
            for item in raw_events:
                if not isinstance(item, dict):
                    continue
                normalized_item = normalize_event_payload({"event": item}, episode_text=text)
                if self._has_event_semantics(normalized_item):
                    normalized.append(normalized_item)

        return self._dedupe_events(normalized)

    def _extract_events_batched(
        self,
        text: str,
        segments: list[str],
    ) -> list[dict[str, Any]]:
        user_template = getattr(self, "_event_struct_batch_user_prompt", "") or ""
        if not user_template:
            raise ValueError("batched segment prompt not configured")

        numbered_segments = "\n\n".join(
            f"[segment_index={idx}]\n{segment}"
            for idx, segment in enumerate(segments)
        )
        user_msg = user_template.format(
            episode_text=text,
            segment_total=len(segments),
            segments_text=numbered_segments,
        )
        data = self._call_generation_json(
            system_prompt=self._event_struct_system_prompt or self._event_system_prompt,
            user_message=user_msg,
            default={},
        )
        raw_items = self._collect_batched_segment_items(data)
        if len(raw_items) != len(segments):
            raise ValueError(
                f"expected {len(segments)} batched segment results, got {len(raw_items)}"
            )

        normalized_by_index: dict[int, dict[str, Any]] = {}
        for item in raw_items:
            segment_index = self._parse_segment_index(item, segment_total=len(segments))
            if segment_index in normalized_by_index:
                raise ValueError(f"duplicate segment_index in batched response: {segment_index}")
            normalized_item = self._normalize_segment_event_item(item=item, episode_text=text)
            normalized_by_index[segment_index] = normalized_item

        if len(normalized_by_index) != len(segments):
            raise ValueError(
                f"batched response missing segment indices: expected {len(segments)}, got {len(normalized_by_index)}"
            )

        normalized: list[dict[str, Any]] = []
        for idx in range(len(segments)):
            normalized_item = normalized_by_index[idx]
            if self._has_event_semantics(normalized_item):
                normalized.append(normalized_item)
        return self._dedupe_events(normalized)

    def _extract_events_single_pass(self, text: str) -> list[dict[str, Any]]:
        user_msg = self._event_user_prompt.format(episode_text=text)
        data = self._call_generation_json(
            system_prompt=self._event_system_prompt,
            user_message=user_msg,
            default={},
        )
        if not data:
            return []

        normalized: list[dict[str, Any]] = []
        raw_events = self._collect_raw_event_items(data)
        if not raw_events and isinstance(data, dict):
            raw_events = [data]

        for item in raw_events:
            if not isinstance(item, dict):
                continue
            normalized_item = normalize_event_payload({"event": item}, episode_text=text)
            if self._has_event_semantics(normalized_item):
                normalized.append(normalized_item)
        return self._dedupe_events(normalized)

    def _has_event_semantics(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        return any(str(payload.get(field, "") or "").strip() for field in ("summary", "action", "causality"))

    def _dedupe_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in events:
            parts = (
                str(item.get("summary", "") or "").strip(),
                str(item.get("action", "") or "").strip(),
                str(item.get("causality", "") or "").strip(),
            )
            signature = parts if any(parts) else None
            if signature is not None and signature in seen:
                continue
            if signature is not None:
                seen.add(signature)
            deduped.append(item)
        return deduped

    def _extract_event_segments(self, text: str) -> list[str]:
        system_prompt = self._event_segment_system_prompt or self._event_system_prompt
        user_template = self._event_segment_user_prompt or self._event_user_prompt
        user_msg = user_template.format(episode_text=text)
        data = self._call_generation_json(
            system_prompt=system_prompt,
            user_message=user_msg,
            default={},
        )

        if isinstance(data, dict) and any(key in data for key in ("events", "event")):
            return [text]

        raw_segments: list[Any] = []
        if isinstance(data, list):
            raw_segments = data
        elif isinstance(data, dict):
            value = data.get("segments")
            if isinstance(value, list):
                raw_segments = value

        segments: list[str] = []
        seen: set[str] = set()
        for item in raw_segments:
            if isinstance(item, dict):
                span = str(
                    item.get("span_text", "")
                    or item.get("segment_text", "")
                    or item.get("text", "")
                ).strip()
            else:
                span = str(item or "").strip()
            if not span:
                continue
            key = span.lower()
            if key in seen:
                continue
            seen.add(key)
            segments.append(span)
        return segments

    def _extract_event_from_segment(
        self,
        episode_text: str,
        segment_text: str,
        segment_index: int,
        segment_total: int,
    ) -> Any:
        system_prompt = self._event_struct_system_prompt or self._event_system_prompt
        user_template = self._event_struct_user_prompt or self._event_user_prompt
        user_msg = user_template.format(
            episode_text=episode_text,
            segment_text=segment_text,
            segment_index=segment_index,
            segment_total=segment_total,
        )
        return self._call_generation_json(
            system_prompt=system_prompt,
            user_message=user_msg,
            default={},
        )

    def _collect_batched_segment_items(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("events", "segments", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []

    def _parse_segment_index(self, payload: dict[str, Any], segment_total: int) -> int:
        raw_value = payload.get("segment_index", payload.get("index"))
        try:
            segment_index = int(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid segment_index: {raw_value!r}") from exc
        if segment_index < 0 or segment_index >= segment_total:
            raise ValueError(f"segment_index out of range: {segment_index}")
        return segment_index

    def _normalize_segment_event_item(
        self,
        item: dict[str, Any],
        episode_text: str,
    ) -> dict[str, Any]:
        raw_event = item.get("event")
        if not isinstance(raw_event, dict):
            raw_events = self._collect_raw_event_items(item)
            raw_event = raw_events[0] if raw_events else {}
        if not isinstance(raw_event, dict):
            raw_event = {}
        return normalize_event_payload({"event": raw_event}, episode_text=episode_text)

    def _call_generation_json(
        self,
        system_prompt: str,
        user_message: str,
        default: Any,
    ) -> Any:
        return self.llm_client.call_generation_json(
            system_prompt=system_prompt,
            user_message=user_message,
            default=default,
            model=self.generation_model,
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

    def _extract_entities(self, text: str) -> list[str]:
        del text
        return []
