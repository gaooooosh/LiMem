"""Load the fixed service and retrieval configuration."""

from pathlib import Path
import tomllib

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SERVICE_CONFIG_PATH = PROJECT_ROOT / "config/service.toml"
ENV_PATH = PROJECT_ROOT / ".env"


class RetrievalSettings(BaseModel):
    """Internal retrieval controls hidden from HTTP callers."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    seed_limit: int = Field(gt=0)
    neighbor_limit: int = Field(gt=0)
    rrf_k: int = Field(ge=0)
    bm25_weight: float = Field(gt=0)
    entity_match_weight: float = Field(ge=0)
    evidence_support_weight: float = Field(ge=0)


class ServiceSettings(BaseModel):
    """Runtime paths, administrator credential, and retrieval controls."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    admin_api_key: SecretStr = Field(min_length=1)
    data_dir: Path
    log_path: Path
    cleanup_interval_seconds: int = Field(gt=0)
    retrieval: RetrievalSettings


class _FileServiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    data_dir: str = Field(min_length=1, pattern=r"\S")
    log_path: str = Field(min_length=1, pattern=r"\S")
    cleanup_interval_seconds: int = Field(gt=0)


class _ConfigFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    service: _FileServiceSettings
    retrieval: RetrievalSettings


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_service_settings() -> ServiceSettings:
    """Read config/service.toml and LIMEM_ADMIN_API_KEY from the root .env."""

    with SERVICE_CONFIG_PATH.open("rb") as config_file:
        settings = _ConfigFile.model_validate(tomllib.load(config_file))
    if not ENV_PATH.is_file():
        raise FileNotFoundError(ENV_PATH)
    env_values = dotenv_values(ENV_PATH)

    return ServiceSettings(
        admin_api_key=env_values["LIMEM_ADMIN_API_KEY"],
        data_dir=_project_path(settings.service.data_dir),
        log_path=_project_path(settings.service.log_path),
        cleanup_interval_seconds=settings.service.cleanup_interval_seconds,
        retrieval=settings.retrieval,
    )
