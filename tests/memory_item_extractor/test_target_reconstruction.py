"""Check field preservation, frozen per-relation inputs, and no retry on failure."""
from types import SimpleNamespace

import openai
import pytest
from pydantic import ValidationError

from limem.memory_item_extractor.extractor import MemoryItemExtractor
from limem.memory_item_extractor.factory import create_workflow1_extractor
from limem.memory_item_extractor.models import LLMClaimResult, TargetEntityResult
from limem.memory_item_extractor.target_reconstruction import TargetReconstructor
from qwen_fixture import client, episode


def claims():
    return LLMClaimResult.model_validate({"items": [{
        "subject": "用户 usr-A", "statement": "用户 usr-A 将媒体音量从5调至10，将空调目标温度调至24℃。",
        "relations": [{"predicate": "将媒体音量从5调至10", "target": None},
                      {"predicate": "将空调目标温度调至24℃", "target": None}],
        "raw_event_ids": ["rec-A"], "entity_mentions": [],
    }]})


def test_only_targets_change_and_later_calls_read_frozen_relations(monkeypatch, tmp_path):
    local, calls = client(monkeypatch, [({"target": "10"}, None), ({"target": "24℃"}, None)])
    original = claims()
    before = original.model_dump(mode="json")
    try:
        output = TargetReconstructor(local, tmp_path).reconstruct(original, episode_id="sample:episode:0")
        assert original.model_dump(mode="json") == before
        actual = output.model_dump(mode="json")
        before["items"][0]["relations"][0]["target"] = "10"
        before["items"][0]["relations"][1]["target"] = "24℃"
        assert actual == before
        import json
        assert json.loads(calls[1]["messages"][1]["content"])["relations"][0]["target"] is None
        assert all("response_format" not in c and c["max_tokens"] == 256 for c in calls)
    finally:
        local._client.close()


def test_existing_targets_do_not_call_model(monkeypatch, tmp_path):
    local, calls = client(monkeypatch, [])
    original = claims()
    for relation in original.items[0].relations:
        relation.target = "已有目标"
    try:
        output = TargetReconstructor(local, tmp_path).reconstruct(original, episode_id="sample")
        assert output == original and not calls
    finally:
        local._client.close()


@pytest.mark.parametrize("response,error", [
    (({"target": "99"}, None), ValueError),
    (({"target": 10}, None), ValidationError),
    (({"target": "10", "issues": []}, None), ValidationError),
    ("unavailable", openai.APIStatusError),
])
def test_invalid_response_stops_without_retry_or_draft_fallback(monkeypatch, tmp_path, response, error):
    local, calls = client(monkeypatch, [response])
    original = claims()
    try:
        with pytest.raises(error):
            TargetReconstructor(local, tmp_path).reconstruct(original, episode_id="sample")
        assert len(calls) == 1 and original.items[0].relations[0].target is None
        trace, = tmp_path.iterdir()
        assert (trace / "error.json").exists() and not (trace / "output.json").exists()
    finally:
        local._client.close()


def test_legal_empty_target_is_retained(monkeypatch, tmp_path):
    local, calls = client(monkeypatch, [({"target": None}, None), ({"target": None}, None)])
    try:
        original = claims()
        assert TargetReconstructor(local, tmp_path).reconstruct(original, episode_id="sample") == original
        assert len(calls) == 2
    finally:
        local._client.close()


def test_new_target_reaches_classifier_and_context_is_unchanged(monkeypatch, tmp_path):
    local, calls = client(monkeypatch, [({"target": "10"}, None)])
    draft = claims()
    draft.items[0].relations = draft.items[0].relations[:1]
    try:
        class Generator:
            def generate(self, *_args, **_kwargs): return draft
        class Classifier:
            def classify(self, targets, *, episode_id):
                assert targets == ["10"]
                return TargetEntityResult(entity_target_indexes=[])
        result = MemoryItemExtractor(local, claim_generator=Generator(), target_classifier=Classifier(),
            target_reconstructor=TargetReconstructor(local, tmp_path)).extract(episode(), user_ref="usr-A")
        assert result.items[0].relations[0].target == "10"
        assert result.items[0].raw_event_ids == ["rec-A"]
        assert result.items[0].context == "[rec-A] " + episode().events[0].context
        assert len(calls) == 1
    finally:
        local._client.close()


def test_factory_always_returns_complete_v3(tmp_path):
    admission = object()
    result = create_workflow1_extractor(SimpleNamespace(), trace_root=tmp_path, admission=admission)
    assert result.admission is admission
    assert result.__class__.__name__ == "Workflow1Extractor"
