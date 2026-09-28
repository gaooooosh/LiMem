"""Strict inputs and outputs used by memory consolidators."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from limem.memory_store import ConsolidationSupport, MemoryItem

_NonEmptyText = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
]
_ClauseIndices = Annotated[list[int], Field(min_length=1)]


class FinalizedSupport(BaseModel):
    """Verbatim source fragments restored for one candidate support."""

    model_config = ConfigDict(extra="forbid", strict=True)

    evidence: str
    condition_fragments: list[str]
    action_fragments: list[str]


class FinalizedCandidate(BaseModel):
    """Candidate after condition synthesis and deterministic fragment recovery."""

    model_config = ConfigDict(extra="forbid", strict=True)

    actions: list[_NonEmptyText] = Field(min_length=1)
    statement: str
    condition: str | None = None
    supports: list[FinalizedSupport]


class CandidateSupportProposal(BaseModel):
    """One source and its driver-action clauses selected by discovery."""

    model_config = ConfigDict(extra="forbid", strict=True)

    evidence: _NonEmptyText
    action_clause_indices: list[int] = Field(min_length=1)


class CandidateProposal(BaseModel):
    """Action-only repeated behavior proposed by the first LLM call."""

    model_config = ConfigDict(extra="forbid", strict=True)

    actions: list[_NonEmptyText] = Field(min_length=1)
    statement: _NonEmptyText
    supports: list[CandidateSupportProposal] = Field(min_length=2)


class CandidateDiscoveryResult(BaseModel):
    """All repeated behavior candidates found across the source observations."""

    model_config = ConfigDict(extra="forbid", strict=True)

    candidates: list[CandidateProposal]


class ConditionSupportProposal(BaseModel):
    """Merged pre-action clauses for one fixed candidate and Evidence."""

    model_config = ConfigDict(extra="forbid", strict=True)

    evidence: _NonEmptyText
    condition_clause_indices: list[int] = Field(min_length=1)


class ConditionFactSupport(BaseModel):
    """An explicit Evidence label and its original observation clause indices."""

    model_config = ConfigDict(extra="forbid", strict=True)

    evidence: _NonEmptyText
    clause_indices: _ClauseIndices


class ConditionFactProposal(BaseModel):
    """One condition fact with explicitly selected source Evidence."""

    model_config = ConfigDict(extra="forbid", strict=True)

    text: _NonEmptyText
    supports: list[ConditionFactSupport] = Field(min_length=1)


class ConditionSynthesisResult(BaseModel):
    """Common condition facts induced for one fixed candidate."""

    model_config = ConfigDict(extra="forbid", strict=True)

    facts: list[ConditionFactProposal] = Field(min_length=1)

    def condition_text(self) -> str:
        return "；".join(fact.text for fact in self.facts)

    def supported_evidence(self) -> set[str]:
        """Evidence supporting every fact in the combined AND condition."""
        return set.intersection(
            *[{support.evidence for support in fact.supports} for fact in self.facts]
        )


class ConsolidationResult(BaseModel):
    """Candidates and source links written by one explicit consolidation."""

    model_config = ConfigDict(extra="forbid", strict=True)

    subject_entity_id: _NonEmptyText
    candidates: list[MemoryItem]
    supports: list[ConsolidationSupport]
