"""Generate candidate claims, classify target Entities, and attach Evidence."""

from limem.episode_builder import EpisodeInput
from limem.llm_client import LLMClient

from .models import (
    ExtractionItem,
    L2Result,
    LLMClaimItem,
    LLMClaimResult,
    TargetEntityResult,
)
from .prompt import (
    CLAIM_SYSTEM_PROMPT,
    TARGET_ENTITY_SYSTEM_PROMPT,
    build_claim_user_prompt,
    build_target_entity_user_prompt,
)


class MemoryItemExtractor:
    """Extract complete claims, then classify their relation targets."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client if llm_client is not None else LLMClient()

    def extract(
        self, episode: EpisodeInput, *, claim_episode: EpisodeInput | None = None,
    ) -> L2Result:
        """Extract claims and complete deterministic fields."""

        if not episode.events:
            return L2Result(items=[])

        claim_input = episode if claim_episode is None else claim_episode
        if (
            claim_input.episode_id != episode.episode_id
            or claim_input.required_actions != episode.required_actions
            or [(e.raw_event_id, e.action) for e in claim_input.events]
            != [(e.raw_event_id, e.action) for e in episode.events]
        ):
            raise ValueError("claim Episode may only change event context")

        claim_result = self._llm_client.generate_json(
            CLAIM_SYSTEM_PROMPT,
            build_claim_user_prompt(claim_input),
            LLMClaimResult,
        )
        events_by_id = {event.raw_event_id: event for event in episode.events}
        event_positions = {
            event.raw_event_id: index for index, event in enumerate(episode.events)
        }
        contexts = []
        target_refs: list[tuple[int, str]] = []

        for claim_index, claim in enumerate(claim_result.items):
            context_lines = []
            positions = []
            for raw_event_id in claim.raw_event_ids:
                if raw_event_id not in events_by_id:
                    raise ValueError(f"unknown raw_event_id: {raw_event_id}")
                positions.append(event_positions[raw_event_id])
                context_lines.append(
                    f"[{raw_event_id}] {events_by_id[raw_event_id].context}"
                )

            if len(positions) != len(set(positions)):
                raise ValueError("raw_event_ids must not repeat")
            if positions != sorted(positions):
                raise ValueError("raw_event_ids must follow Episode order")

            contexts.append("\n".join(context_lines))
            target_refs.extend(
                (claim_index, relation.target)
                for relation in claim.relations
                if relation.target is not None
            )

        _validate_required_action_coverage(episode, claim_result)
        if not claim_result.items:
            return L2Result(items=[])

        target_entities: list[list[str]] = [[] for _ in claim_result.items]
        if target_refs:
            target_result = self._llm_client.generate_json(
                TARGET_ENTITY_SYSTEM_PROMPT,
                build_target_entity_user_prompt([target for _, target in target_refs]),
                TargetEntityResult,
            )
            _validate_target_entity_indexes(target_result, len(target_refs))
            for target_index in target_result.entity_target_indexes:
                claim_index, target = target_refs[target_index]
                if target not in target_entities[claim_index]:
                    target_entities[claim_index].append(target)

        items = []
        for claim, context, selected_targets in zip(
            claim_result.items,
            contexts,
            target_entities,
            strict=True,
        ):
            items.append(
                ExtractionItem(
                    **claim.model_dump(exclude={"entity_mentions"}),
                    entity_mentions=_merge_entity_mentions(claim, selected_targets),
                    context=context,
                )
            )

        return L2Result(items=items)


def _validate_required_action_coverage(
    episode: EpisodeInput,
    result: LLMClaimResult,
) -> None:
    for required_action in episode.required_actions:
        covered = any(
            item.subject == required_action.subject
            and required_action.raw_event_id in item.raw_event_ids
            for item in result.items
        )
        if not covered:
            raise ValueError(
                "uncovered required action: "
                f"{required_action.raw_event_id} ({required_action.action_code})"
            )


def _validate_target_entity_indexes(
    result: TargetEntityResult,
    target_count: int,
) -> None:
    indexes = result.entity_target_indexes
    if len(indexes) != len(set(indexes)):
        raise ValueError("entity_target_indexes must not repeat")
    if indexes != sorted(indexes):
        raise ValueError("entity_target_indexes must follow target order")
    for index in indexes:
        if index < 0 or index >= target_count:
            raise ValueError(f"entity_target_index out of range: {index}")


def _merge_entity_mentions(
    claim: LLMClaimItem,
    selected_targets: list[str],
) -> list[str]:
    source_text = _entity_source_text(claim)
    positioned_mentions: list[tuple[int, str]] = []
    targets = {
        relation.target for relation in claim.relations if relation.target is not None
    }

    seen: set[str] = set()
    base_mentions = []
    for mention in claim.entity_mentions:
        if mention == claim.subject or mention in targets or mention in seen:
            continue
        if mention not in source_text:
            continue
        seen.add(mention)
        base_mentions.append(mention)

    occupied_spans: list[tuple[int, int]] = []
    for mention in sorted(base_mentions, key=len, reverse=True):
        positions = [
            index
            for index in range(len(source_text))
            if source_text.startswith(mention, index)
        ]
        position = next(
            (
                index
                for index in positions
                if all(
                    index + len(mention) <= start or index >= end
                    for start, end in occupied_spans
                )
            ),
            positions[0],
        )
        positioned_mentions.append((position, mention))
        occupied_spans.append((position, position + len(mention)))

    for target in selected_targets:
        if target == claim.subject or target in seen:
            continue
        position = source_text.find(target)
        if position < 0:
            continue
        positioned_mentions.append((position, target))
        seen.add(target)

    positioned_mentions.sort(key=lambda value: value[0])
    return [mention for _, mention in positioned_mentions]


def _entity_source_text(claim: LLMClaimItem) -> str:
    parts = [claim.statement]
    if claim.condition is not None:
        parts.append(claim.condition)
    return "\n".join(parts)
