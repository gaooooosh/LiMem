"""Entity familiarity consolidation placeholder."""

from .base import SubjectConsolidator
from .models import ConsolidationResult


class EntityFamiliarityConsolidator(SubjectConsolidator):
    """Reserve subject-scoped familiarity inference for a later implementation."""

    consolidator_type = "entity_familiarity"

    def consolidate(self, subject_entity_id: str) -> ConsolidationResult:
        # TODO: infer familiarity from repeated selection of one canonical Entity.
        raise NotImplementedError(
            "EntityFamiliarityConsolidator is not implemented"
        )
