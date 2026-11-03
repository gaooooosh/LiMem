"""Generate confirmed source operations and model-supported additional claims."""
import json
from uuid import uuid4

from limem.memory_item_extractor.claim_generator import (
    ClaimGenerator, IndexedResult, source_view, materialize, EXAMPLE_MARKER,
)
from limem.memory_item_extractor.required_claims import slot_schema, slot_name, slotted_prompt
from limem.memory_item_extractor.models import LLMClaimItem, LLMClaimResult, bind_required_subject
from limem.memory_item_extractor.extractor import _validate_required_action_coverage
from .source_actions import compile_session
from .source_prompt import SUPPLEMENT, PROMPT


class SourceClaimGenerator(ClaimGenerator):
    def __init__(self, client, trace_root, *, raw_session):
        super().__init__(client, trace_root)
        self.raw_session = raw_session

    def generate(self, episode, *, user_ref):
        if not episode.required_actions:
            return super().generate(episode, user_ref=user_ref)
        subjects = {action.raw_event_id: action.subject for action in episode.required_actions}
        compiled = compile_session(self.raw_session, subjects)
        if not compiled:
            return super().generate(episode, user_ref=user_ref)

        remaining = [action for action in episode.required_actions if action.raw_event_id not in compiled]
        generation_episode = episode.model_copy(update={'required_actions': remaining})
        view = source_view(generation_episode, user_ref)
        positions = {event.raw_event_id: i for i, event in enumerate(episode.events)}
        view['already_expressed'] = [
            {**{k: v for k, v in item.items() if k != 'raw_event_ids'},
             'source_indices': [positions[rid] for rid in item['raw_event_ids']]}
            for action in episode.required_actions for item in compiled.get(action.raw_event_id, [])]
        trace = self.trace_root / uuid4().hex
        trace.mkdir(parents=True)
        (trace / 'source-division.json').write_text(json.dumps(dict(
            episode_id=episode.episode_id, compiled=compiled,
            remaining_required=[action.model_dump(mode='json') for action in remaining]),
            ensure_ascii=False, indent=2), encoding='utf8')
        by_source = {}
        if remaining:
            prompt = slotted_prompt(self.config['generator_system_prompt'])
            prefix, encoded = prompt.split(EXAMPLE_MARKER, 1)
            answer, _ = self.client.complete_json(
                prefix + SUPPLEMENT + EXAMPLE_MARKER + encoded,
                json.dumps(view, ensure_ascii=False), slot_schema(view), trace_dir=trace / 'generator')
            for action in view['required_actions']:
                index = action['source_index']
                indexed = [dict(subject=action['subject'],
                                **item.model_dump(exclude={'support_indices'}),
                                source_indices=[index, *item.support_indices])
                           for item in getattr(answer.required_claims, slot_name(index, action))]
                by_source[episode.events[index].raw_event_id] = materialize(
                    episode, IndexedResult.model_validate({'items': indexed})).items
            extra = materialize(episode, IndexedResult(items=answer.additional_items)).items
        else:
            answer, _ = self.client.complete_json(
                PROMPT + SUPPLEMENT, json.dumps(view, ensure_ascii=False),
                IndexedResult, trace_dir=trace / 'generator')
            extra = materialize(episode, answer).items

        output = []
        for action in episode.required_actions:
            if action.raw_event_id in compiled:
                claims = compiled[action.raw_event_id]
            else:
                claims = [item.model_dump(mode='json') for item in by_source[action.raw_event_id]]
            output.extend(bind_required_subject(LLMClaimItem.model_validate(item), action).model_dump(mode='json')
                          for item in claims)
        output.extend(item.model_dump(mode='json') for item in extra)
        result = LLMClaimResult.model_validate({'items': output})
        _validate_required_action_coverage(episode, result)
        (trace / 'output.json').write_text(result.model_dump_json(indent=2), encoding='utf8')
        return result
