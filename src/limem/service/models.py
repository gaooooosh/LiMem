"""Strict HTTP records for API keys and memory-bank metadata."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
    JsonValue,
)

_NonEmptyText = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
]
_DisplayName = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=80,
        pattern=r"^[^\r\n]*\S[^\r\n]*$",
    ),
]

BankType = Literal["temporary", "persistent"]
SourceFormat = Literal["vehicle_session_v1"]
ConsolidatorType = Literal["repeated_behavior"]


class ErrorBody(BaseModel):
    """Stable fields returned for one failed HTTP request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: _NonEmptyText
    message: _NonEmptyText
    guidance: _NonEmptyText
    request_id: _NonEmptyText
    details: list[dict[str, JsonValue]]
    docs_url: str | None


class ErrorResponse(BaseModel):
    """Public wrapper for every service error."""

    model_config = ConfigDict(extra="forbid", strict=True)

    error: ErrorBody


class CreateKeyRequest(BaseModel):
    """Administrator request to issue one API key."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: _DisplayName


class KeyRecord(BaseModel):
    """Public metadata for one issued API key."""

    model_config = ConfigDict(extra="forbid", strict=True)

    key_id: _NonEmptyText
    name: _DisplayName
    status: Literal["active", "revoked"]
    created_at: AwareDatetime
    revoked_at: AwareDatetime | None

    @model_validator(mode="after")
    def require_status_timestamp(self) -> "KeyRecord":
        if self.status == "active" and self.revoked_at is not None:
            raise ValueError("active key cannot have revoked_at")
        if self.status == "revoked" and self.revoked_at is None:
            raise ValueError("revoked key requires revoked_at")
        return self


class IssuedKey(KeyRecord):
    """New key metadata plus the API key returned only when issued."""

    api_key: _NonEmptyText


class CreateBankRequest(BaseModel):
    """Owner request for one memory bank."""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: _DisplayName
    type: BankType = "temporary"
    source_format: SourceFormat


class BankRecord(BaseModel):
    """Ownership and lifecycle metadata for one memory bank."""

    model_config = ConfigDict(extra="forbid", strict=True)

    memory_bank_id: _NonEmptyText
    owner_key_id: _NonEmptyText
    name: _DisplayName
    type: BankType
    source_format: SourceFormat
    created_at: AwareDatetime
    expires_at: AwareDatetime | None
    deletes_at: AwareDatetime | None

    @model_validator(mode="after")
    def require_ordered_lifecycle(self) -> "BankRecord":
        if self.type == "temporary" and (
            self.expires_at is None or self.deletes_at is None
        ):
            raise ValueError("temporary bank requires expiry and deletion timestamps")
        if self.type == "persistent" and (
            self.expires_at is not None or self.deletes_at is not None
        ):
            raise ValueError("persistent bank cannot expire")
        timestamps: list[datetime] = [self.created_at]
        if self.expires_at is not None:
            timestamps.append(self.expires_at)
        if self.deletes_at is not None:
            timestamps.append(self.deletes_at)
        if timestamps != sorted(timestamps):
            raise ValueError("bank lifecycle timestamps must be ordered")
        return self


class BankView(BaseModel):
    """Public memory-bank metadata without its owner identity."""

    model_config = ConfigDict(extra="forbid", strict=True)

    memory_bank_id: _NonEmptyText
    name: _DisplayName
    type: BankType
    source_format: SourceFormat
    created_at: AwareDatetime
    expires_at: AwareDatetime | None
    deletes_at: AwareDatetime | None

    @classmethod
    def from_record(cls, record: BankRecord) -> "BankView":
        return cls.model_validate(record.model_dump(exclude={"owner_key_id"}))


class KeyListResponse(BaseModel):
    """Administrator view of every issued key identity."""

    model_config = ConfigDict(extra="forbid", strict=True)

    api_keys: list[KeyRecord]


class BankListResponse(BaseModel):
    """All active memory banks owned by the caller key."""

    model_config = ConfigDict(extra="forbid", strict=True)

    memory_banks: list[BankView]


class EntityView(BaseModel):
    """Public identity fields for one Entity."""

    model_config = ConfigDict(extra="forbid", strict=True)

    entity_id: _NonEmptyText
    name: _NonEmptyText
    aliases: list[_NonEmptyText]
    types: list[_NonEmptyText]
    has_profile: bool


class EntityListResponse(BaseModel):
    """All Entities currently stored in one memory bank."""

    model_config = ConfigDict(extra="forbid", strict=True)

    entities: list[EntityView]


class SessionValidationResponse(BaseModel):
    """Result of validating one source Session without storing it."""

    model_config = ConfigDict(extra="forbid", strict=True)

    valid: Literal[True] = True
    source_format: SourceFormat = "vehicle_session_v1"
    session_id: _NonEmptyText
    record_count: int = Field(ge=1)


class WriteSessionResponse(BaseModel):
    """Result of one complete atomic Session write."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["written"] = "written"
    memory_bank_id: _NonEmptyText
    session_id: _NonEmptyText
    memory_items_created: int = Field(ge=0)


