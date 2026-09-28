"""Deterministic contract checks for LLM consolidation output."""

from collections.abc import Sequence

from pydantic import ValidationError

from limem.memory_store import Evidence, MemoryItem, MemoryRelation

from .models import (
    CandidateDiscoveryResult,
    CandidateProposal,
    CandidateSupportProposal,
    ConditionSynthesisResult,
    ConditionSupportProposal,
    FinalizedCandidate,
)
from .repeated_behavior_prompt import (
    driver_action_clauses,
    pre_action_observation_clauses,
)


class ConsolidationValidationError(ValueError):
    """An LLM response violates the deterministic consolidation contract."""


class ConsolidationValidator:
    """Validate labels, independent support, fragments, and MemoryItem shape."""

    def validate_candidate_discovery(
        self,
        result: CandidateDiscoveryResult,
        *,
        evidence_labels: Sequence[str],
        session_text_by_label: dict[str, str],
    ) -> None:
        known = set(evidence_labels)
        for candidate_index, candidate in enumerate(result.candidates):
            labels = [support.evidence for support in candidate.supports]
            if len(set(labels)) < 2:
                raise ConsolidationValidationError(
                    f"candidate {candidate_index} needs at least two independent Evidence"
                )
            if len(labels) != len(set(labels)):
                raise ConsolidationValidationError(
                    f"candidate {candidate_index} repeats an Evidence label"
                )
            for support in candidate.supports:
                if support.evidence not in known:
                    raise ConsolidationValidationError(
                        f"candidate {candidate_index} references unknown Evidence "
                        f"{support.evidence}"
                    )
                self.action_fragments(support, session_text_by_label)

    def validate_condition_synthesis(
        self,
        candidate: CandidateProposal,
        result: ConditionSynthesisResult,
        *,
        session_text_by_label: dict[str, str],
    ) -> None:
        known = {support.evidence for support in candidate.supports}
        for fact_index, fact in enumerate(result.facts):
            labels = [support.evidence for support in fact.supports]
            if len(labels) != len(set(labels)):
                raise ConsolidationValidationError(
                    f"condition fact {fact_index} repeats an Evidence label"
                )
            if not set(labels) <= known:
                raise ConsolidationValidationError(
                    f"condition fact {fact_index} cites Evidence outside its fixed group"
                )
            if len(labels) * 2 <= len(known):
                raise ConsolidationValidationError(
                    f"condition fact {fact_index} must cite more than half of fixed Evidence"
                )
            for support in fact.supports:
                self.condition_fragments(
                    ConditionSupportProposal(
                        evidence=support.evidence,
                        condition_clause_indices=support.clause_indices,
                    ),
                    session_text_by_label,
                )
        if len(result.supported_evidence()) * 2 <= len(known):
            raise ConsolidationValidationError(
                "combined condition must be supported by more than half of fixed Evidence"
            )

    def merged_condition_fragments(
        self,
        candidate: CandidateProposal,
        result: ConditionSynthesisResult,
        evidence: str,
        session_text_by_label: dict[str, str],
    ) -> list[str]:
        indices = sorted(
            {
                index
                for fact in result.facts
                for support in fact.supports
                if support.evidence == evidence
                for index in support.clause_indices
            }
        )
        return self.condition_fragments(
            ConditionSupportProposal(
                evidence=evidence,
                condition_clause_indices=indices,
            ),
            session_text_by_label,
        )

    @staticmethod
    def action_fragments(
        support: CandidateSupportProposal,
        session_text_by_label: dict[str, str],
    ) -> list[str]:
        indices = support.action_clause_indices
        if len(indices) != len(set(indices)):
            raise ConsolidationValidationError(
                f"action support {support.evidence} repeats a clause index"
            )
        clauses = driver_action_clauses(session_text_by_label[support.evidence])
        if any(index < 1 or index > len(clauses) for index in indices):
            raise ConsolidationValidationError(
                f"action support {support.evidence} has an invalid clause index"
            )
        return [clauses[index - 1] for index in indices]

    @staticmethod
    def condition_fragments(
        support: ConditionSupportProposal,
        session_text_by_label: dict[str, str],
    ) -> list[str]:
        indices = support.condition_clause_indices
        if len(indices) != len(set(indices)):
            raise ConsolidationValidationError(
                f"condition support {support.evidence} repeats a clause index"
            )
        clauses = pre_action_observation_clauses(
            session_text_by_label[support.evidence]
        )
        if any(index < 1 or index > len(clauses) for index in indices):
            raise ConsolidationValidationError(
                f"condition support {support.evidence} has an invalid clause index"
            )
        return [clauses[index - 1] for index in indices]

    def validate_candidates(
        self,
        candidates: Sequence[FinalizedCandidate],
        *,
        group_labels: Sequence[str],
        evidence_by_label: dict[str, Evidence],
        session_text_by_label: dict[str, str],
        subject_entity_id: str,
    ) -> None:
        known = set(group_labels)
        for candidate_index, candidate in enumerate(candidates):
            self._validate_candidate_support(
                candidate,
                candidate_index=candidate_index,
                known_labels=known,
                evidence_by_label=evidence_by_label,
                session_text_by_label=session_text_by_label,
            )
            self._validate_memory_item(candidate, subject_entity_id, candidate_index)

    @staticmethod
    def _validate_candidate_support(
        candidate: FinalizedCandidate,
        *,
        candidate_index: int,
        known_labels: set[str],
        evidence_by_label: dict[str, Evidence],
        session_text_by_label: dict[str, str],
    ) -> None:
        labels = [support.evidence for support in candidate.supports]
        if len(labels) < 2 or len(set(labels)) < 2:
            raise ConsolidationValidationError(
                f"candidate {candidate_index} needs at least two independent Evidence"
            )
        if len(labels) != len(set(labels)):
            raise ConsolidationValidationError(
                f"candidate {candidate_index} repeats an Evidence label"
            )

        for support_index, support in enumerate(candidate.supports):
            label = support.evidence
            if label not in known_labels:
                raise ConsolidationValidationError(
                    f"candidate {candidate_index} references Evidence {label} "
                    "outside its discovery group"
                )
            session_source_text = "\n".join(
                session_text_by_label[label].splitlines()[2:]
            )
            action_prefixes = tuple(
                f"[{raw_event_id}] Action:"
                for raw_event_id in evidence_by_label[label].raw_event_ids
            )
            evidence_action_text = "\n".join(
                line
                for line in session_text_by_label[label].splitlines()[2:]
                if line.startswith(action_prefixes)
            )
            for kind, fragments in (
                ("condition", support.condition_fragments),
                ("action", support.action_fragments),
            ):
                if not fragments:
                    raise ConsolidationValidationError(
                        f"candidate {candidate_index} support {support_index} "
                        f"has no {kind} fragments"
                    )
                for fragment in fragments:
                    if not fragment.strip() or "\n" in fragment or "\r" in fragment:
                        raise ConsolidationValidationError(
                            f"candidate {candidate_index} support {support_index} "
                            f"has an invalid {kind} fragment"
                        )
                    source_text = (
                        session_source_text
                        if kind == "condition"
                        else evidence_action_text
                    )
                    if fragment not in source_text:
                        raise ConsolidationValidationError(
                            f"candidate {candidate_index} support {support_index} "
                            f"{kind} fragment is not verbatim in the allowed "
                            f"source for Evidence {label}: "
                            f"{fragment!r}"
                        )

    @staticmethod
    def _validate_memory_item(
        candidate: FinalizedCandidate,
        subject_entity_id: str,
        candidate_index: int,
    ) -> None:
        try:
            MemoryItem(
                id=f"candidate-validation-{candidate_index}",
                subject_entity_id=subject_entity_id,
                relations=[
                    MemoryRelation(
                        predicate=action,
                        target=None,
                        target_entity_id=None,
                    )
                    for action in candidate.actions
                ],
                statement=candidate.statement,
                condition=candidate.condition,
                entity_refs=[],
                status="candidate",
            )
        except ValidationError as exc:
            raise ConsolidationValidationError(
                f"candidate {candidate_index} is not a valid MemoryItem: {exc}"
            ) from exc
