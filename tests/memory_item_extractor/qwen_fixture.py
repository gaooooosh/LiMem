import json
import httpx
import openai
from limem.episode_builder import EpisodeInput
from limem.llm_client.qwen import QwenLLMClient, QwenSettings

def episode(required=False):
    return EpisodeInput.model_validate({
        "episode_id": "sample:episode:0", "events": [{
            "raw_event_id": "rec-A", "action": "驾驶员：将音量调至10。",
            "context": "用户 usr-A。调节后音量10；这是完整证据背景。",
        }], "required_actions": ([{
            "raw_event_id": "rec-A", "subject": "用户 usr-A", "actor": "驾驶员",
            "action_code": "media.volume",
        }] if required else []),
    })


def client(monkeypatch, responses):
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        response = responses[len(calls) - 1]
        if response == "unavailable":
            return httpx.Response(503, json={"error": {"message": "unavailable"}})
        return httpx.Response(200, json={
            "id": "test", "created": 0, "object": "chat.completion", "model": "qwen",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(response[0], ensure_ascii=False),
            }, "logprobs": response[1]}],
        })

    monkeypatch.setattr("limem.llm_client.qwen.OpenAI", lambda **kw: openai.OpenAI(
        **kw, http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    ))
    return QwenLLMClient(QwenSettings(
        base_url="http://127.0.0.1:8001/v1", model="qwen", temperature=0.0,
        max_tokens=4096, timeout_seconds=120.0, seed=0,
    )), calls
