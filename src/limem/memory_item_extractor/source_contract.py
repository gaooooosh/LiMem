"""Explicit human touch submission confirms configuration, not physical effect.

This is the user-confirmed vehicle-source convention (2026-11-01).
Unknown operation semantics remain with the ordinary claim task.
"""
import re
from .source_actions import action_fields
from limem.raw_data_parser.vehicle_protocol import flatten_and_validate



def reviewed_nature(record):
    trigger = record.get('trigger') or {}
    if trigger.get('actor') not in {'driver','passenger','rear'}:
        return None
    code = trigger.get('relatedAction')
    payload = flatten_and_validate(trigger.get('payload',{}),location='source contract')
    paths = action_fields(code,payload)
    if paths is None:
        return None
    text = trigger.get('detail') or ''
    if any(word in text for word in ['失败','尚未','未操作','未保存','仅预览']):
        return None
    if code == 'navigation.start' and payload.get('navigation.active') is not True:
        return None
    if code == 'media.pause' and payload.get('cabin.media.play_status') != 'pause':
        return None
    if '完成触控提交' in text or (code == 'media.pause' and '点击' in text):
        return 'configured'
    if code == 'charging.setScheduledDeparture' and ('保存预约出发设置' in text or '设置已保存' in text):
        return 'saved'
    if code == 'navigation.start' and ('已启动' in text or '并启动所选路线' in text):
        return 'started' if payload.get('navigation.active') is True else None
    if code == 'seat.setVentilation':
        found = re.search(r'通风(?:已)?(?:开启到|开启至|调至|切换至|启用|开启)([0-3])档',text)
        if found and len(paths) == 1 and payload[paths[0]] == int(found.group(1)):
            return 'configured'
    return None
