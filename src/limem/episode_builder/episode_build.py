"""Build one deterministic L2 Episode from a complete SessionInput."""

from typing import cast

from limem.raw_data_parser import RawEvent, SessionInput

from .models import EpisodeEventInput, EpisodeInput, RequiredActionInput

_CONTEXT_FIELDS = (
    "location",
    "environment",
    "vehicle",
    "occupants",
    "media",
    "screen",
    "navigation",
    "energy",
    "schedule",
    "climate",
    "windows",
    "seats",
    "dialogue",
    "body",
    "lighting",
    "wiper",
    "perception",
    "safety",
    "connectivity",
    "maintenance",
)


def _sentences(parts: list[str]) -> str:
    return "。".join(part.rstrip("。") for part in parts) + "。"


class EpisodeBuilder:
    """Organize all Raw Events in one Session into one readable Episode."""

    def build(self, session: SessionInput) -> list[EpisodeInput]:
        """Return the single Episode defined by the current short-Session contract."""

        events = [self._build_event(raw_event) for raw_event in session.raw_events]
        required_actions = [
            required_action
            for raw_event in session.raw_events
            if (required_action := self._build_required_action(raw_event)) is not None
        ]
        return [
            EpisodeInput(
                episode_id=f"{session.session_ref}:episode:0",
                events=events,
                required_actions=required_actions,
            )
        ]

    @staticmethod
    def _build_required_action(raw_event: RawEvent) -> RequiredActionInput | None:
        if raw_event.actor is None or raw_event.actor == "系统":
            return None
        if raw_event.action is None:
            return None
        if raw_event.user_ref is None:
            raise ValueError(
                f"required action {raw_event.raw_event_id!r} has no stable user_ref"
            )
        return RequiredActionInput(
            raw_event_id=raw_event.raw_event_id,
            subject=f"用户 {raw_event.user_ref}",
            actor=raw_event.actor,
            action_code=raw_event.action,
        )

    @staticmethod
    def _build_event(raw_event: RawEvent) -> EpisodeEventInput:
        return EpisodeEventInput(
            raw_event_id=raw_event.raw_event_id,
            context=EpisodeBuilder._build_context(raw_event),
            action=EpisodeBuilder._build_action(raw_event),
        )

    @staticmethod
    def _build_context(raw_event: RawEvent) -> str:
        parts: list[str] = []
        if raw_event.occurred_at is not None and raw_event.user_ref is not None:
            parts.append(
                f"{raw_event.occurred_at.isoformat()}，用户 {raw_event.user_ref}"
            )
        elif raw_event.occurred_at is not None:
            parts.append(raw_event.occurred_at.isoformat())
        elif raw_event.user_ref is not None:
            parts.append(f"用户 {raw_event.user_ref}")

        parts.append(raw_event.event)
        parts.extend(
            value
            for field in _CONTEXT_FIELDS
            if (value := getattr(raw_event, field)) is not None
        )
        return _sentences(parts)

    @staticmethod
    def _build_action(raw_event: RawEvent) -> str | None:
        actor = raw_event.actor
        if actor is None:
            return None

        detail = cast(str, raw_event.detail)
        action_payload = cast(dict[str, str], raw_event.payload)
        payload = "，".join(
            f"{key} 为 {value}" for key, value in action_payload.items()
        )
        return _sentences(
            [
                f"{actor}：{detail}",
                f"变化后状态：{payload}",
            ]
        )
