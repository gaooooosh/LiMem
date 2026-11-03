"""Express an operation only within the confirmed vehicle-source contract.

This function does not infer success from an action code or a snapshot. The
caller supplies a reviewed source nature. Unsupported inputs remain outside
this component's scope; exceptions are never converted to a fallback result.
"""
from limem.raw_data_parser.vehicle_protocol import FIELD_RULES, flatten_and_validate, render_payload_value
from limem.raw_data_parser.vehicle import VehicleRawDataParser


def field_label(path):
    label = FIELD_RULES[path].label
    if path.startswith('seat.') and path.endswith('.ventilation_level'):
        seat = path.rsplit('.', 1)[0]
        position = VehicleRawDataParser._render_dialogue({'cabin':{'dialogue':{'speaker_seat':seat}}})
        return position.removeprefix('说话成员：') + '座椅通风'
    return label


def action_fields(code, payload):
    if code == 'climate.setTemperature':
        paths = [p for p in payload if p.startswith('hvac.zone.') and p.endswith('.target_temp_c')]
    elif code == 'seat.setVentilation':
        paths = [p for p in payload if p.startswith('seat.') and p.endswith('.ventilation_level')]
    else:
        choices = {
            'media.setVolume':['cabin.media.volume'],
            'charging.setScheduledDeparture':['power.scheduled_departure.time','power.scheduled_departure.enabled'],
            'navigation.start':['navigation.destination'],
            'media.pause':['cabin.media.title','cabin.media.play_status'],
        }
        if code not in choices:
            return None
        paths = [p for p in choices[code] if p in payload]
    if not paths or any(payload[p] is None or payload[p] == '' for p in paths):
        return None
    return paths


def compose_action(record, subject, *, nature, previous):
    if nature is None:
        return None
    if nature not in {'configured','saved','started'}:
        raise ValueError('unrecognized reviewed action nature')
    trigger = record['trigger']
    code = trigger['relatedAction']
    payload = flatten_and_validate(trigger['payload'], location='reviewed action payload')
    paths = action_fields(code, payload)
    if paths is None:
        return None
    relations, clauses, observations, support = [], [], [], []
    for path in paths:
        label = field_label(path)
        value = render_payload_value(path, payload[path])
        if path == 'power.scheduled_departure.enabled':
            value = '开启' if payload[path] else '关闭'
        if code == 'media.pause':
            if path == 'cabin.media.play_status':
                continue
            predicate = '暂停播放'
        elif code == 'navigation.start':
            predicate = '启动导航前往'
        elif code == 'charging.setScheduledDeparture':
            predicate = ('保存预约出发时间为' if path.endswith('.time') else '将预约出发开关保存为')
        else:
            predicate = '将'+label+'设为'
        relations.append(dict(predicate=predicate,target=value))
        clauses.append(predicate+value)
        old = previous.get(path)
        if old and old['value'] is not None and old['value'] != '' and old['value'] != payload[path]:
            observations.append(label+'此前观测为'+render_payload_value(path,old['value']))
            support.append(old['raw_event_id'])
    if code == 'media.pause' and not relations:
        relations.append(dict(predicate='暂停播放',target=None))
        clauses.append(relations[0]['predicate'])
    statement = subject+' '+'；'.join(clauses)
    if observations:
        statement += '（'+'；'.join(observations)+'）'
    ids = list(dict.fromkeys([*support,record['record_id']]))
    return [dict(subject=subject,relations=relations,statement=statement+'。',condition=None,
                 entity_mentions=[],raw_event_ids=ids)]


def compile_session(raw, required_subjects):
    from .source_contract import reviewed_nature
    positions={record['record_id']:i for i,record in enumerate(raw['records'])}
    pool, compiled = {}, {}
    for record in raw['records']:
        rid=record['record_id']
        if rid in required_subjects:
            nature=reviewed_nature(record)
            if nature is not None:
                claims=compose_action(record,required_subjects[rid],nature=nature,previous=pool)
                if claims is not None:
                    for claim in claims:
                        claim['raw_event_ids']=sorted(claim['raw_event_ids'],key=positions.__getitem__)
                    compiled[rid]=claims
        for path,value in flatten_and_validate(record['device_tree'],location='source observation').items():
            pool[path]={'value':value,'raw_event_id':rid}
    return compiled
