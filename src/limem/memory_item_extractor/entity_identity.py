"""Identity admission from the public vehicle schema, never from an answer-key list."""
import re,unicodedata
from limem.raw_data_parser.vehicle_protocol import FIELD_RULES,flatten_and_validate,render_payload_value
from limem.memory_item_extractor.models import L2Result

# Generic physical categories from the vehicle protocol, not unique named identities.
DEVICE_CATEGORIES={'屏幕','中控屏','仪表屏','抬头显示屏','副驾屏','后排屏','车窗','车门','座椅','空调','天窗','方向盘','充电口','阅读灯','氛围灯','音响','车机','车辆','车内','座舱'}
IDENTITY_SUFFIXES=('.title','.app_name','.artist','.album','.contact_name','.destination','.poi_name','.device_name','.media_name','.next_event')

def norm(text):return unicodedata.normalize('NFKC',text).casefold().strip()

def vocabulary():
    words={norm(s) for s in DEVICE_CATEGORIES}
    for path,rule in FIELD_RULES.items():
        words.update([norm(path),norm(rule.label)])
        if rule.kind=='enum':
            for value in rule.values:
                if value:words.update([norm(value),norm(render_payload_value(path,value))])
    return words

VOCABULARY=vocabulary()

def named_values(record,source_ids):
    result=set()
    for source in record['records']:
        if source['record_id'] not in source_ids:continue
        for path,value in flatten_and_validate(source['device_tree'],location='entity identity input').items():
            if path.endswith(IDENTITY_SUFFIXES) and isinstance(value,str) and value:result.add(norm(value))
    return result

def excluded(text,explicit_names):
    value=norm(text)
    if value in explicit_names:return None
    generic=re.sub(r'^(?:所有|全部|全车|四扇|四个|前排|后排|主驾|副驾|左前|右前|左后|右后)+','',value)
    if value in VOCABULARY or generic in DEVICE_CATEGORIES:return 'protocol field, enum or generic device category'
    if re.fullmatch(r'[+-]?\d+(?:\.\d+)?\s*(?:%|℃|°c|级|档|度|分钟|秒|小时|km/h|km|kw|db|lux|ppm|米)?',value):return 'scalar parameter without identity'
    return None

def apply_policy(result,record):
    output=result.model_dump(mode='json');decisions=[]
    for i,item in enumerate(output['items']):
        names=named_values(record,set(item['raw_event_ids']));kept=[]
        for mention in item['entity_mentions']:
            reason=excluded(mention,names)
            decisions.append({'item_index':i,'text':mention,'keep':reason is None,'reason':reason})
            if reason is None:kept.append(mention)
        item['entity_mentions']=kept
    final=L2Result.model_validate(output)
    for a,b in zip(result.items,final.items,strict=True):assert a.model_dump(exclude={'entity_mentions'})==b.model_dump(exclude={'entity_mentions'})
    return final,decisions
