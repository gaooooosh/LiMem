"""Resolve existing relational contact targets from current anchors and known history."""
from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict
from limem.episode_builder import EpisodeInput
from limem.memory_store.models import Entity, MemoryItem
from limem.raw_data_parser import VehicleRawDataParser
from limem.raw_data_parser.vehicle_protocol import flatten_and_validate
from limem.resolver.base import Resolver
from .models import L2Result
from .evidence import build_evidence_context


@dataclass(frozen=True)
class ReferenceHistory:
    entities: list[Entity]
    memory_items: list[MemoryItem]


@dataclass(frozen=True)
class ContactReferenceInput:
    raw_session: dict
    episode: EpisodeInput
    result: L2Result
    history: ReferenceHistory


class ContactReferenceForm(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['relation', 'named', 'other']


class ReferenceIntent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    intent: Literal['call', 'route', 'other']


class PathDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    decision: Literal['supported', 'insufficient']


FORM_PROMPT='判断target如何描述一个人。relation：用角色、职责或人与事的关系描述，如负责人、主持人、领队、组织这次活动的人；named：直接姓名、昵称，或新认识的某个姓名昵称；other：非人物或无法判断。只输出JSON的kind，relation/named/other。'
INTENT_PROMPT='识别当前请求的主动作，只输出JSON的intent。call：明确请求给某人打电话或联系某人；route：明确请求查询、启动或更改去某地的路线；other：其他动作或主请求无法确定。不要判断项目、负责人等名词是什么实体类型，判断用户要求做什么。提到某人负责项目但实际要求查去现场的路线，应为route。仅取消和其他多个动作混合且无法确定一个目标时为other。'
PATH_PROMPT='本次只解析target_reference这一个关系称谓，query只提供上下文。同一句话可能联系多个人，不能把query中任意联系人当成此目标。目标必须属于该query表达的联系人。判断候选路径的终点是否就是当前请求所指的人或地点。只输出JSON，decision为supported或insufficient。\nsupported：当前来源中的锚点与历史的明确关系连接起来，路径终点确实回答了本请求要找的对象。可以做这类有依据的关系推理，不要求原话再次说出姓名。必须完整核对关系和目标类型。\ninsufficient：路径终点类型不对、只是共现或同场的人、只是相关项目/地点而非请求的目标、关系缺关键连接，或仅共享昵称无法确定。比如请求找一个人时，不能用地点或项目名作答案。当前说新认识同名者时不能套用历史旧人。\n候选路径只是检索结果，不能因连通就判定支持；需要读facts和当前query。不要把导航保留的旧目的地当当前位置。也不要因为做了有明确关系依据的指代消解，就一概认为是在无依据猜测。'


def _save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def _candidates(raw,bank):
    session=VehicleRawDataParser().parse(raw)
    entities={e['id']:e for e in bank['entities'] if e['canonical_entity_id']==e['id']}
    canonical={e['id']:e['canonical_entity_id'] for e in bank['entities'] if e['canonical_entity_id'] in entities}
    excluded={canonical[e['id']] for e in bank['entities'] if e['id'] in canonical and e['external_ref']==raw['user_id']}
    by_surface={}
    for e in bank['entities']:
        if e['id'] not in canonical:continue
        for surface in [e['name'],*e['aliases']]:
            by_surface.setdefault(surface,set()).add(canonical[e['id']])
    facts=[]
    for m in bank['memory_items']:
        if m['status']!='active' or m['condition'] is not None:continue
        ids={m['subject_entity_id'],*(r['target_entity_id'] for r in m['relations'] if r['target_entity_id']),
             *(r['entity_id'] for r in m['entity_refs'])}
        nodes={canonical[i] for i in ids if i in canonical}-excluded
        facts.append((m,nodes))
    pool={};queries={};seen=set();contexts=[]
    for index,r in enumerate(session.raw_events):
        tree=r.raw_payload['device_tree'];leaves=flatten_and_validate(tree,location='current reference anchors')
        for path,value in leaves.items():
            if path not in pool or type(pool[path][0])!=type(value) or pool[path][0]!=value:
                pool[path]=(value,index)
        d=tree.get('cabin',{}).get('dialogue',{});query=d.get('query');key=(query,d.get('request_time'))
        contexts.append({'source_index':index,'location':r.location,
                         'dialogue':{k:d.get(k) for k in ['query','tts','speaker_seat']},
                         'screen':tree.get('cabin',{}).get('screen',{}).get('hmi',{})})
        if not query or key in seen:continue
        seen.add(key);anchors=[]
        for path,value in leaves.items():
            if not value or not isinstance(value,str):continue
            sources={pool[path][1]}
            if path=='sensing.location.poi_name':pass
            elif path.startswith('seat.') and path.endswith('.person_id'):
                occupied=path.removesuffix('.person_id')+'.occupied'
                if leaves.get(occupied) is not True:continue
                sources.add(pool[occupied][1])
            else:continue
            matches=by_surface.get(value,set())-excluded
            if len(matches)!=1:continue
            anchors.append((next(iter(matches)),path,value,sources))
        paths=[];signatures=set()
        def add(anchor,path,value,source_ids,nodes,used):
            endpoint=nodes[-1]
            signature=(path,value,endpoint,tuple(m['id'] for m in used))
            if signature in signatures:return
            signatures.add(signature)
            paths.append({'path_index':len(paths),'anchor':{'field':path,'value':value},
                'anchor_identity':{k:entities[anchor][k] for k in ['name','aliases','types']},
                'endpoint_name':entities[endpoint]['name'],'endpoint_types':entities[endpoint]['types'],
                'endpoint_entity_id':endpoint,
                'entity_chain':[entities[n]['name'] for n in nodes],
                'current_source_indices':sorted(source_ids|{index}),
                'history_memory_ids':[m['id'] for m in used],
                'facts':[m['statement'] for m in used]})
        for anchor,path,value,source_ids in anchors:
            # Identity anchors and two MemoryItem links follow the existing graph boundary.
            frontier=[([anchor],[])]
            add(anchor,path,value,source_ids,[anchor],[])
            for _ in range(2):
                following=[]
                for nodes,used in frontier:
                    for fact,members in facts:
                        if nodes[-1] not in members or fact['id'] in {m['id'] for m in used}:continue
                        for target in sorted(members-set(nodes)):
                            new_nodes=nodes+[target];new_used=used+[fact]
                            add(anchor,path,value,source_ids,new_nodes,new_used)
                            following.append((new_nodes,new_used))
                frontier=following
        queries[f'source_{index}']={'text':query,'paths':paths}
    return queries,contexts


class ContactReferenceResolver(Resolver[ContactReferenceInput,L2Result]):
    """Bind only proven unique contacts; unknown references stay as extracted."""

    def __init__(self,client,trace_root:Path):
        self.client=client
        self.trace_root=trace_root

    @property
    def resolution_description(self)->str:
        return '解析已有关系目标中的联系人称谓；保留全部抽取文本，补充已知Entity引用和当前来源依赖。'

    def resolve(self,value:ContactReferenceInput)->L2Result:
        episode,result=value.episode,value.result
        if episode.required_actions or not result.items:
            return result
        bank={'entities':[e.model_dump(mode='json') for e in value.history.entities],
              'memory_items':[m.model_dump(mode='json') for m in value.history.memory_items]}
        queries,context=_candidates(value.raw_session,bank)
        if not any(v['paths'] for v in queries.values()):
            return result
        directory=self.trace_root/uuid4().hex
        _save(directory/'input.json',{'session_id':value.raw_session['session_id'],
              'queries':queries,'current_sources':context,'l2_result':result.model_dump(mode='json')})
        matches={}
        resolutions=[]
        for key,query in queries.items():
            if not query['paths']:continue
            source_index=int(key.removeprefix('source_'))
            query_id=episode.events[source_index].raw_event_id
            occurrences=[]
            for i,item in enumerate(result.items):
                if query_id not in item.raw_event_ids:continue
                for j,relation in enumerate(item.relations):
                    if relation.target is None or re.sub(r'[\s的]', '', query['text']).count(re.sub(r'[\s的]', '', relation.target))!=1:continue
                    form,_=self.client.complete_json(FORM_PROMPT,json.dumps({'target':relation.target},ensure_ascii=False),
                        ContactReferenceForm,trace_dir=directory/key/f'item_{i}_relation_{j}'/'form')
                    if form.kind=='relation':occurrences.append((i,j,relation.target))
            if not occurrences:continue
            role,_=self.client.complete_json(INTENT_PROMPT,json.dumps({'query':query['text']},ensure_ascii=False),
                                            ReferenceIntent,trace_dir=directory/key/'requested-role')
            if role.intent!='call':continue
            for i,j,target in occurrences:
                accepted=[]
                for path in query['paths']:
                    if 'Person' not in path['endpoint_types']:continue
                    payload={'query':query['text'],'target_reference':target,
                        'current_sources':[c for c in context if c['source_index'] in path['current_source_indices']],
                        'candidate_path':path}
                    verdict,_=self.client.complete_json(PATH_PROMPT,json.dumps(payload,ensure_ascii=False),PathDecision,
                        trace_dir=directory/key/f'item_{i}_relation_{j}'/f"path_{path['path_index']}")
                    if verdict.decision=='supported':accepted.append(path)
                if len({p['endpoint_entity_id'] for p in accepted})!=1:continue
                chosen=min(accepted,key=lambda p:(len(p['current_source_indices']),len(p['history_memory_ids']),p['path_index']))
                reference={'query_source_index':source_index,'query_text':query['text'],'path':chosen}
                resolutions.append({'item_index':i,'relation_index':j,**reference})
                matches.setdefault((i,j),[]).append(reference)
        data=result.model_dump(mode='json')
        positions={e.raw_event_id:i for i,e in enumerate(episode.events)}
        for (i,j),options in matches.items():
            if len({r['path']['endpoint_entity_id'] for r in options})!=1:continue
            reference=min(options,key=lambda r:(len(r['path']['current_source_indices']),
                          len(r['path']['history_memory_ids']),r['query_source_index']))
            path=reference['path']
            required=[episode.events[k].raw_event_id for k in path['current_source_indices']]
            item=data['items'][i]
            item['target_bindings'].append({'relation_index':j,'entity_id':path['endpoint_entity_id'],
                'raw_event_ids':required,'history_memory_ids':path['history_memory_ids']})
            item['raw_event_ids']=sorted(set(item['raw_event_ids'])|set(required),key=positions.__getitem__)
            item['context']=build_evidence_context(episode.events,item['raw_event_ids'])
        output=L2Result.model_validate(data)
        _save(directory/'resolutions.json',resolutions)
        _save(directory/'output.json',output.model_dump(mode='json'))
        return output
