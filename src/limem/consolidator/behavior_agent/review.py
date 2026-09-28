"""Re-derive behavior from visible operations, without the prior candidate."""

from typing import Literal
from pydantic import Field, create_model
from .models import StrictModel
from . import environment
from .prompts import REVIEW_SYSTEM as SYSTEM

__all__ = ["SYSTEM", "Review", "step_model", "view"]


class Review(StrictModel):
    tool: Literal["review"]
    behavior: str = Field(min_length=1)
    records: list[str] = Field(min_length=1)
    missing_information: str


def step_model(state):
    return create_model(
        "SourceReview",
        __base__=Review,
        records=(list[Literal[tuple(state["visible"])]], Field(min_length=1)),
    )


def view(data, state):
    return dict(
        records=[
            environment.card_view(r)
            for r in data["records"]
            if r["id"] in state["visible"]
        ]
    )
