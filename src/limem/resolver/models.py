"""Strict query resolution input and output records."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

_NonEmptyText = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
]


class QueryResolutionInput(BaseModel):
    """Natural-language query and current context supplied to QueryResolver."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: _NonEmptyText | None = None
    context: _NonEmptyText | None = None

    @model_validator(mode="after")
    def require_resolution_text(self) -> QueryResolutionInput:
        if self.query is None and self.context is None:
            raise ValueError("query and context cannot both be null")
        return self


class ResolvedEntityMention(BaseModel):
    """One matched surface and every existing Entity carrying that surface."""

    model_config = ConfigDict(extra="forbid", strict=True)

    text: _NonEmptyText
    source: Literal["query", "context"]
    entity_ids: list[_NonEmptyText] = Field(min_length=1)


class ResolvedQuery(BaseModel):
    """Original request text plus deterministically linked Entity mentions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: _NonEmptyText | None
    context: _NonEmptyText | None
    entity_mentions: list[ResolvedEntityMention]
