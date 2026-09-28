"""Load the fixed repository embedding configuration and API key."""

from pathlib import Path
import tomllib
from typing import Annotated

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StringConstraints

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EMBEDDING_CONFIG_PATH = PROJECT_ROOT / "config/embedding.toml"
ENV_PATH = PROJECT_ROOT / ".env"

_NonEmptyText = Annotated[
    str,
    StringConstraints(min_length=1, pattern=r"\S"),
]


class EmbeddingSettings(BaseModel):
    """Validated runtime settings consumed by EmbeddingClient."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    api_key: SecretStr = Field(min_length=1)
    model: _NonEmptyText
    dimension: int = Field(gt=0)
    batch_size: int = Field(gt=0, le=20)


class _FileEmbeddingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    api_key_env: _NonEmptyText
    model: _NonEmptyText
    dimension: int = Field(gt=0)
    batch_size: int = Field(gt=0, le=20)


class _ConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    embedding: _FileEmbeddingSettings


def load_embedding_settings() -> EmbeddingSettings:
    """Read config/embedding.toml and the configured key from the root .env."""

    with EMBEDDING_CONFIG_PATH.open("rb") as config_file:
        file_settings = _ConfigFile.model_validate(tomllib.load(config_file)).embedding

    if not ENV_PATH.is_file():
        raise FileNotFoundError(ENV_PATH)
    env_values = dotenv_values(ENV_PATH)

    values = file_settings.model_dump(exclude={"api_key_env"})
    return EmbeddingSettings(
        api_key=env_values[file_settings.api_key_env],
        **values,
    )
