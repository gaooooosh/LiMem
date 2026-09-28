"""Build the vehicle claim-input view; retain the full Episode for Evidence."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import TYPE_CHECKING

from pydantic import JsonValue

from .models import SessionInput
from .vehicle_protocol import (
    FIELD_RULES, flatten_and_validate, render_leaf, render_payload_value,
)

if TYPE_CHECKING:
    from limem.episode_builder import EpisodeInput

# Keep literal utterances and object identity even when their values are unchanged.
SEMANTIC_PATHS = (
    'cabin.dialogue.query', 'cabin.dialogue.tts', 'cabin.dialogue.speaker_seat',
    'cabin.dialogue.request_time', 'cabin.dialogue.domain',
    'cabin.screen.hmi.description', 'cabin.screen.hmi.last_touch',
    'cabin.screen.hmi.active_screen', 'cabin.screen.hmi.app',
    'cabin.screen.hmi.media_name', 'cabin.screen.hmi.play_status',
    'cabin.media.app_name', 'cabin.media.title', 'cabin.media.artist',
    'cabin.media.category', 'cabin.media.play_status',
    'connectivity.bluetooth.device_name', 'connectivity.phone.contact_name',
    'connectivity.phone.number_masked', 'navigation.destination',
)


_LABEL_COUNTS = Counter(rule.label for rule in FIELD_RULES.values())


def _label(path: str) -> str:
    label = FIELD_RULES[path].label
    return label if _LABEL_COUNTS[label] == 1 else f'{label}（{path}）'


def _observation(path: str, value: JsonValue) -> str:
    rendered = render_leaf(path, value)
    assert rendered is not None
    if _LABEL_COUNTS[FIELD_RULES[path].label] > 1:
        return f'{_label(path)}：{render_payload_value(path, value)}'
    return rendered


def build_vehicle_claim_episode(
    session: SessionInput, episode: EpisodeInput,
) -> EpisodeInput:
    """Project context only, using a pool local to this already parsed Session."""
    pool = {}
    events = []
    for raw, event in zip(session.raw_events, episode.events, strict=True):
        if raw.raw_event_id != event.raw_event_id:
            raise ValueError('claim projection requires matching Session and Episode events')
        record = raw.raw_payload
        leaves = flatten_and_validate(record['device_tree'], location='state projection')
        differences = []
        for path, value in leaves.items():
            old = pool.get(path)
            if old is not None and (type(old['value']) is not type(value) or old['value'] != value):
                differences.append(
                    f"{_label(path)}：{render_payload_value(path, old['value'])} → "
                    f"{render_payload_value(path, value)}"
                    f"（前值来源：{old['raw_event_id']}，{old['time']}）"
                )
            elif old is None and pool:
                differences.append(
                    f'{_label(path)}：首次观测 {render_payload_value(path, value)}'
                )
        semantics = [_observation(p, leaves[p]) for p in SEMANTIC_PATHS
                     if p in leaves and leaves[p] is not None and leaves[p] != '']
        context = (
            f"{record['timestamp']}，用户 {raw.user_ref}。"
            f"{raw.event.rstrip('。')}。"
            '以下仅为观测状态，不单独证明用户动作；前值注明来源。'
            f"当前原话及对象信息：{'；'.join(semantics) or '无'}。"
            f"观测变化：{'；'.join(differences) or '无'}。"
        )
        events.append(event.model_copy(update={'context': context}))
        for path, value in leaves.items():
            pool[path] = {'value': deepcopy(value), 'raw_event_id': raw.raw_event_id,
                          'time': record['timestamp']}
    return episode.model_copy(update={'events': events})
