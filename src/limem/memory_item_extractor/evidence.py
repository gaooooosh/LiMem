"""Present the selected source context and action without semantic rewriting."""
from collections.abc import Sequence
from limem.episode_builder.models import EpisodeEventInput


def build_evidence_context(events: Sequence[EpisodeEventInput], raw_event_ids: list[str]) -> str:
    by_id = {event.raw_event_id: event for event in events}
    parts = []
    for raw_event_id in raw_event_ids:
        event = by_id[raw_event_id]
        text = f'[{raw_event_id}] {event.context}'
        if event.action is not None:
            text += f'\nAction: {event.action}'
        parts.append(text)
    return '\n'.join(parts)
