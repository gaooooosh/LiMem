"""Rank text and Entity seeds, then expand each candidate path twice."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from limem.embedding_client import EmbeddingClient
from limem.memory_store import Entity, MemoryItem, MemoryStore
from limem.resolver import QueryResolutionInput, QueryResolver

from .models import (
    ConsolidationSupportRetrievalVia,
    EntityRetrievalVia,
    RetrievalRequest,
    RetrievalResult,
    RetrievalStep,
    RetrievalVia,
)


@dataclass(frozen=True)
class _Score:
    bm25_score: float
    bm25_rank: int
    embedding_score: float
    embedding_rank: int
    entity_match_score: int
    evidence_support_score: int
    rrf_score: float
    rrf_rank: int


@dataclass(frozen=True)
class _Occurrence:
    canonical_entity_id: str
    field: str
    position: int | None


@dataclass(frozen=True)
class _EntityIndex:
    canonical_by_id: dict[str, str]
    ids_by_canonical: dict[str, tuple[str, ...]]


class Retriever:
    """Retrieve ranked MemoryItem paths without LLM calls or index mutation."""

    def __init__(
        self,
        memory_store: MemoryStore,
        query_resolver: QueryResolver,
        embedding_client: EmbeddingClient,
    ) -> None:
        self._memory_store = memory_store
        self._query_resolver = query_resolver
        self._embedding_client = embedding_client

    def retrieve(self, request: RetrievalRequest) -> list[RetrievalResult]:
        """Return complete paths ordered by their per-step RRF rank sequence."""

        memory_items = self._memory_store.list_memory_items()
        entity_index = _build_entity_index(self._memory_store.list_entities())
        if not memory_items:
            return []

        item_by_id = {item.id: item for item in memory_items}
        resolved = self._query_resolver.resolve(
            QueryResolutionInput(query=request.query, context=request.context)
        )
        resolved_entity_ids = _resolved_entity_ids(resolved, entity_index)
        scores = self._score_items(
            request,
            memory_items,
            resolved_entity_ids,
            entity_index,
        )

        seed_sources: dict[str, str | None] = {}
        for item in sorted(memory_items, key=lambda value: scores[value.id].rrf_rank)[
            : request.seed_limit
        ]:
            seed_sources[item.id] = None

        for entity_id in resolved_entity_ids:
            adjacent = self._adjacent_for_canonical(entity_id, entity_index)
            for item in sorted(adjacent, key=lambda value: scores[value.id].rrf_rank)[
                : request.neighbor_limit
            ]:
                seed_sources.setdefault(item.id, entity_id)

        paths = [
            [
                _step(
                    item_by_id[memory_item_id],
                    scores[memory_item_id],
                    stage="seed",
                    seed_entity_id=seed_entity_id,
                )
            ]
            for memory_item_id, seed_entity_id in seed_sources.items()
        ]
        paths = self._expand(paths, 1, request.neighbor_limit, scores, entity_index)
        paths = self._expand(paths, 2, request.neighbor_limit, scores, entity_index)
        paths = _deduplicate_paths(paths)
        paths.sort(
            key=lambda path: (
                tuple(step.rrf_rank for step in path),
                tuple(step.memory_item.id for step in path),
            )
        )
        return [
            RetrievalResult(steps=self._append_support_steps(path, item_by_id, scores))
            for path in paths[: request.limit]
        ]

    def _append_support_steps(
        self,
        path: list[RetrievalStep],
        item_by_id: dict[str, MemoryItem],
        scores: dict[str, _Score],
    ) -> list[RetrievalStep]:
        source_memory_item_ids = [step.memory_item.id for step in path]
        source_ids_by_candidate = (
            self._memory_store.get_consolidation_candidate_source_memory_item_ids(
                source_memory_item_ids
            )
        )
        path_id_set = set(source_memory_item_ids)
        candidates = sorted(
            (
                (candidate_memory_item_id, source_ids)
                for candidate_memory_item_id, source_ids in source_ids_by_candidate.items()
                if candidate_memory_item_id not in path_id_set
            ),
            key=lambda value: (scores[value[0]].rrf_rank, value[0]),
        )
        support_steps_by_source_id: dict[str, list[RetrievalStep]] = {}
        for candidate_memory_item_id, source_ids in candidates:
            support_steps_by_source_id.setdefault(source_ids[0], []).append(
                _step(
                    item_by_id[candidate_memory_item_id],
                    scores[candidate_memory_item_id],
                    stage="support",
                    via=ConsolidationSupportRetrievalVia(
                        source_memory_item_ids=source_ids
                    ),
                )
            )
        return [
            value
            for step in path
            for value in (
                step,
                *support_steps_by_source_id.get(step.memory_item.id, []),
            )
        ]

    def _score_items(
        self,
        request: RetrievalRequest,
        memory_items: list[MemoryItem],
        resolved_entity_ids: list[str],
        entity_index: _EntityIndex,
    ) -> dict[str, _Score]:
        ids = [item.id for item in memory_items]
        lexical_hits = self._memory_store.search_lexical(
            request.query,
            request.context,
            len(ids),
        )
        bm25_by_id = {hit.memory_item_id: hit.bm25_score for hit in lexical_hits}
        bm25_scores = {
            memory_item_id: bm25_by_id.get(memory_item_id, 0.0)
            for memory_item_id in ids
        }

        embeddings = self._memory_store.get_embeddings(ids)
        embedding_by_id = {value.memory_item_id: value for value in embeddings}
        missing = [item.id for item in memory_items if item.id not in embedding_by_id]
        if missing:
            raise ValueError(f"missing embeddings for MemoryItems: {missing}")

        expected_model = self._embedding_client.settings.model
        expected_dimension = self._embedding_client.settings.dimension
        for memory_item_id in ids:
            embedding = embedding_by_id[memory_item_id]
            if embedding.model != expected_model:
                raise ValueError(f"embedding model mismatch for {memory_item_id}")
            if embedding.dimensions != expected_dimension:
                raise ValueError(f"embedding dimension mismatch for {memory_item_id}")
            _require_vector(
                embedding.vector,
                expected_dimension,
                f"MemoryItem {memory_item_id}",
            )

        request_text = "\n".join(
            value for value in (request.query, request.context) if value is not None
        )
        query_vector = self._embedding_client.embed_query(request_text)
        _require_vector(query_vector, expected_dimension, "query embedding")
        embedding_scores = {
            item.id: _cosine(query_vector, embedding_by_id[item.id].vector)
            for item in memory_items
        }
        resolved_entity_id_set = set(resolved_entity_ids)
        entity_match_scores = {
            item.id: len(
                resolved_entity_id_set & _non_subject_entity_ids(item, entity_index)
            )
            for item in memory_items
        }
        evidence_counts = self._memory_store.count_direct_evidence(ids)
        evidence_support_scores = {
            memory_item_id: _bounded_evidence_support_score(
                evidence_counts[memory_item_id]
            )
            for memory_item_id in ids
        }

        bm25_ranks = _rank(ids, bm25_scores, descending=False)
        embedding_ranks = _rank(ids, embedding_scores, descending=True)
        rrf_scores = {
            memory_item_id: request.bm25_weight
            / (request.rrf_k + bm25_ranks[memory_item_id])
            + 1 / (request.rrf_k + embedding_ranks[memory_item_id])
            + entity_match_scores[memory_item_id]
            * request.entity_match_weight
            / (request.rrf_k + 1)
            + evidence_support_scores[memory_item_id]
            * request.evidence_support_weight
            / (request.rrf_k + 1)
            for memory_item_id in ids
        }
        rrf_ranks = _rank(ids, rrf_scores, descending=True)
        return {
            memory_item_id: _Score(
                bm25_score=bm25_scores[memory_item_id],
                bm25_rank=bm25_ranks[memory_item_id],
                embedding_score=embedding_scores[memory_item_id],
                embedding_rank=embedding_ranks[memory_item_id],
                entity_match_score=entity_match_scores[memory_item_id],
                evidence_support_score=evidence_support_scores[memory_item_id],
                rrf_score=rrf_scores[memory_item_id],
                rrf_rank=rrf_ranks[memory_item_id],
            )
            for memory_item_id in ids
        }

    def _expand(
        self,
        paths: list[list[RetrievalStep]],
        round_number: int,
        neighbor_limit: int,
        scores: dict[str, _Score],
        entity_index: _EntityIndex,
    ) -> list[list[RetrievalStep]]:
        expanded_paths: list[list[RetrievalStep]] = []
        for path in paths:
            if round_number == 2 and path[-1].stage != "expansion_1":
                expanded_paths.append(path)
                continue

            path_ids = {step.memory_item.id for step in path}
            incoming_entity_id = path[-1].via.entity_id if path[-1].via else None
            occurrences = _occurrences(path[-1].memory_item, entity_index)
            occurrences_by_entity: dict[str, list[_Occurrence]] = {}
            for occurrence in occurrences:
                if occurrence.canonical_entity_id == incoming_entity_id:
                    continue
                occurrences_by_entity.setdefault(
                    occurrence.canonical_entity_id,
                    [],
                ).append(occurrence)

            extensions: list[list[RetrievalStep]] = []
            seen_neighbors: set[str] = set()
            for entity_id, previous_occurrences in occurrences_by_entity.items():
                candidates: list[tuple[MemoryItem, EntityRetrievalVia]] = []
                for candidate in self._adjacent_for_canonical(entity_id, entity_index):
                    if candidate.id in path_ids:
                        continue
                    via = _select_via(
                        path[-1].memory_item.id,
                        entity_id,
                        previous_occurrences,
                        _occurrences(candidate, entity_index),
                    )
                    if via is not None:
                        candidates.append((candidate, via))

                candidates.sort(key=lambda value: scores[value[0].id].rrf_rank)
                for candidate, via in candidates[:neighbor_limit]:
                    if candidate.id in seen_neighbors:
                        continue
                    seen_neighbors.add(candidate.id)
                    extensions.append(
                        [
                            *path,
                            _step(
                                candidate,
                                scores[candidate.id],
                                stage=f"expansion_{round_number}",
                                via=via,
                            ),
                        ]
                    )

            expanded_paths.extend(extensions if extensions else [path])
        return expanded_paths

    def _adjacent_for_canonical(
        self,
        canonical_entity_id: str,
        entity_index: _EntityIndex,
    ) -> list[MemoryItem]:
        return self._memory_store.get_adjacent_memory_items(
            entity_index.ids_by_canonical[canonical_entity_id]
        )


def _step(
    item: MemoryItem,
    score: _Score,
    *,
    stage: str,
    seed_entity_id: str | None = None,
    via: RetrievalVia | None = None,
) -> RetrievalStep:
    return RetrievalStep(
        memory_item=item,
        stage=stage,
        seed_entity_id=seed_entity_id,
        via=via,
        bm25_score=score.bm25_score,
        bm25_rank=score.bm25_rank,
        embedding_score=score.embedding_score,
        embedding_rank=score.embedding_rank,
        entity_match_score=score.entity_match_score,
        evidence_support_score=score.evidence_support_score,
        rrf_score=score.rrf_score,
        rrf_rank=score.rrf_rank,
    )


def _rank(
    ids: list[str],
    scores: dict[str, float],
    *,
    descending: bool,
) -> dict[str, int]:
    ordered = sorted(
        ids,
        key=lambda memory_item_id: (
            -scores[memory_item_id] if descending else scores[memory_item_id],
            memory_item_id,
        ),
    )
    ranks: dict[str, int] = {}
    previous_score: float | None = None
    current_rank = 0
    for position, memory_item_id in enumerate(ordered, 1):
        score = scores[memory_item_id]
        if position == 1 or score != previous_score:
            current_rank = position
            previous_score = score
        ranks[memory_item_id] = current_rank
    return ranks


def _bounded_evidence_support_score(evidence_count: int) -> int:
    return int(evidence_count >= 2)


def _cosine(left: list[float], right: list[float]) -> float:
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def _require_vector(
    vector: list[float],
    expected_dimension: int,
    label: str,
) -> None:
    if len(vector) != expected_dimension:
        raise ValueError(f"{label} dimension does not match EmbeddingClient settings")
    if sqrt(sum(value * value for value in vector)) == 0:
        raise ValueError(f"{label} cannot be zero")


def _build_entity_index(entities: list[Entity]) -> _EntityIndex:
    entity_by_id = {entity.id: entity for entity in entities}
    canonical_by_id: dict[str, str] = {}
    for entity in entities:
        current = entity
        visited: set[str] = set()
        while current.canonical_entity_id not in (None, current.id):
            if current.id in visited:
                raise ValueError("canonical Entity cycle")
            visited.add(current.id)
            current = entity_by_id[current.canonical_entity_id]
        canonical_by_id[entity.id] = current.id

    ids_by_canonical: dict[str, list[str]] = {}
    for entity_id, canonical_entity_id in canonical_by_id.items():
        ids_by_canonical.setdefault(canonical_entity_id, []).append(entity_id)
    return _EntityIndex(
        canonical_by_id=canonical_by_id,
        ids_by_canonical={
            entity_id: tuple(sorted(member_ids))
            for entity_id, member_ids in ids_by_canonical.items()
        },
    )


def _resolved_entity_ids(resolved, entity_index: _EntityIndex) -> list[str]:
    values: list[str] = []
    for mention in resolved.entity_mentions:
        for entity_id in mention.entity_ids:
            canonical_entity_id = entity_index.canonical_by_id[entity_id]
            if canonical_entity_id not in values:
                values.append(canonical_entity_id)
    return values


def _non_subject_entity_ids(
    item: MemoryItem,
    entity_index: _EntityIndex,
) -> set[str]:
    values = {
        entity_index.canonical_by_id[relation.target_entity_id]
        for relation in item.relations
        if relation.target_entity_id is not None
    }
    values.update(
        entity_index.canonical_by_id[entity_ref.entity_id]
        for entity_ref in item.entity_refs
    )
    return values


def _occurrences(item: MemoryItem, entity_index: _EntityIndex) -> list[_Occurrence]:
    values = [
        _Occurrence(
            canonical_entity_id=entity_index.canonical_by_id[item.subject_entity_id],
            field="subject",
            position=None,
        )
    ]
    values.extend(
        _Occurrence(
            canonical_entity_id=entity_index.canonical_by_id[relation.target_entity_id],
            field="relation_target",
            position=position,
        )
        for position, relation in enumerate(item.relations)
        if relation.target_entity_id is not None
    )
    values.extend(
        _Occurrence(
            canonical_entity_id=entity_index.canonical_by_id[entity_ref.entity_id],
            field="entity_ref",
            position=position,
        )
        for position, entity_ref in enumerate(item.entity_refs)
    )
    return values


def _select_via(
    from_memory_item_id: str,
    entity_id: str,
    previous_occurrences: list[_Occurrence],
    current_occurrences: list[_Occurrence],
) -> EntityRetrievalVia | None:
    for previous in previous_occurrences:
        for current in current_occurrences:
            if current.canonical_entity_id != entity_id:
                continue
            # Sharing only a subject does not establish a useful path connection.
            if previous.field == "subject" and current.field == "subject":
                continue
            return EntityRetrievalVia(
                from_memory_item_id=from_memory_item_id,
                entity_id=entity_id,
                previous_field=previous.field,
                previous_position=previous.position,
                current_field=current.field,
                current_position=current.position,
            )
    return None


def _deduplicate_paths(
    paths: list[list[RetrievalStep]],
) -> list[list[RetrievalStep]]:
    values: list[list[RetrievalStep]] = []
    seen: set[tuple[str, ...]] = set()
    for path in paths:
        key = tuple(step.memory_item.id for step in path)
        if key not in seen:
            seen.add(key)
            values.append(path)
    return values
