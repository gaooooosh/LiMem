"""Call the local Qwen vLLM server once and validate structured JSON."""

import json
from pathlib import Path
import tomllib
from typing import TypeVar

from openai import OpenAI
from openai.types.chat import ChatCompletion
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from .config import PROJECT_ROOT

QWEN_CONFIG_PATH = PROJECT_ROOT / "config/qwen.toml"
OutputModel = TypeVar("OutputModel", bound=BaseModel)


class QwenSettings(BaseModel):
    """Local inference settings; no cloud credential is required."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    base_url: HttpUrl
    model: str = Field(min_length=1, pattern=r"\S")
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(gt=0)
    timeout_seconds: float = Field(gt=0)
    seed: int = Field(ge=0)


class _ConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    qwen: QwenSettings


def load_qwen_settings() -> QwenSettings:
    with QWEN_CONFIG_PATH.open("rb") as config_file:
        return _ConfigFile.model_validate(tomllib.load(config_file)).qwen


def _build_qwen_schema(output_model: type[BaseModel], *, json_string_grammar: bool = False) -> dict:
    """Express nonblank text as a complete string for local grammar decoding."""
    schema = output_model.model_json_schema()

    def rewrite(node):
        if isinstance(node, dict):
            if node.get("type") == "string" and node.get("pattern") == r"\S":
                node["pattern"] = r"^[\s\S]*\S[\s\S]*$"
            if json_string_grammar and node.get("type") == "string" and node.get("pattern") in {
                r"^[\s\S]*\S[\s\S]*$", r"^[^\r\n]*[^\s\r\n][^\r\n]*$",
            }:
                node.pop("pattern")
            for value in node.values():
                rewrite(value)
        elif isinstance(node, list):
            for value in node:
                rewrite(value)

    rewrite(schema)
    return schema


class QwenLLMClient:
    """Implement the same generate_json contract as the cloud client."""

    def __init__(self, settings: QwenSettings | None = None,
                 *, json_string_grammar: bool = False) -> None:
        self.settings = settings if settings is not None else load_qwen_settings()
        self.json_string_grammar = json_string_grammar
        self._client = OpenAI(
            api_key="local-qwen",
            base_url=str(self.settings.base_url),
            max_retries=0,
            timeout=self.settings.timeout_seconds,
        )

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[OutputModel],
    ) -> OutputModel:
        result, _ = self.complete_json(system_prompt, user_prompt, output_model)
        return result

    def build_json_request(
        self, system_prompt: str, user_prompt: str, output_model: type[BaseModel],
        *, model: str | None = None, logprobs: bool = False,
    ) -> dict:
        schema = (
            _build_qwen_schema(output_model, json_string_grammar=True)
            if self.json_string_grammar else _build_qwen_schema(output_model)
        )
        request = dict(
            model=self.settings.model if model is None else model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_tokens,
            seed=self.settings.seed,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": output_model.__name__,
                    "schema": schema,
                    "strict": True,
                },
            },
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        if logprobs:
            request.update(logprobs=True, top_logprobs=5)
        return request

    def complete_json(
        self, system_prompt: str, user_prompt: str, output_model: type[OutputModel],
        *, model: str | None = None, logprobs: bool = False,
        trace_dir: Path | None = None,
    ) -> tuple[OutputModel, ChatCompletion]:
        """Expose response log probabilities and preserve each accepted W1 call."""
        request = self.build_json_request(
            system_prompt, user_prompt, output_model, model=model, logprobs=logprobs,
        )
        if trace_dir is not None:
            trace_dir.mkdir(parents=True)
            _save_trace(trace_dir / "request.json", request)
        try:
            completion = self._client.chat.completions.create(**request)
            if trace_dir is not None:
                _save_trace(trace_dir / "response.json", completion.model_dump(mode="json"))
            result = self._validate_completion(completion, output_model)
            return result, completion
        except Exception as error:
            if trace_dir is not None:
                _save_trace(trace_dir / "error.json", {
                    "type": type(error).__name__, "message": str(error),
                })
            raise

    @staticmethod
    def _validate_completion(completion: ChatCompletion,
                             output_model: type[OutputModel]) -> OutputModel:
        if not completion.choices:
            raise ValueError("Qwen returned no choices")
        choice = completion.choices[0]
        if choice.finish_reason == "length":
            raise ValueError("Qwen output reached max_tokens before completion")
        content = choice.message.content
        if not content:
            raise ValueError("Qwen returned empty content")
        return output_model.model_validate_json(content)

    def complete_plain_json(
        self, system_prompt: str, user_prompt: str, output_model: type[OutputModel],
        *, max_tokens: int, trace_dir: Path,
    ) -> tuple[OutputModel, ChatCompletion]:
        """Run the explicitly selected plain-JSON task once; never a schema fallback."""
        request = dict(
            model=self.settings.model,
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}],
            temperature=self.settings.temperature, seed=self.settings.seed,
            max_tokens=max_tokens,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        trace_dir.mkdir(parents=True)
        _save_trace(trace_dir / "request.json", request)
        try:
            completion = self._client.chat.completions.create(**request)
            _save_trace(trace_dir / "response.json", completion.model_dump(mode="json"))
            result = self._validate_completion(completion, output_model)
            return result, completion
        except Exception as error:
            _save_trace(trace_dir / "error.json", {"type": type(error).__name__, "message": str(error)})
            raise


def _save_trace(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
