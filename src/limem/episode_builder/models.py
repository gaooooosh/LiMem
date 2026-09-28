"""Strict L2 input models produced by EpisodeBuilder."""

from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
    model_validator,
)

_NonEmptyText = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
]


class EpisodeEventInput(BaseModel):
    """Readable Context and optional Action tied to one Raw Event."""

    model_config = ConfigDict(extra="forbid", strict=True)

    raw_event_id: _NonEmptyText
    context: _NonEmptyText
    action: _NonEmptyText | None = None

    @model_serializer(mode="wrap")
    def omit_empty_action(
        self,
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}


class RequiredActionInput(BaseModel):
    """One source-confirmed Action that L2 must preserve."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    raw_event_id: _NonEmptyText
    subject: _NonEmptyText
    actor: _NonEmptyText
    action_code: _NonEmptyText


class EpisodeInput(BaseModel):
    """One ordered Episode passed to a single L2 extraction call."""

    model_config = ConfigDict(extra="forbid", strict=True)

    episode_id: _NonEmptyText
    events: list[EpisodeEventInput]
    required_actions: list[RequiredActionInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_ordered_action_members(self) -> "EpisodeInput":
        event_positions = {
            event.raw_event_id: position for position, event in enumerate(self.events)
        }
        action_ids = [action.raw_event_id for action in self.required_actions]
        if any(raw_event_id not in event_positions for raw_event_id in action_ids):
            raise ValueError("required action raw_event_id must belong to EpisodeInput")
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("required action raw_event_id must be unique")
        positions = [event_positions[raw_event_id] for raw_event_id in action_ids]
        if positions != sorted(positions):
            raise ValueError("required actions must follow Episode order")
        return self
