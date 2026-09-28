"""Base interfaces for explicit memory consolidation."""

from abc import ABC, abstractmethod
from typing import Literal

from limem.memory_store import Evidence, MemoryItem, MemoryStore

from .models import ConsolidationResult

ConsolidationStage = Literal[
    "source_loading",
    "candidate_discovery",
    "condition_synthesis",
    "fragment_validation",
    "embedding",
    "persistence",
]


class ConsolidationError(RuntimeError):
    """A consolidation stage failed before the operation could complete."""

    def __init__(
        self,
        stage: ConsolidationStage,
        subject_entity_id: str,
        cause: Exception,
    ) -> None:
        self.stage = stage
        self.subject_entity_id = subject_entity_id
        self.cause = cause
        super().__init__(
            f"consolidation failed at {stage} for subject "
            f"{subject_entity_id}: {cause}"
        )


class Consolidator(ABC):
    """Run one explicit consolidation operation."""

    @abstractmethod
    def consolidate(self, subject_entity_id: str) -> ConsolidationResult:
        """Consolidate one requested scope or raise with the failed stage."""


class SubjectConsolidator(Consolidator, ABC):
    """Consolidate direct memory owned by one confirmed Entity subject."""

    consolidator_type: str

    def __init__(self, memory_store: MemoryStore) -> None:
        self._memory_store = memory_store

    def _load_direct_sources(
        self,
        subject_entity_id: str,
    ) -> tuple[list[MemoryItem], list[Evidence]]:
        memory_items = self._memory_store.get_active_direct_memory_items(
            subject_entity_id
        )
        evidence = self._memory_store.get_unconsolidated_evidence(
            [item.id for item in memory_items],
            consolidator_type=self.consolidator_type,
        )
        return memory_items, evidence
