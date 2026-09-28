"""Tool contracts frozen with behavior-react-20260923."""

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Propose(StrictModel):
    tool: Literal["propose"]
    behavior: str = Field(min_length=1)
    condition: str | None
    supports: list[str]
    open_question: str


class MatchQuery(StrictModel):
    selection: Literal["match"]
    field: str = Field(min_length=1)
    position: Literal["before", "after", "delta"]
    operator: Literal["eq", "ne"]
    value: str | int | float | bool | None


class UnreadQuery(StrictModel):
    selection: Literal["unread"]


class SourceQuery(StrictModel):
    selection: Literal["source"]
    records: list[str] = Field(min_length=1)


class Inspect(StrictModel):
    tool: Literal["inspect"]
    query: Annotated[
        UnreadQuery | SourceQuery | MatchQuery, Field(discriminator="selection")
    ]
