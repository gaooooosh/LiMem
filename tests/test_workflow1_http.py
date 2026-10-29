"""Synthetic cases for the real authenticated HTTP -> v4 -> store path."""
import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from limem.llm_client.qwen import QwenLLMClient, QwenSettings
from limem.service.app import create_app
from limem.service.config import ServiceSettings, RetrievalSettings


def raw_session(required):
    timestamp = "2026-01-01T10:00:00+08:00"
    return {"session_id": "synthetic-session", "vehicle_id": "synthetic-car", "user_id": "test-user",
            "records": [{"record_id": "record-0", "timestamp": timestamp, "type": "trigger",
                         "trigger": {"eventType": "MediaEvent", "startTime": timestamp, "endTime": None,
                                     "source": "媒体播放", "actor": "driver",
                                     "detail": "驾驶员将媒体音量调至10。", "payload": {"cabin.media": {"volume": 10}},
                                     "relatedAction": "media.setVolume" if required else ""},
                         "device_tree": {"cabin": {"media": {"volume": 10}}}}]}


def settings(tmp_path):
    return ServiceSettings(admin_api_key=SecretStr("synthetic-admin"), data_dir=tmp_path / "data",
                           log_path=tmp_path / "service.log", cleanup_interval_seconds=3600,
                           retrieval=RetrievalSettings(seed_limit=5, neighbor_limit=5, rrf_k=60,
                               bm25_weight=3.0, entity_match_weight=0.1, evidence_support_weight=0.25))


class Embedding:
    settings = SimpleNamespace(model="synthetic", dimension=2)
    def embed_documents(self, texts): return [[1.0, 1.0] for _ in texts]
    def embed_query(self, text): return [1.0, 1.0]


class ConsolidationClient:
    def generate_json(self, *args, **kwargs):
        raise AssertionError("Workflow1 must not call the configurable consolidation LLM")


@pytest.mark.parametrize("required,admit,created", [(True, True, 1), (False, True, 1), (False, False, 0)])
def test_http_write_uses_v4_and_preserves_branch_contract(tmp_path, monkeypatch, required, admit, created):
    class Admission:
        def predict(self, record):
            assert not required
            assert record == raw_session(required)
            return {"nonempty": admit, "probability_nonempty": float(admit)}
    monkeypatch.setattr("limem.memory_item_extractor.workflow.BehaviorAdmission", Admission)
    calls = []
    def respond(request):
        body = json.loads(request.content); calls.append(body)
        name = body.get("response_format", {}).get("json_schema", {}).get("name")
        claim = {"relations": [{"predicate": "将媒体音量调至10", "target": None}],
                 "statement": "用户 test-user 将媒体音量调至10。", "condition": None, "entity_mentions": []}
        if name == "SlottedResult":
            output = {"required_claims": {"source_0__media_setVolume": [{**claim, "support_indices": []}]},
                      "additional_items": []}
        elif name == "IndexedResult":
            output = {"items": [{**claim, "subject": "用户 test-user", "source_indices": [0]}]}
        elif name == "TargetKind": output = {"kind": "parameter"}
        elif name is None:
            assert body["max_tokens"] == 256
            output = {"target": "10"}
        else: raise AssertionError(name)
        return httpx.Response(200, json={"id": "synthetic", "created": 0, "object": "chat.completion",
            "model": "qwen", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(output, ensure_ascii=False)}}]})
    monkeypatch.setattr("limem.llm_client.qwen.OpenAI", lambda **kw: openai.OpenAI(
        **kw, http_client=httpx.Client(transport=httpx.MockTransport(respond))))
    local = QwenLLMClient(QwenSettings(base_url="http://127.0.0.1:8001/v1", model="qwen",
        temperature=0, max_tokens=4096, timeout_seconds=120, seed=0))
    try:
        app = create_app(settings(tmp_path), llm_client=ConsolidationClient(),
                         embedding_client=Embedding(), qwen_client=local)
        with TestClient(app) as client:
            issued = client.post("/v1/admin/api-keys", headers={"X-API-Key": "synthetic-admin"},
                                 json={"name": "synthetic"})
            assert issued.status_code == 201, issued.text
            headers = {"X-API-Key": issued.json()["api_key"]}
            bank = client.post("/v1/limem", headers=headers,
                               json={"name": "synthetic", "source_format": "vehicle_session_v1"})
            assert bank.status_code == 201, bank.text
            result = client.post(f"/v1/limem/{bank.json()['memory_bank_id']}/sessions", headers=headers,
                                 json=raw_session(required))
            assert result.status_code == 200, result.text
            assert result.json()["memory_items_created"] == created
            assert len(calls) == 3
            assert all(c['model'] == 'qwen' and c['temperature'] == 0 and c['seed'] == 0 for c in calls)
            assert all(c['chat_template_kwargs']['enable_thinking'] is False for c in calls)
            trace, = (settings(tmp_path).data_dir / "debug/workflow1-v4").iterdir()
            assert (trace / "l2result.json").is_file()
            assert (trace / "admission-decision.json").exists() is (not required)
    finally:
        local._client.close()
