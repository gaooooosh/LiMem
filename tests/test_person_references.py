"""Synthetic cases for the service's final person-reference stage."""

from types import SimpleNamespace

import pytest

from limem.memory_item_extractor.contact_references import ContactReferenceResolver
from limem.memory_item_extractor.models import ExtractionItem, L2Result, Relation
from limem.memory_store import MemoryStore
from limem.service.pipeline import MemoryServicePipeline
from test_workflow1_http import ConsolidationClient, Embedding, settings


@pytest.mark.parametrize(("filter_value", "expected"), [
    ("项目伙伴", "林青"),
    ("周末球友", "林雨"),
    ("", None),
])
def test_service_resolves_shared_alias_only_with_current_evidence(tmp_path, filter_value, expected):
    timestamp = "2026-01-01T10:00:00+08:00"
    query = "给小林打电话。"
    dialogue = {"query": query, "request_time": timestamp, "speaker_seat": "seat.row1.left"}
    screen = {"app": "通讯录", "description": f"联系人筛选：{filter_value}" if filter_value else ""}
    raw = {
        "session_id": "synthetic-session", "vehicle_id": "synthetic-car", "user_id": "test-user",
        "records": [{
            "record_id": "record-0", "timestamp": timestamp, "type": "trigger",
            "trigger": {"eventType": "DialogueEvent", "startTime": timestamp, "endTime": None,
                        "source": "车机对话", "actor": "driver", "detail": query,
                        "payload": {"cabin.dialogue": dialogue}, "relatedAction": ""},
            "device_tree": {"cabin": {"dialogue": dialogue, "screen": {"hmi": screen}}},
        }],
    }
    item = ExtractionItem(
        subject="用户 test-user", relations=[Relation(predicate="请求呼叫", target="小林")],
        statement="用户 test-user 请求呼叫小林。", entity_mentions=["小林"],
        raw_event_ids=["record-0"], context=query,
    )
    database = tmp_path / "bank.sqlite"
    store = MemoryStore(database)
    try:
        with store._connection:
            store._connection.executemany(
                """INSERT INTO entity(id, name, aliases_json, types_json, canonical_entity_id)
                   VALUES (?, ?, ?, '["Person"]', ?)""",
                [("person-a", "林青", '["小林", "项目伙伴"]', "person-a"),
                 ("person-b", "林雨", '["小林", "周末球友"]', "person-b")],
            )
    finally:
        store.close()

    client = ConsolidationClient()
    pipeline = MemoryServicePipeline(
        SimpleNamespace(extract=lambda *args, **kwargs: L2Result(items=[item])),
        Embedding(), settings(tmp_path).retrieval, client,
        reference_resolver=ContactReferenceResolver(client, tmp_path / "traces"),
    )
    assert pipeline.write_session(database, SimpleNamespace(source_format="vehicle_session_v1"), raw)[1] == 1
    store = MemoryStore(database)
    try:
        [memory] = store.list_memory_items()
        [relation] = memory.relations
        person = store.get_entity(relation.target_entity_id)
        assert memory.statement == item.statement
        if expected:
            assert relation.target == person.name == expected
        else:
            assert relation.target == "小林"
            assert person.canonical_entity_id is None
            assert person.name.endswith("（身份未确认）")
    finally:
        store.close()
