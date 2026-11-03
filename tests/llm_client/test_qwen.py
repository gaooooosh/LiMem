"""Verify local schema/transport settings and one-call failure behavior."""

from types import SimpleNamespace
import json
import re

import httpx
import openai
import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from limem.llm_client import qwen
from limem.memory_item_extractor.models import LLMClaimResult


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: str


class FakeOpenAI:
    content = '{"value":"ok"}'
    finish_reason = "stop"
    instance = None

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.calls = []
        self.chat = SimpleNamespace(completions=self)
        type(self).instance = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=self.finish_reason,
            message=SimpleNamespace(content=self.content),
        )])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(qwen, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(FakeOpenAI, "content", '{"value":"ok"}')
    monkeypatch.setattr(FakeOpenAI, "finish_reason", "stop")
    return qwen.QwenLLMClient(qwen.QwenSettings(
        base_url="http://127.0.0.1:8001/v1",
        model="qwen", temperature=0.0, max_tokens=4096,
        timeout_seconds=120.0, seed=0,
    ))


def test_local_request_uses_schema_and_non_thinking_parameters(client):
    assert client.generate_json("system", "user", Output) == Output(value="ok")
    transport = FakeOpenAI.instance
    assert transport.init_kwargs == {
        "api_key": "local-qwen", "base_url": "http://127.0.0.1:8001/v1",
        "max_retries": 0, "timeout": 120.0,
    }
    assert transport.calls == [{
        "model": "qwen",
        "messages": [{"role": "system", "content": "system"},
                     {"role": "user", "content": "user"}],
        "temperature": 0.0, "max_tokens": 4096, "seed": 0,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "Output", "schema": Output.model_json_schema(), "strict": True,
        }},
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }]


@pytest.mark.parametrize("content", [None, "", "not json", '{"value":12}',
                                      '{"value":"ok","extra":true}'])
def test_invalid_output_fails_once(client, monkeypatch, content):
    monkeypatch.setattr(FakeOpenAI, "content", content)
    with pytest.raises((ValueError, ValidationError)):
        client.generate_json("system", "user", Output)
    assert len(FakeOpenAI.instance.calls) == 1


def test_even_valid_json_is_rejected_when_output_is_truncated(client, monkeypatch):
    monkeypatch.setattr(FakeOpenAI, "finish_reason", "length")
    with pytest.raises(ValueError, match="max_tokens"):
        client.generate_json("system", "user", Output)
    assert len(FakeOpenAI.instance.calls) == 1


def test_claim_request_allows_complete_strings_under_full_match(client, monkeypatch):
    original_schema = LLMClaimResult.model_json_schema()
    claim = {
        "subject": "用户 usr-sim-01",
        "relations": [{"predicate": "开启充电站导航", "target": "国网充电站·望京科技园"}],
        "statement": "用户 usr-sim-01 开启前往国网充电站·望京科技园的导航。",
        "entity_mentions": ["国网充电站·望京科技园"],
        "raw_event_ids": ["record-000002"],
    }
    monkeypatch.setattr(FakeOpenAI, "content", json.dumps({"items": [claim]}))
    result = client.generate_json("system", "user", LLMClaimResult)
    assert result == LLMClaimResult.model_validate({"items": [claim]})
    call, = FakeOpenAI.instance.calls
    generated = call["response_format"]["json_schema"]["schema"]
    properties = generated["$defs"]["LLMClaimItem"]["properties"]
    relations = generated["$defs"]["Relation"]["properties"]
    fields = [
        (properties["subject"], claim["subject"]),
        (relations["predicate"], claim["relations"][0]["predicate"]),
        (relations["target"]["anyOf"][0], claim["relations"][0]["target"]),
        (properties["entity_mentions"]["items"], claim["entity_mentions"][0]),
        (properties["raw_event_ids"]["items"], claim["raw_event_ids"][0]),
    ]
    for field, text in fields:
        assert re.fullmatch(field["pattern"], text), (field, text)
        assert re.fullmatch(field["pattern"], "") is None
        assert re.fullmatch(field["pattern"], " \t\r\n") is None
        assert re.fullmatch(field["pattern"], " \n完整文本\n ")
    original_properties = original_schema["$defs"]["LLMClaimItem"]["properties"]
    assert properties["statement"] == original_properties["statement"]
    assert properties["condition"] == original_properties["condition"]
    assert generated["$defs"]["LLMClaimItem"]["required"] == (
        original_schema["$defs"]["LLMClaimItem"]["required"]
    )
    assert LLMClaimResult.model_json_schema() == original_schema
    assert call["max_tokens"] == 4096 and call["temperature"] == 0.0
    assert call["seed"] == 0
    assert call["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.parametrize("subject", ["", " \t\r\n"])
def test_claim_response_still_rejects_blank_subject(client, monkeypatch, subject):
    monkeypatch.setattr(FakeOpenAI, "content", json.dumps({"items": [{
        "subject": subject, "relations": [{"predicate": "开启导航"}],
        "statement": "用户开启导航。", "raw_event_ids": ["record-000002"],
    }]}))
    with pytest.raises(ValidationError):
        client.generate_json("system", "user", LLMClaimResult)
    assert len(FakeOpenAI.instance.calls) == 1


def test_qwen_config_load_does_not_require_dotenv(tmp_path, monkeypatch):
    config_path = tmp_path / "qwen.toml"
    config_path.write_text(
        '[qwen]\nbase_url="http://127.0.0.1:8001/v1"\nmodel="qwen"\n'
        'temperature=0.0\nmax_tokens=4096\ntimeout_seconds=120.0\nseed=0\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(qwen, "QWEN_CONFIG_PATH", config_path)
    assert qwen.load_qwen_settings().model == "qwen"
    assert not (tmp_path / ".env").exists()


@pytest.mark.parametrize("status", [200, 503])
def test_real_sdk_serializes_local_request_and_never_retries(monkeypatch, status):
    requests = []
    def respond(request):
        requests.append(request)
        if status == 503:
            return httpx.Response(status, json={"error": {"message": "unavailable"}})
        return httpx.Response(200, json={
            "id": "local-probe", "object": "chat.completion", "created": 0,
            "model": "qwen", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"value":"ok"}'}}],
        })
    def make_sdk(**kwargs):
        return openai.OpenAI(**kwargs, http_client=httpx.Client(
            transport=httpx.MockTransport(respond),
        ))
    monkeypatch.setattr(qwen, "OpenAI", make_sdk)
    client = qwen.QwenLLMClient(qwen.QwenSettings(
        base_url="http://127.0.0.1:8001/v1", model="qwen", temperature=0.0,
        max_tokens=4096, timeout_seconds=120.0, seed=0,
    ))
    try:
        if status == 200:
            assert client.generate_json("system", "user", Output).value == "ok"
        else:
            with pytest.raises(openai.APIStatusError):
                client.generate_json("system", "user", Output)
    finally:
        client._client.close()
    assert len(requests) == 1
    assert str(requests[0].url) == "http://127.0.0.1:8001/v1/chat/completions"
    body = json.loads(requests[0].content)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["response_format"]["json_schema"]["schema"] == Output.model_json_schema()
    assert "thinking" not in body
