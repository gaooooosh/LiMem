"""Load the fixed repository LLM configuration and API key."""

from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Annotated, Literal

from dotenv import dotenv_values
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    StringConstraints,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
LLM_CONFIG_PATH = PROJECT_ROOT / "config/llm.toml"
ENV_PATH = PROJECT_ROOT / ".env"

_NonEmptyText = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
]


class ThinkingSettings(BaseModel):
    """DeepSeek thinking-mode setting passed through OpenAI extra_body."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    type: Literal["disabled"]


class ExtraBodySettings(BaseModel):
    """Extra DeepSeek request fields used by the current model."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    thinking: ThinkingSettings


class ChatTemplateSettings(BaseModel):
    """Thinking switch for a model template that supports enable_thinking."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    enable_thinking: bool


class ChatTemplateExtraBodySettings(BaseModel):
    """Explicit chat-template options supported by SGLang and vLLM."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    chat_template_kwargs: ChatTemplateSettings


class LLMSettings(BaseModel):
    """Validated runtime settings consumed by LLMClient."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    api_key: SecretStr = Field(min_length=1)
    base_url: HttpUrl
    model: _NonEmptyText
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(gt=0)
    extra_body: ExtraBodySettings | ChatTemplateExtraBodySettings | None = None


class _FileLLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    api_key_env: _NonEmptyText
    base_url: HttpUrl
    model: _NonEmptyText
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(gt=0)
    extra_body: ExtraBodySettings | ChatTemplateExtraBodySettings | None = None


class _ConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    llm: _FileLLMSettings


def load_llm_settings() -> LLMSettings:
    """Read config/llm.toml and the configured key from the root .env."""

    with LLM_CONFIG_PATH.open("rb") as config_file:
        file_settings = _ConfigFile.model_validate(tomllib.load(config_file)).llm

    if not ENV_PATH.is_file():
        raise FileNotFoundError(ENV_PATH)
    env_values = dotenv_values(ENV_PATH)

    values = file_settings.model_dump(exclude={"api_key_env"})
    return LLMSettings(
        api_key=env_values[file_settings.api_key_env],
        **values,
    )