class PutProfileRequest(BaseModel):
    """Complete replacement text for one Entity Profile."""

    model_config = ConfigDict(extra="forbid", strict=True)

    text: str


class ProfileResponse(BaseModel):
    """Current caller-maintained Profile for one Entity."""

    model_config = ConfigDict(extra="forbid", strict=True)

    entity_id: _NonEmptyText
    text: str


class RetrieveRequest(BaseModel):
    """Natural-language retrieval input exposed to Agent callers."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: _NonEmptyText | None
    context: _NonEmptyText | None
    limit: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def require_retrieval_text(self) -> "RetrieveRequest":
        if self.query is None and self.context is None:
            raise ValueError("query and context cannot both be null")
        return self


class RetrieveResponse(BaseModel):
    """Compact Agent-facing memory response."""

    model_config = ConfigDict(extra="forbid", strict=True)

    memory_bank_id: _NonEmptyText
    content: _NonEmptyText


class ManualConsolidationResponse(BaseModel):
    """Result of synchronously consolidating one canonical subject."""

    model_config = ConfigDict(extra="forbid", strict=True)

    memory_bank_id: _NonEmptyText
    subject_entity_id: _NonEmptyText
    consolidator_type: ConsolidatorType
    pending_evidence_count: int = Field(ge=0)
    outcome: Literal["consolidated", "no_consolidation"]
    candidate_memory_item_ids: list[_NonEmptyText]


class ManualConsolidationRequest(BaseModel):
    """Explicit request to consolidate one canonical subject."""

    model_config = ConfigDict(extra="forbid", strict=True)

    subject_entity_id: _NonEmptyText
    consolidator_type: ConsolidatorType


class StartConsolidationRunRequest(BaseModel):
    """Explicit request to start one background consolidation run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    consolidator_type: ConsolidatorType
    max_entities: int = Field(gt=0)


class ConsolidationRunResult(BaseModel):
    """Completed result for one subject attempted by an automatic run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    subject_entity_id: _NonEmptyText
    pending_evidence_count: int = Field(ge=1)
    outcome: Literal["consolidated", "no_consolidation"]
    candidate_memory_item_ids: list[_NonEmptyText]


class ConsolidationRunFailure(BaseModel):
    """Failure that stopped an automatic run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    subject_entity_id: _NonEmptyText
    stage: _NonEmptyText
    message: _NonEmptyText


class ConsolidationRunResponse(BaseModel):
    """Process-local status of one background consolidation run."""

    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: _NonEmptyText
    memory_bank_id: _NonEmptyText
    consolidator_type: ConsolidatorType
    max_entities: int = Field(gt=0)
    status: Literal[
        "running",
        "cancel_requested",
        "completed",
        "failed",
        "cancelled",
    ]
    attempted_entities: int = Field(ge=0)
    current_subject_entity_id: _NonEmptyText | None
    results: list[ConsolidationRunResult]
    stop_reason: Literal["no_eligible_entities", "max_entities_reached"] | None
    failure: ConsolidationRunFailure | None


class HealthResponse(BaseModel):
    """Minimal process health response."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["ok"] = "ok"
