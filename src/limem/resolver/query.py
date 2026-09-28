"""Deterministic QueryResolver backed by current MemoryStore Entity surfaces."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from .base import Resolver
from .models import QueryResolutionInput, ResolvedEntityMention, ResolvedQuery


class _EntityRecord(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def aliases(self) -> list[str]: ...

    @property
    def scope_text(self) -> str | None: ...


class _EntityReader(Protocol):
    def list_entities(self) -> Sequence[_EntityRecord]: ...


@dataclass(frozen=True)
class _SurfaceMatch:
    start: int
    end: int
    text: str
    entity_ids: tuple[str, ...]


class QueryResolver(Resolver[QueryResolutionInput, ResolvedQuery]):
    """Link explicit query and context surfaces to existing Entity IDs."""

    def __init__(self, memory_store: _EntityReader) -> None:
        self._memory_store = memory_store

    @property
    def resolution_description(self) -> str:
        return (
            "Resolve query and context surfaces against current Entity names, "
            "aliases, and Episode scope text."
        )

    def resolve(self, value: QueryResolutionInput) -> ResolvedQuery:
        surfaces = _collect_surfaces(self._memory_store.list_entities())
        mentions: list[ResolvedEntityMention] = []

        if value.query is not None:
            mentions.extend(_resolve_text(value.query, "query", surfaces))
        if value.context is not None:
            mentions.extend(_resolve_text(value.context, "context", surfaces))

        return ResolvedQuery(
            query=value.query,
            context=value.context,
            entity_mentions=mentions,
        )


def _collect_surfaces(entities: Sequence[_EntityRecord]) -> dict[str, tuple[str, ...]]:
    entity_ids_by_surface: dict[str, set[str]] = {}
    for entity in entities:
        values = [entity.name, *entity.aliases]
        if entity.scope_text is not None:
            values.append(entity.scope_text)
        for surface in dict.fromkeys(values):
            entity_ids_by_surface.setdefault(surface, set()).add(entity.id)

    return {
        surface: tuple(sorted(entity_ids))
        for surface, entity_ids in entity_ids_by_surface.items()
    }


def _resolve_text(
    text: str,
    source: Literal["query", "context"],
    surfaces: dict[str, tuple[str, ...]],
) -> list[ResolvedEntityMention]:
    candidates: list[_SurfaceMatch] = []
    for surface, entity_ids in surfaces.items():
        start = text.find(surface)
        while start >= 0:
            candidates.append(
                _SurfaceMatch(
                    start=start,
                    end=start + len(surface),
                    text=surface,
                    entity_ids=entity_ids,
                )
            )
            start = text.find(surface, start + 1)

    # Select longer surfaces first, then restore source position order.
    selected: list[_SurfaceMatch] = []
    for candidate in sorted(
        candidates,
        key=lambda match: (-len(match.text), match.start, match.text),
    ):
        if any(_overlaps(candidate, existing) for existing in selected):
            continue
        selected.append(candidate)
    selected.sort(key=lambda match: (match.start, match.end, match.text))

    return [
        ResolvedEntityMention(
            text=match.text,
            source=source,
            entity_ids=list(match.entity_ids),
        )
        for match in selected
    ]


def _overlaps(left: _SurfaceMatch, right: _SurfaceMatch) -> bool:
    return left.start < right.end and right.start < left.end
