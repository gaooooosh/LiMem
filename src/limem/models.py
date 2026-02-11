# -*- coding: utf-8 -*-
"""Data models for the LiMem long-term memory system.

Defines the structured event model with temporal validity tracking and
weighted relationship management.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Consistency(str, Enum):
    """Internal consistency markers for event validation."""

    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    UNCERTAIN = "uncertain"


@dataclass
class TimeRange:
    """Temporal extent of an event."""

    start: int = 0  # Unix timestamp
    end: int = 0  # Unix timestamp
    display_time_bucket: str = ""  # morning, afternoon, evening, night


@dataclass
class Participant:
    """Person involved in an event."""

    role: str  # e.g., "driver", "passenger", "child"
    seat: str = ""  # e.g., "front_left", "rear_right"


@dataclass
class Location:
    """Spatial context of an event."""

    geo_context: str = ""  # e.g., "home", "work", "on_highway"
    digital_context: str = ""  # e.g., "music_app", "navigation"


@dataclass
class Evidence:
    """Source evidence for an event."""

    source: str = ""  # e.g., "user_query", "system_response"
    snippet: str = ""  # Relevant text snippet
    timestamp: int = 0  # Unix timestamp
    confidence: float = 1.0  # Source confidence score


@dataclass
class EpisodicEventFrame:
    """Structured event frame extracted from episodic memory.

    Represents a consolidated semantic event with temporal validity
    tracking and weighted relationships to entities.
    """

    # Core semantic content
    summary: str = ""

    # Context metadata
    participants: list[dict] = field(default_factory=list)
    time_range: dict = field(default_factory=dict)
    location: dict = field(default_factory=dict)

    # Event semantics
    action: str = ""
    causality: str = ""
    evidence: list[dict] = field(default_factory=list)

    # Validation metadata
    consistency: str = Consistency.CONSISTENT.value

    # Temporal tracking
    last_active: int = 0  # Unix timestamp of last activation

    def to_db_fields(self) -> dict[str, Any]:
        """Convert to database field format.

        Returns:
            Dictionary with all fields ready for Kuzu insertion.
        """
        # Ensure participants is a list of dicts
        participants_data = []
        for p in self.participants:
            if isinstance(p, dict):
                participants_data.append(p)
            elif isinstance(p, Participant):
                participants_data.append({"role": p.role, "seat": p.seat})
            else:
                participants_data.append({"role": str(p), "seat": ""})

        # Ensure evidence is a list of dicts
        evidence_data = []
        for e in self.evidence:
            if isinstance(e, dict):
                evidence_data.append(e)
            elif isinstance(e, Evidence):
                evidence_data.append(
                    {
                        "source": e.source,
                        "snippet": e.snippet,
                        "timestamp": e.timestamp,
                        "confidence": e.confidence,
                    }
                )
            else:
                evidence_data.append({"source": str(e), "snippet": "", "timestamp": 0, "confidence": 1.0})

        # Handle time_range
        if isinstance(self.time_range, dict):
            time_range_data = self.time_range
        elif isinstance(self.time_range, TimeRange):
            time_range_data = {
                "start": self.time_range.start,
                "end": self.time_range.end,
                "display_time_bucket": self.time_range.display_time_bucket,
            }
        else:
            time_range_data = {"start": 0, "end": 0, "display_time_bucket": ""}

        # Handle location
        if isinstance(self.location, dict):
            location_data = self.location
        elif isinstance(self.location, Location):
            location_data = {
                "geo_context": self.location.geo_context,
                "digital_context": self.location.digital_context,
            }
        else:
            location_data = {"geo_context": "", "digital_context": ""}

        return {
            "summary": self.summary,
            "participants": participants_data,
            "time_range": time_range_data,
            "location": location_data,
            "action": self.action,
            "causality": self.causality,
            "evidence": evidence_data,
            "consistency": self.consistency,
            "last_active": self.last_active,
        }

    @classmethod
    def from_partial(cls, partial: dict[str, Any], current_time: int) -> "EpisodicEventFrame":
        """Create event frame from partial dictionary data.

        Args:
            partial: Dictionary with potentially missing fields.
            current_time: Current Unix timestamp for temporal fields.

        Returns:
            Populated EpisodicEventFrame instance.
        """
        return cls(
            summary=partial.get("summary", ""),
            participants=partial.get("participants", []),
            time_range=partial.get("time_range", {}),
            location=partial.get("location", {}),
            action=partial.get("action", ""),
            causality=partial.get("causality", ""),
            evidence=partial.get("evidence", []),
            consistency=partial.get("consistency", Consistency.CONSISTENT.value),
            last_active=partial.get("last_active", current_time),
        )


@dataclass
class RankedEvent:
    """Event with retrieval weight for ranking.

    Used in the retrieval pipeline to sort and filter events by
    their computed weight scores.
    """

    event_id: str
    summary: str
    weight: float
    c_valid: int
    t_valid: int
    t_expired: Optional[int]
    t_invalid: Optional[int]
    action: str = ""
    causality: str = ""
    participants: str = ""
    location: str = ""
    time_range: str = ""

    def __lt__(self, other: "RankedEvent") -> bool:
        """Enable sorting by weight (descending)."""
        return self.weight > other.weight


@dataclass
class ContextSnapshot:
    """Snapshot of the current retrieval context.

    Contains the query, extracted entities, and retrieved events
    for a single retrieval operation.
    """

    query: str = ""
    entities: list[str] = field(default_factory=list)
    raw_events: list[dict] = field(default_factory=list)
    ranked_events: list[RankedEvent] = field(default_factory=list)
    top_k_events: list[RankedEvent] = field(default_factory=list)


@dataclass
class ProactiveProposal:
    """Proactive suggestion based on memory retrieval.

    Generated when high-weight events match user intent patterns.
    """

    event_id: str
    proposal_type: str  # e.g., "music_suggestion", "route_recommendation"
    confidence: float
    reasoning: str = ""
    action_prompt: str = ""
