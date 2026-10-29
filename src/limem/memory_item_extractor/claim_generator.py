"""Generate v4 claims with fixed subject/source slots; no learned empty checker."""
import json
from pathlib import Path
from typing import Annotated
from uuid import uuid4
from pydantic import BaseModel,ConfigDict,Field
from limem.episode_builder import EpisodeInput
from .extractor import _validate_required_action_coverage
from .models import LLMClaimResult,Relation,_ClaimText,_NonEmptyText

CLAIM_PROMPT_PATH=Path(__file__).with_name('claim_prompt.json')
EXAMPLE_MARKER='以下示例全部来自独立train，表示边界和格式，不是本次事实：\n'
FREE_EXAMPLE_NOTICE='以下示例为独立人工合成的边界对照，不是实际train记录，也不是本次事实：\n'

class IndexedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subject: _NonEmptyText
    relations: list[Relation] = Field(min_length=1)
    statement: _ClaimText
    condition: _ClaimText | None = None
    entity_mentions: list[_NonEmptyText] = Field(default_factory=list)
    source_indices: list[Annotated[int, Field(ge=0)]] = Field(min_length=1)


class IndexedResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    items: list[IndexedItem]


def source_view(episode: EpisodeInput, user_ref: str) -> dict:
    data = episode.model_dump(mode="json")
    ids = {event["raw_event_id"]: index for index, event in enumerate(data["events"])}

    def normalize_refs(text: str) -> str:
        for value, index in ids.items():
            text = text.replace(value, f"source_index={index}")
        return text

    return {
        "episode_id": data["episode_id"], "user_ref": user_ref,
        "events": [{"source_index": index, "action": event.get("action"),
                    "context": normalize_refs(event["context"])}
                   for index, event in enumerate(data["events"])],
        "required_actions": [
            {"source_index": ids[action["raw_event_id"]],
             **{key: value for key, value in action.items() if key != "raw_event_id"}}
            for action in data["required_actions"]
        ],
    }



def materialize(episode: EpisodeInput, result: IndexedResult) -> LLMClaimResult:
    ids = [event.raw_event_id for event in episode.events]
    items = []
    for item in result.items:
        if len(item.source_indices) != len(set(item.source_indices)):
            raise ValueError("Duplicate source indices")
        if any(index < 0 or index >= len(ids) for index in item.source_indices):
            raise ValueError("Unknown source index")
        items.append({
            **item.model_dump(exclude={"source_indices"}),
            "raw_event_ids": [ids[index] for index in sorted(item.source_indices)],
        })
    return LLMClaimResult.model_validate({"items": items})


class ClaimGenerator:
    def __init__(self,client,trace_root:Path):
        self.client=client
        self.trace_root=trace_root
        self.config=json.loads(CLAIM_PROMPT_PATH.read_text(encoding='utf8'))
        self.free_prompt=(self.config['generator_system_prompt'].split(EXAMPLE_MARKER,1)[0]
                          +FREE_EXAMPLE_NOTICE
                          +json.dumps(self.config['free_branch_examples'],ensure_ascii=False)
                          +self.config['free_branch_support_contract'])
        if (client.settings.model != "qwen" or client.settings.temperature != 0
                or client.settings.seed != 0 or client.settings.max_tokens != 4096
                or client.json_string_grammar):
            raise ValueError("v4 requires frozen Qwen decoding settings")


    def generate(self,episode:EpisodeInput,*,user_ref:str|None)->LLMClaimResult:
        trace=self.trace_root/uuid4().hex
        trace.mkdir(parents=True)
        _save(trace/'input.json',{'episode':episode.model_dump(mode='json'),'user_ref':user_ref,'version':'v4'})
        try:
            if not user_ref or not user_ref.strip():
                raise ValueError('v4 requires the validated Session user_id')
            if episode.required_actions:
                from .required_claims import generate_slotted
                output=generate_slotted(self.client,episode,user_ref,self.config['generator_system_prompt'],trace/'generator')
            else:
                result,_=self.client.complete_json(self.free_prompt,
                    json.dumps(source_view(episode,user_ref),ensure_ascii=False),IndexedResult,trace_dir=trace/'generator')
                output=materialize(episode,result)
            _validate_required_action_coverage(episode,output)
            _save(trace/'output.json',output.model_dump(mode='json'))
            return output
        except Exception as error:
            _save(trace/'error.json',{'type':type(error).__name__,'message':str(error)})
            raise


def _save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
