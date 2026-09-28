"""Explicit memory consolidation interfaces and implementations."""

from .base import ConsolidationError, Consolidator, SubjectConsolidator
from .entity_familiarity import EntityFamiliarityConsolidator
from .models import (
    CandidateDiscoveryResult,
    CandidateProposal,
    CandidateSupportProposal,
    ConditionFactProposal,
    ConditionFactSupport,
    ConditionSupportProposal,
    ConditionSynthesisResult,
    ConsolidationResult,
    FinalizedCandidate,
    FinalizedSupport,
)
from .repeated_behavior import RepeatedBehaviorConsolidator
from .validator import ConsolidationValidationError, ConsolidationValidator

__all__ = [
    "CandidateDiscoveryResult",
    "CandidateProposal",
    "CandidateSupportProposal",
    "ConsolidationError",
    "ConsolidationResult",
    "ConsolidationValidationError",
    "ConsolidationValidator",
    "Consolidator",
    "ConditionFactProposal",
    "ConditionFactSupport",
    "ConditionSupportProposal",
    "ConditionSynthesisResult",
    "FinalizedCandidate",
    "FinalizedSupport",
    "EntityFamiliarityConsolidator",
    "RepeatedBehaviorConsolidator",
    "SubjectConsolidator",
]
