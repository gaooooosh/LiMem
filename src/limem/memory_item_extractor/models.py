"""Strict MemoryItem extraction contracts before persistence."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_serializer, model_validator

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


class SourceSubjectReference(BaseModel):
    """The primary action's actor, supplied by the source adapter and program."""

    model_config = ConfigDict(extra='forbid', strict=True)
    raw_event_id: _NonEmptyText
    source_ref: _NonEmptyText | None


class LLMClaimItem(_ClaimFields):
    """Claim generated from the complete Episode."""

    subject_binding: SourceSubjectReference | None = None

    @model_serializer(mode='wrap')
    def omit_empty_subject_binding(self, handler):
        data = handler(self)
        if self.subject_binding is None:
            data.pop('subject_binding')
        return data


class LLMClaimResult(BaseModel):
    """Strict response from the claim extraction call."""

    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[LLMClaimItem]


class TargetEntityResult(BaseModel):
    """Indexes of relation targets worth storing as Entity identities."""

    model_config = ConfigDict(extra="forbid", strict=True)

    entity_target_indexes: list[int]


class ResolvedTargetReference(BaseModel):
    """Program-resolved identity of one target occurrence, with its dependencies."""

    model_config = ConfigDict(extra="forbid", strict=True)

    relation_index: int = Field(ge=0)
    entity_id: _NonEmptyText
    raw_event_ids: list[_NonEmptyText] = Field(min_length=1)
    history_memory_ids: list[_NonEmptyText]


class ExtractionItem(_ClaimFields):
    """One complete claim plus deterministic context for its Evidence."""

    context: _NonEmptyText
    subject_binding: SourceSubjectReference | None = None
    target_bindings: list[ResolvedTargetReference] = Field(default_factory=list)
    # A null value marks an unresolved name. A list records a proven local alias.
    local_person_aliases: dict[_NonEmptyText, list[_NonEmptyText] | None] = Field(default_factory=dict)

    @model_serializer(mode='wrap')
    def omit_empty_subject_binding(self, handler):
        data = handler(self)
        if self.subject_binding is None:
            data.pop('subject_binding')
        if not self.local_person_aliases:
            data.pop("local_person_aliases")
        return data

    @model_validator(mode="after")
    def unique_bound_occurrences(self):
        indexes = [reference.relation_index for reference in self.target_bindings]
        if len(indexes) != len(set(indexes)):
            raise ValueError("target bindings must have distinct relation indexes")
        return self


class L2Result(BaseModel):
    """All candidate claims extracted from one Episode."""

    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[ExtractionItem]


def bind_required_subject(item: LLMClaimItem, action) -> LLMClaimItem:
    if action.subject.startswith('用户 '):
        return item
    return item.model_copy(update={'subject_binding': SourceSubjectReference(
        raw_event_id=action.raw_event_id, source_ref=action.actor_ref)})
