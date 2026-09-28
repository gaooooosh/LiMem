"""Qwen3-1.7B structured requests with the frozen inference settings."""

from datetime import datetime, timezone
import json
import time
from urllib.request import Request, urlopen
from .environment import compact


class QwenClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.calls = []
        self.count = 0

    def post(self, path, body):
        req = Request(
            self.base_url + path,
            data=compact(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=120) as response:
            return json.load(response)

    def call(self, system, user, model, *, purpose, input_limit, output_limit):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        index = self.count
        self.count += 1
        record = dict(
            index=index,
            purpose=purpose,
            started_at=datetime.now(timezone.utc).isoformat(),
            messages=messages,
            input_limit=input_limit,
            max_tokens=output_limit,
            model="Qwen3-1.7B",
            temperature=0,
            seed=0,
            thinking=False,
        )
        started = time.monotonic()
        try:
            tokens = self.post(
                "/tokenize",
                dict(
                    model="Qwen3-1.7B",
                    messages=messages,
                    chat_template_kwargs={"enable_thinking": False},
                ),
            )["count"]
            record["prompt_tokens"] = tokens
            if tokens > input_limit or tokens + output_limit > 16384:
                raise ValueError(f"input_budget_exceeded: {tokens} > {input_limit}")
            body = dict(
                model="Qwen3-1.7B",
                messages=messages,
                temperature=0,
                seed=0,
                max_tokens=output_limit,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": model.__name__,
                        "strict": True,
                        "schema": model.model_json_schema(),
                    },
                },
                chat_template_kwargs={"enable_thinking": False},
            )
            response = self.post("/v1/chat/completions", body)
            choice = response["choices"][0]
            content = choice["message"]["content"]
            record.update(
                content=content,
                finish_reason=choice["finish_reason"],
                usage=response.get("usage"),
                response_model=response["model"],
            )
            if choice["finish_reason"] != "stop":
                raise ValueError(f"output_ended_with_{choice['finish_reason']}")
            result = model.model_validate_json(content)
            record["status"] = "succeeded"
            return result
        except Exception as exc:
            record.update(
                status="failed", error_type=type(exc).__name__, error=str(exc)
            )
            raise
        finally:
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self.calls.append(record)
