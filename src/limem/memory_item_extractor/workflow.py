"""The accepted v4 Workflow1: G, target reconstruction/filtering, BERT and identity boundary."""
import json
from pathlib import Path
from uuid import uuid4
from .behavior_admission import BehaviorAdmission
from .claim_generator import ClaimGenerator
from .entity_identity import apply_policy
from .extractor import MemoryItemExtractor,_validate_required_action_coverage
from .models import L2Result,LLMClaimResult
from .target_reconstruction import TargetReconstructor
from .typed_targets import TypedTargetClassifier


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def filter_mentions(result,client,path):
    output=result.model_dump(mode='json')
    candidates=[];positions=[]
    for i,item in enumerate(result.items):
        targets={r.target for r in item.relations if r.target is not None}
        for mention in item.entity_mentions:
            if mention not in targets:
                candidates.append(mention);positions.append((i,mention))
    chosen=TypedTargetClassifier(client,path/'entity-classification').classify(candidates,episode_id='entity-filter').entity_target_indexes if candidates else []
    keep={positions[i] for i in chosen}
    for i,item in enumerate(result.items):
        targets={r.target for r in item.relations if r.target is not None}
        output['items'][i]['entity_mentions']=[m for m in item.entity_mentions if m in targets or (i,m) in keep]
    final=L2Result.model_validate(output)
    save(path/'mention-decisions.json',{'candidates':positions,'chosen_indexes':chosen})
    return final


class Workflow1Extractor:
    def __init__(self,client,trace_root:Path,*,admission=None):
        self.client=client
        self.trace_root=trace_root
        self.admission=admission if admission is not None else BehaviorAdmission()

    def extract(self,episode,*,claim_episode=None,user_ref=None,raw_session):
        directory=self.trace_root/uuid4().hex
        directory.mkdir(parents=True)
        save(directory/'input.json',{'session_id':raw_session['session_id'],'episode':episode.model_dump(mode='json'),
            'claim_episode':(claim_episode if claim_episode is not None else episode).model_dump(mode='json')})
        try:
            core=MemoryItemExtractor(self.client,
                claim_generator=ClaimGenerator(self.client,directory/'draft'),
                target_classifier=TypedTargetClassifier(self.client,directory/'target-classification'),
                target_reconstructor=TargetReconstructor(self.client,directory/'target-reconstruction'))
            result=core.extract(episode,claim_episode=claim_episode,user_ref=user_ref)
            result=filter_mentions(result,self.client,directory/'mentions')
            save(directory/'before-admission.json',result.model_dump(mode='json'))
            if not episode.required_actions:
                decision=self.admission.predict(raw_session)
                save(directory/'admission-decision.json',decision)
                if not decision['nonempty']:result=L2Result(items=[])
            result,decisions=apply_policy(result,raw_session)
            save(directory/'identity-decisions.json',decisions)
            _validate_required_action_coverage(episode,LLMClaimResult.model_validate(
                {'items':[i.model_dump(exclude={'context'}) for i in result.items]}))
            save(directory/'l2result.json',result.model_dump(mode='json'))
            return result
        except Exception as error:
            save(directory/'error.json',{'type':type(error).__name__,'message':str(error)})
            raise
