"""Pydantic models for the LiMem HTTP service."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    data: Any
    timestamp: int | None = None


class IngestResponse(BaseModel):
    event_id: str
    summary: str
    is_new: bool
    entities_created: int
    event_count: int


class QueryRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1)


class QueryResult(BaseModel):
    event_id: str
    summary: str
    action: str
    causality: str
    timestamp: int
    score: float


class QueryResponse(BaseModel):
    results: list[QueryResult]
    total: int


class EvolveResponse(BaseModel):
    message: str
    details: dict[str, int] = Field(default_factory=dict)
