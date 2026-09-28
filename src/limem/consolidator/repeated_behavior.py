"""Explicit two-stage consolidation of repeated subject behavior."""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from limem.embedding_client import EmbeddingClient
from limem.episode_builder import EpisodeBuilder
from limem.llm_client import LLMClient
from limem.memory_store import (
    ConsolidationSupport,
    Entity,
    EntityIdentityConfirmation,
    EntityRef,
    Evidence,
    MemoryEmbedding,
    MemoryItem,
    MemoryRelation,
    MemoryStore,
)
from limem.raw_data_parser import VehicleRawDataParser

from .base import ConsolidationError, ConsolidationStage, SubjectConsolidator
from .models import (
    CandidateDiscoveryResult,
    CandidateProposal,
    ConditionSynthesisResult,
    ConsolidationResult,
    FinalizedCandidate,
    FinalizedSupport,
)
from .repeated_behavior_prompt import (
    CANDIDATE_DISCOVERY_SYSTEM_PROMPT,
    CONDITION_SYNTHESIS_SYSTEM_PROMPT,
    build_candidate_discovery_user_prompt,
    build_condition_synthesis_user_prompt,
    discovery_action_session,
    evidence_has_driver_action,
    render_evidence_session,
)
from .validator import ConsolidationValidator
from .vehicle_identity import FUNCTION_TEXT, confirm_ventilation_identity, references

OutputModel = TypeVar("OutputModel", bound=BaseModel)
ResultT = TypeVar("ResultT")


class _JSONClient(Protocol):
    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[OutputModel],
    ) -> OutputModel: ...


