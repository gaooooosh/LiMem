"""Strict request and diagnostic result models for memory retrieval."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from limem.memory_store import MemoryItem

_NonEmptyText = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
]

ConnectionField = Literal["subject", "relation_target", "entity_ref"]


class RetrievalRequest(BaseModel):
    """Natural-language request and experiment-time retrieval controls."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: _NonEmptyText | None = None
    context: _NonEmptyText | None = None
    limit: int = Field(gt=0)
    seed_limit: int = Field(gt=0)
    neighbor_limit: int = Field(gt=0)
    rrf_k: int = Field(ge=0)
    bm25_weight: float = Field(gt=0)
    entity_match_weight: float = Field(ge=0)
    evidence_support_weight: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def require_retrieval_text(self) -> RetrievalRequest:
        if self.query is None and self.context is None:
            raise ValueError("query and context cannot both be null")
        return self


class EntityRetrievalVia(BaseModel):
    """The Entity connection selected between two adjacent steps."""

    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["entity"] = "entity"
    from_memory_item_id: _NonEmptyText
    entity_id: _NonEmptyText
    previous_field: ConnectionField
    previous_position: int | None
    current_field: ConnectionField
    current_position: int | None

    @model_validator(mode="after")
    def require_field_position_pair(self) -> EntityRetrievalVia:
        for side, field, position in (
            ("previous", self.previous_field, self.previous_position),
            ("current", self.current_field, self.current_position),
        ):
            if field == "subject" and position is not None:
                raise ValueError(f"{side} subject position must be null")
            if field != "subject" and (position is None or position < 0):
                raise ValueError(f"{side} {field} position must be a nonnegative index")
        return self


class ConsolidationSupportRetrievalVia(BaseModel):
    """The source MemoryItems that support one consolidated candidate."""

    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["consolidation_support"] = "consolidation_support"
    source_memory_item_ids: list[_NonEmptyText] = Field(min_length=1)


RetrievalVia = Annotated[
    EntityRetrievalVia | ConsolidationSupportRetrievalVia,
    Field(discriminator="type"),
]


class RetrievalStep(BaseModel):
    """One ranked MemoryItem and its role in a diagnostic retrieval path."""

    model_config = ConfigDict(extra="forbid", strict=True)

    memory_item: MemoryItem
    stage: Literal["seed", "expansion_1", "expansion_2", "support"]
    seed_entity_id: _NonEmptyText | None = None
    via: RetrievalVia | None = None
    bm25_score: float
    bm25_rank: int = Field(gt=0)
    embedding_score: float
    embedding_rank: int = Field(gt=0)
    entity_match_score: int = Field(ge=0)
    evidence_support_score: int = Field(ge=0)
    rrf_score: float
    rrf_rank: int = Field(gt=0)

    @model_validator(mode="after")
    def require_stage_origin(self) -> RetrievalStep:
        if self.stage == "seed" and self.via is not None:
            raise ValueError("seed step cannot have via")
        if self.stage == "support" and not isinstance(
            self.via, ConsolidationSupportRetrievalVia
        ):
            raise ValueError("support step requires consolidation support via")
        if self.stage.startswith("expansion_") and not isinstance(
            self.via, EntityRetrievalVia
        ):
            raise ValueError("expansion step requires Entity via")
        if self.stage != "seed" and self.seed_entity_id is not None:
            raise ValueError("only seed step can have seed_entity_id")
        return self


class RetrievalResult(BaseModel):
    """One complete candidate path returned to the downstream caller."""

    model_config = ConfigDict(extra="forbid", strict=True)

    steps: list[RetrievalStep] = Field(min_length=1)
