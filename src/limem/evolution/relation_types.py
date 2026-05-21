# -*- coding: utf-8 -*-
"""Shared event-relation vocabulary for memory evolution and recall."""

from __future__ import annotations

REL_SAME_TOPIC = "同一主题"
REL_MEANING_UPDATE = "意义更新"
REL_SHARED_CONTEXT = "共同背景"

RELATION_TYPES = {
    REL_SAME_TOPIC,
    REL_MEANING_UPDATE,
    REL_SHARED_CONTEXT,
}

LEGACY_RELATION_ALIASES = {
    "同一事件": REL_SAME_TOPIC,
    "同一件事": REL_SAME_TOPIC,
    "merge": REL_SAME_TOPIC,
    "更新": REL_MEANING_UPDATE,
    "覆盖": REL_MEANING_UPDATE,
    "修正": REL_MEANING_UPDATE,
    "update": REL_MEANING_UPDATE,
    "补充": REL_SHARED_CONTEXT,
    "导致": REL_SHARED_CONTEXT,
    "延续": REL_SHARED_CONTEXT,
    "因果": REL_SHARED_CONTEXT,
    "触发": REL_SHARED_CONTEXT,
    "前置条件": REL_SHARED_CONTEXT,
    "促成": REL_SHARED_CONTEXT,
    "后续": REL_SHARED_CONTEXT,
    "演进": REL_SHARED_CONTEXT,
    "extend": REL_SHARED_CONTEXT,
    "link": REL_SHARED_CONTEXT,
    "时序相邻": "",
}


def normalize_event_relation_type(value: object) -> str:
    relation_type = str(value or "").strip()
    if relation_type in RELATION_TYPES:
        return relation_type
    return LEGACY_RELATION_ALIASES.get(relation_type, "")


def event_relation_query_types() -> list[str]:
    return sorted(RELATION_TYPES | set(LEGACY_RELATION_ALIASES.keys()))
