"""Shared Raw Event and SessionInput contracts for all source adapters."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    StringConstraints,
    model_serializer,
    model_validator,
)

_NonEmptyText = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
]
_NonEmptyPayload = Annotated[
    dict[_NonEmptyText, _NonEmptyText],
    Field(min_length=1),
]


class RawEvent(BaseModel):
    """One source record plus directly readable deterministic fields."""

    model_config = ConfigDict(extra="forbid", strict=True)

    raw_event_id: _NonEmptyText
    session_ref: _NonEmptyText
    session_position: int = Field(ge=0)
    user_ref: _NonEmptyText | None = None
    vehicle_ref: _NonEmptyText | None = None
    source: _NonEmptyText
    occurred_at: AwareDatetime | None = None
    record_type: _NonEmptyText
    event: _NonEmptyText
    actor: _NonEmptyText | None = None
    action: _NonEmptyText | None = None
    detail: _NonEmptyText | None = None
    payload: _NonEmptyPayload | None = None
    location: _NonEmptyText | None = None
    environment: _NonEmptyText | None = None
    vehicle: _NonEmptyText | None = None
    occupants: _NonEmptyText | None = None
    media: _NonEmptyText | None = None
    screen: _NonEmptyText | None = None
    navigation: _NonEmptyText | None = None
    energy: _NonEmptyText | None = None
    schedule: _NonEmptyText | None = None
    climate: _NonEmptyText | None = None
    windows: _NonEmptyText | None = None
    seats: _NonEmptyText | None = None
    dialogue: _NonEmptyText | None = None
    body: _NonEmptyText | None = None
    lighting: _NonEmptyText | None = None
    wiper: _NonEmptyText | None = None
    perception: _NonEmptyText | None = None
    safety: _NonEmptyText | None = None
    connectivity: _NonEmptyText | None = None
    maintenance: _NonEmptyText | None = None
    raw_payload: dict[str, JsonValue]

    @model_validator(mode="after")
    def require_complete_action_fields(self) -> RawEvent:
        action_fields = (self.actor, self.detail, self.payload)
        if all(value is None for value in action_fields):
            return self
        if any(value is None for value in action_fields):
            raise ValueError("actor, detail, and payload must be provided together")
        return self

    @model_serializer(mode="wrap")
    def omit_empty_fields(
        self,
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}


class SessionInput(BaseModel):
    """One complete ordered Session accepted by EpisodeBuilder."""

    model_config = ConfigDict(extra="forbid", strict=True)

    session_ref: _NonEmptyText
    record_count: int = Field(ge=1)
    raw_events: list[RawEvent]

    @model_validator(mode="after")
    def require_ordered_session_members(self) -> SessionInput:
        if any(event.session_ref != self.session_ref for event in self.raw_events):
            raise ValueError("raw event session_ref must match SessionInput")

        raw_event_ids = [event.raw_event_id for event in self.raw_events]
        if len(raw_event_ids) != len(set(raw_event_ids)):
            raise ValueError("raw_event_id must be unique within a SessionInput")

        positions = [event.session_position for event in self.raw_events]
        if any(current >= following for current, following in zip(positions, positions[1:])):
            raise ValueError("session_position must be strictly increasing")
        if any(position >= self.record_count for position in positions):
            raise ValueError("session_position must be less than record_count")
        return self
