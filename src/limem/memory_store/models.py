"""Strict records returned by the SQLite memory store."""

import json

from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    JsonValue,
    StringConstraints,
    field_validator,
)

_NonEmptyText = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
]
_ClaimText = Annotated[
    str,
    StringConstraints(
        min_length=1,
        pattern=r"^[^\r\n]*[^\s\r\n][^\r\n]*$",
    ),
]


class Entity(BaseModel):
    """One persisted identity or Episode-scoped placeholder."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: _NonEmptyText
    name: _NonEmptyText
    external_ref: _NonEmptyText | None
    aliases: list[_NonEmptyText]
    types: list[_NonEmptyText]
    canonical_entity_id: _NonEmptyText | None
    scope_text: _NonEmptyText | None


class EntityIdentityConfirmation(BaseModel):
    """Existing unresolved Entities proven identical by a consolidator.

    The first ID becomes canonical; every member points directly to it.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    entity_ids: list[_NonEmptyText] = Field(min_length=2)


class Profile(BaseModel):
    """Caller-maintained text addressed by one stable ID."""

    model_config = ConfigDict(extra="forbid", strict=True)

    entity_id: _NonEmptyText
    text: str


class SourceSession(BaseModel):
    """One complete source JSON Session stored under its parsed Session ID."""

    model_config = ConfigDict(extra="forbid", strict=True)

    session_id: _NonEmptyText
    source_format: _NonEmptyText
    raw_session_json: dict[str, JsonValue] = Field(min_length=1)
    created_at: AwareDatetime

    @field_validator("raw_session_json")
    @classmethod
    def require_standard_json(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        json.dumps(value, allow_nan=False)
        return value


class MemoryRelation(BaseModel):
    """One ordered relation with text and an optional Entity target."""

    model_config = ConfigDict(extra="forbid", strict=True)

    predicate: _NonEmptyText
    target: _NonEmptyText | None
    target_entity_id: _NonEmptyText | None


class EntityRef(BaseModel):
    """A named entity mentioned outside the complete subject and targets."""

    model_config = ConfigDict(extra="forbid", strict=True)

    text: _NonEmptyText
    entity_id: _NonEmptyText


class MemoryItem(BaseModel):
    """One complete persisted claim."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: _NonEmptyText
    subject_entity_id: _NonEmptyText
    relations: list[MemoryRelation] = Field(min_length=1)
    statement: _ClaimText
    condition: _ClaimText | None
    entity_refs: list[EntityRef]
    status: Literal["active", "candidate", "inactive"]


class Evidence(BaseModel):
    """One independent source supporting a MemoryItem."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: _NonEmptyText
    memory_item_id: _NonEmptyText
    source_session_id: _NonEmptyText
    raw_event_ids: list[_NonEmptyText] = Field(min_length=1)
    context: _NonEmptyText


class ConsolidationSupport(BaseModel):
    """Source fragments selected to support one consolidated candidate."""

    model_config = ConfigDict(extra="forbid", strict=True)

    consolidator_type: _NonEmptyText
    candidate_memory_item_id: _NonEmptyText
    source_evidence_id: _NonEmptyText
    fragments: list[_NonEmptyText] = Field(min_length=1)


class PendingConsolidationSubject(BaseModel):
    """One canonical subject with Evidence pending for a consolidator type."""

    model_config = ConfigDict(extra="forbid", strict=True)

    subject_entity_id: _NonEmptyText
    evidence_count: int = Field(gt=0)


class MemoryEmbedding(BaseModel):
    """The combined statement and condition embedding for a MemoryItem."""

    model_config = ConfigDict(extra="forbid", strict=True)

    memory_item_id: _NonEmptyText
    model: _NonEmptyText
    dimensions: int = Field(gt=0)
    vector: list[FiniteFloat] = Field(min_length=1)


class LexicalSearchHit(BaseModel):
    """One FTS5 hit with SQLite's unmodified BM25 score."""

    model_config = ConfigDict(extra="forbid", strict=True)

    memory_item_id: _NonEmptyText
    bm25_score: float


class WriteItem(BaseModel):
    """Database IDs produced for one ExtractionItem."""

    model_config = ConfigDict(extra="forbid", strict=True)

    subject_entity_id: _NonEmptyText
    memory_item_id: _NonEmptyText
    evidence_id: _NonEmptyText


class WriteResult(BaseModel):
    """IDs produced by one atomic L2Result write."""

    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[WriteItem]