class RepeatedBehaviorConsolidator(SubjectConsolidator):
    """Find repeated behavior and persist supported candidate MemoryItems."""

    consolidator_type = "repeated_behavior"
    minimum_evidence_count = 5

    def __init__(
        self,
        memory_store: MemoryStore,
        llm_client: _JSONClient | None = None,
        embedding_client: EmbeddingClient | None = None,
        validator: ConsolidationValidator | None = None,
        before_persistence: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(memory_store)
        self._llm_client = llm_client if llm_client is not None else LLMClient()
        self._embedding_client = (
            embedding_client if embedding_client is not None else EmbeddingClient()
        )
        self._validator = (
            validator if validator is not None else ConsolidationValidator()
        )
        self._before_persistence = before_persistence

    def consolidate(self, subject_entity_id: str) -> ConsolidationResult:
        memory_items, evidence, session_text_by_evidence_id = self._at_stage(
            "source_loading",
            subject_entity_id,
            lambda: self._load_sources(subject_entity_id),
        )
        if not evidence:
            return ConsolidationResult(
                subject_entity_id=subject_entity_id,
                candidates=[],
                supports=[],
            )

        labels_by_evidence_id = {
            source.id: f"E{position}"
            for position, source in enumerate(evidence, start=1)
        }
        evidence_by_label = {
            labels_by_evidence_id[source.id]: source for source in evidence
        }
        session_text_by_label = {
            labels_by_evidence_id[evidence_id]: text
            for evidence_id, text in session_text_by_evidence_id.items()
        }
        action_session_text_by_label = {
            label: discovery_action_session(
                evidence_by_label[label],
                session_text_by_label[label],
            )
            for label in evidence_by_label
        }
        discovery = self._at_stage(
            "candidate_discovery",
            subject_entity_id,
            lambda: self._llm_client.generate_json(
                CANDIDATE_DISCOVERY_SYSTEM_PROMPT,
                build_candidate_discovery_user_prompt(
                    memory_items,
                    evidence,
                    labels_by_evidence_id,
                    session_text_by_label,
                ),
                CandidateDiscoveryResult,
            ),
        )
        self._at_stage(
            "fragment_validation",
            subject_entity_id,
            lambda: self._validator.validate_candidate_discovery(
                discovery,
                evidence_labels=list(evidence_by_label),
                session_text_by_label=action_session_text_by_label,
            ),
        )
        if not discovery.candidates:
            return ConsolidationResult(
                subject_entity_id=subject_entity_id,
                candidates=[],
                supports=[],
            )

        memory_items_by_id = {item.id: item for item in memory_items}
        conditions = self._at_stage(
            "condition_synthesis",
            subject_entity_id,
            lambda: self._synthesize_conditions(
                discovery.candidates,
                session_text_by_label,
            ),
        )
        finalized = self._at_stage(
            "fragment_validation",
            subject_entity_id,
            lambda: self._finalize_candidates(
                subject_entity_id,
                discovery,
                conditions,
                evidence_by_label,
                session_text_by_label,
                action_session_text_by_label,
            ),
        )

        candidates, supports, identity_confirmations = self._at_stage(
            "fragment_validation",
            subject_entity_id,
            lambda: self._materialize(
                subject_entity_id,
                finalized,
                evidence_by_label,
                memory_items_by_id,
            ),
        )
        if not candidates:
            return ConsolidationResult(
                subject_entity_id=subject_entity_id,
                candidates=[],
                supports=[],
            )

        embeddings = self._at_stage(
            "embedding",
            subject_entity_id,
            lambda: self._embed_candidates(candidates),
        )
        self._at_stage(
            "persistence",
            subject_entity_id,
            lambda: self._persist(candidates, supports, embeddings, identity_confirmations),
        )
        return ConsolidationResult(
            subject_entity_id=subject_entity_id,
            candidates=candidates,
            supports=supports,
        )

    def _persist(
        self,
        candidates: list[MemoryItem],
        supports: list[ConsolidationSupport],
        embeddings: list[MemoryEmbedding],
        identity_confirmations: list[EntityIdentityConfirmation],
    ) -> None:
        if self._before_persistence is not None:
            self._before_persistence()
        self._memory_store.write_consolidation(
            candidates, supports, embeddings, identity_confirmations=identity_confirmations
        )

    def _load_sources(
        self,
        subject_entity_id: str,
    ) -> tuple[list[MemoryItem], list[Evidence], dict[str, str]]:
        memory_items, evidence = self._load_direct_sources(subject_entity_id)
        parser = VehicleRawDataParser()
        builder = EpisodeBuilder()
        episodes_by_session_id = {}
        texts: dict[str, str] = {}
        for position, source in enumerate(evidence, start=1):
            if source.source_session_id not in episodes_by_session_id:
                stored_session = self._memory_store.get_source_session(
                    source.source_session_id
                )
                if stored_session.source_format != "vehicle_session_v1":
                    raise ValueError(
                        "RepeatedBehaviorConsolidator only supports "
                        f"vehicle_session_v1, got {stored_session.source_format!r} "
                        f"for Session {stored_session.session_id}"
                    )
                session = parser.parse(stored_session.raw_session_json)
                [episode] = builder.build(session)
                episodes_by_session_id[source.source_session_id] = episode
            texts[source.id] = render_evidence_session(
                f"E{position}",
                source,
                episodes_by_session_id[source.source_session_id],
            )
        evidence = [
            source
            for source in evidence
            if evidence_has_driver_action(source, texts[source.id])
        ]
        memory_item_ids = {source.memory_item_id for source in evidence}
        memory_items = [
            item for item in memory_items if item.id in memory_item_ids
        ]
        texts = {
            source.id: render_evidence_session(
                f"E{position}",
                source,
                episodes_by_session_id[source.source_session_id],
            )
            for position, source in enumerate(evidence, start=1)
        }
        return memory_items, evidence, texts

    def _synthesize_conditions(
        self,
        candidates: Sequence[CandidateProposal],
        session_text_by_label: dict[str, str],
    ) -> list[ConditionSynthesisResult]:
        def synthesize(candidate: CandidateProposal) -> ConditionSynthesisResult:
            return self._llm_client.generate_json(
                CONDITION_SYNTHESIS_SYSTEM_PROMPT,
                build_condition_synthesis_user_prompt(
                    candidate,
                    session_text_by_label,
                ),
                ConditionSynthesisResult,
            )

        if len(candidates) == 1:
            return [synthesize(candidates[0])]
        # Candidates are independent; persistence still waits for every result.
        with ThreadPoolExecutor(max_workers=len(candidates)) as executor:
            return list(executor.map(synthesize, candidates))

    def _finalize_candidates(
        self,
        subject_entity_id: str,
        discovery: CandidateDiscoveryResult,
        conditions: Sequence[ConditionSynthesisResult],
        evidence_by_label: dict[str, Evidence],
        session_text_by_label: dict[str, str],
        action_session_text_by_label: dict[str, str],
    ) -> list[FinalizedCandidate]:
        candidates = []
        for proposal, condition in zip(
            discovery.candidates,
            conditions,
            strict=True,
        ):
            self._validator.validate_condition_synthesis(
                proposal,
                condition,
                session_text_by_label=session_text_by_label,
            )
            candidates.append(
                FinalizedCandidate(
                    actions=proposal.actions,
                    statement=proposal.statement,
                    condition=condition.condition_text(),
                    supports=[
                        FinalizedSupport(
                            evidence=support.evidence,
                            condition_fragments=(
                                self._validator.merged_condition_fragments(
                                    proposal,
                                    condition,
                                    support.evidence,
                                    session_text_by_label,
                                )
                            ),
                            action_fragments=self._validator.action_fragments(
                                support,
                                action_session_text_by_label,
                            ),
                        )
                        for support in proposal.supports
                        if support.evidence in condition.supported_evidence()
                    ],
                )
            )
        self._validator.validate_candidates(
            candidates,
            group_labels=list(evidence_by_label),
            evidence_by_label=evidence_by_label,
            session_text_by_label=session_text_by_label,
            subject_entity_id=subject_entity_id,
        )
        return candidates

    def _materialize(
        self,
        subject_entity_id: str,
        finalized: Sequence[FinalizedCandidate],
        evidence_by_label: dict[str, Evidence],
        memory_items_by_id: dict[str, MemoryItem],
    ) -> tuple[
        list[MemoryItem], list[ConsolidationSupport], list[EntityIdentityConfirmation]
    ]:
        entities_by_id = {
            entity.id: entity for entity in self._memory_store.list_entities()
        }
        confirmations: list[EntityIdentityConfirmation] = []
        unresolved_function_ids: set[str] = set()
        for proposal in finalized:
            candidate_text = "\n".join(
                [proposal.statement, *proposal.actions, proposal.condition or ""]
            )
            if FUNCTION_TEXT not in candidate_text:
                continue
            ids = {
                entity_id
                for support in proposal.supports
                for text, entity_id in references(
                    memory_items_by_id[evidence_by_label[support.evidence].memory_item_id]
                )
                if text == FUNCTION_TEXT
            }
            if len({entities_by_id[eid].canonical_entity_id or eid for eid in ids}) > 1:
                unresolved_function_ids.update(ids)
        if unresolved_function_ids:
            confirmation = confirm_ventilation_identity(
                self._memory_store,
                [entities_by_id[eid] for eid in sorted(unresolved_function_ids)],
            )
            confirmations.append(confirmation)
            for eid in confirmation.entity_ids:
                entities_by_id[eid] = entities_by_id[eid].model_copy(
                    update={"canonical_entity_id": confirmation.entity_ids[0]}
                )
        candidates: list[MemoryItem] = []
        supports: list[ConsolidationSupport] = []

        for proposal in finalized:
            candidate_id = str(uuid4())
            source_items = [
                memory_items_by_id[
                    evidence_by_label[support.evidence].memory_item_id
                ]
                for support in proposal.supports
            ]
            candidates.append(
                self._materialize_candidate(
                    candidate_id,
                    subject_entity_id,
                    proposal,
                    source_items,
                    entities_by_id,
                )
            )
            supports.extend(
                ConsolidationSupport(
                    consolidator_type=self.consolidator_type,
                    candidate_memory_item_id=candidate_id,
                    source_evidence_id=evidence_by_label[support.evidence].id,
                    fragments=[
                        *support.condition_fragments,
                        *support.action_fragments,
                    ],
                )
                for support in proposal.supports
            )
        return candidates, supports, confirmations

    def _materialize_candidate(
        self,
        candidate_id: str,
        subject_entity_id: str,
        proposal: FinalizedCandidate,
        source_items: Sequence[MemoryItem],
        entities_by_id: dict[str, Entity],
    ) -> MemoryItem:
        relations = [
            MemoryRelation(
                predicate=action,
                target=None,
                target_entity_id=None,
            )
            for action in proposal.actions
        ]
        return MemoryItem(
            id=candidate_id,
            subject_entity_id=subject_entity_id,
            relations=relations,
            statement=proposal.statement,
            condition=proposal.condition,
            entity_refs=self._inherit_entity_refs(
                proposal,
                source_items,
                entities_by_id,
                subject_entity_id,
            ),
            status="candidate",
        )

    @staticmethod
    def _inherit_entity_refs(
        proposal: FinalizedCandidate,
        source_items: Sequence[MemoryItem],
        entities_by_id: dict[str, Entity],
        subject_entity_id: str,
    ) -> list[EntityRef]:
        candidate_text = "\n".join(
            [proposal.statement, *proposal.actions, proposal.condition or ""]
        )
        referenced_ids: dict[str, list[str]] = {}
        for item in source_items:
            for ref in item.entity_refs:
                referenced_ids.setdefault(ref.text, []).append(ref.entity_id)
            for relation in item.relations:
                if relation.target is not None and relation.target_entity_id is not None:
                    referenced_ids.setdefault(relation.target, []).append(
                        relation.target_entity_id
                    )

        inherited = []
        for text, entity_ids in referenced_ids.items():
            if text not in candidate_text:
                continue
            normalized_ids = {
                entities_by_id[entity_id].canonical_entity_id or entity_id
                for entity_id in entity_ids
            }
            if len(normalized_ids) != 1:
                raise ValueError(
                    f"source Entity reference {text!r} maps to multiple identities"
                )
            [entity_id] = normalized_ids
            if entity_id != subject_entity_id:
                inherited.append(EntityRef(text=text, entity_id=entity_id))
        return inherited

    def _embed_candidates(
        self,
        candidates: Sequence[MemoryItem],
    ) -> list[MemoryEmbedding]:
        vectors = self._embedding_client.embed_documents(
            [
                "\n".join(
                    value
                    for value in (candidate.statement, candidate.condition)
                    if value is not None
                )
                for candidate in candidates
            ]
        )
        return [
            MemoryEmbedding(
                memory_item_id=candidate.id,
                model=self._embedding_client.settings.model,
                dimensions=self._embedding_client.settings.dimension,
                vector=vector,
            )
            for candidate, vector in zip(candidates, vectors, strict=True)
        ]

    @staticmethod
    def _at_stage(
        stage: ConsolidationStage,
        subject_entity_id: str,
        operation: Callable[[], ResultT],
    ) -> ResultT:
        try:
            return operation()
        except Exception as exc:
            raise ConsolidationError(stage, subject_entity_id, exc) from exc
