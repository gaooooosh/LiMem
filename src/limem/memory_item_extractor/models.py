"""Strict MemoryItem extraction contracts before persistence."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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


class Relation(BaseModel):
    """One ordered predicate and its optional relation-specific target."""

    model_config = ConfigDict(extra="forbid", strict=True)

    predicate: _NonEmptyText
    target: _NonEmptyText | None = None


class _ClaimFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    subject: _NonEmptyText
    relations: list[Relation] = Field(min_length=1)
    statement: _ClaimText
    condition: _ClaimText | None = None
    entity_mentions: list[_NonEmptyText] = Field(default_factory=list)
    raw_event_ids: list[_NonEmptyText] = Field(min_length=1)


class LLMClaimItem(_ClaimFields):
    """Claim generated from the complete Episode."""


class LLMClaimResult(BaseModel):
    """Strict response from the claim extraction call."""

    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[LLMClaimItem]


class TargetEntityResult(BaseModel):
    """Indexes of relation targets worth storing as Entity identities."""

    model_config = ConfigDict(extra="forbid", strict=True)

    entity_target_indexes: list[int]


class ExtractionItem(_ClaimFields):
    """One complete claim plus deterministic context for its Evidence."""

    context: _NonEmptyText


class L2Result(BaseModel):
    """All candidate claims extracted from one Episode."""

    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ExtractionItem]
