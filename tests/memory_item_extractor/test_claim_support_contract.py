import json
from pathlib import Path
from types import SimpleNamespace

from limem.episode_builder import EpisodeInput
from limem.memory_item_extractor.claim_generator import ClaimGenerator, IndexedResult
from limem.memory_item_extractor.models import LLMClaimResult

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / 'tests/fixtures'


def test_free_branch_matches_full142_evaluated_prompt(tmp_path):
    requests = []
    class Client:
        settings = SimpleNamespace(model='qwen', temperature=0, seed=0, max_tokens=4096)
        json_string_grammar = False
        def complete_json(self, system, user, model, **kwargs):
            requests.append((system, json.loads(user), model))
            return IndexedResult(items=[]), None
    gen = ClaimGenerator(Client(), tmp_path)
    episode = EpisodeInput(episode_id='session:episode:0', events=[{'raw_event_id':'e','context':'驾驶员请求调整充电上限。'}])
    gen.generate(episode, user_ref='user')
    assert requests[0][0] == (ARTIFACT / 'candidate-system-prompt.txt').read_text(encoding='utf8')
    assert requests[0][1]['events'][0]['context'] == episode.events[0].context
    assert requests[0][2] is IndexedResult


def test_required_branch_retains_original_prompt(tmp_path, monkeypatch):
    seen=[]
    class Client:
        settings=SimpleNamespace(model='qwen',temperature=0,seed=0,max_tokens=4096)
        json_string_grammar=False
    def slotted(client,episode,user,prompt,trace):
        seen.append(prompt)
        return LLMClaimResult(items=[{'subject':'用户 user','statement':'用户 user 调整通风。','relations':[{'predicate':'调整通风'}],'raw_event_ids':['e']}])
    monkeypatch.setattr('limem.memory_item_extractor.required_claims.generate_slotted',slotted)
    gen=ClaimGenerator(Client(),tmp_path)
    episode=EpisodeInput(episode_id='session:episode:0',events=[{'raw_event_id':'e','context':'手动调整通风。','action':'驾驶员：调整通风。'}],required_actions=[{'raw_event_id':'e','subject':'用户 user','actor':'驾驶员','action_code':'seat.setVentilation'}])
    gen.generate(episode,user_ref='user')
    assert seen == [gen.config['generator_system_prompt']]
