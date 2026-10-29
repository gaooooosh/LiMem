"""Compose the existing write and retrieval components for one memory bank."""

from math import isfinite
from pathlib import Path
from threading import Event
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, JsonValue, ValidationError

from limem.consolidator import (
    ConsolidationError,
    ConsolidationResult,
    RepeatedBehaviorConsolidator,
)
from limem.episode_builder import EpisodeBuilder
from limem.memory_item_extractor import MemoryItemExtractor
from limem.memory_store import (
    EmbeddingValidationError,
    MemoryStore,
    PendingConsolidationSubject,
)
from limem.raw_data_parser.vehicle_state_pool import build_vehicle_claim_episode
from limem.raw_data_parser import (
    RawDataParseError,
    SessionInput,
    VehicleRawDataParser,
)
from limem.resolver import QueryResolver
from limem.retrieval import (
    RetrievalRequest,
    RetrievalResultFormatter,
    Retriever,
)

from .config import RetrievalSettings
from .models import BankRecord, ConsolidatorType, RetrieveRequest

OutputModel = TypeVar("OutputModel", bound=BaseModel)


class _JSONClient(Protocol):
    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[OutputModel],
    ) -> OutputModel: ...


class _EmbeddingSettings(Protocol):
    model: str
    dimension: int


class _EmbeddingClient(Protocol):
    settings: _EmbeddingSettings

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

WriteStage = Literal[
    "session_validation",
    "episode_build",
    "memory_extraction",
    "embedding",
    "persistence",
]


class InvalidSessionFormatError(Exception):
    """The submitted JSON does not satisfy the selected source format."""

    def __init__(self, details: list[dict[str, str]]) -> None:
        super().__init__("invalid session format")
        self.details = details


class DuplicateSessionError(Exception):
    """The same Session ID already completed in this memory bank."""


class WriteStageError(Exception):
    """One named write stage failed before the Session could complete."""

    def __init__(self, stage: WriteStage, cause: Exception) -> None:
        super().__init__(str(cause))
        self.stage = stage
        self.cause = cause


class RetrievalStageError(Exception):
    """The deterministic retrieval pipeline failed."""

    def __init__(
        self, stage: Literal["embedding", "retrieval"], cause: Exception
    ) -> None:
        super().__init__(str(cause))
        self.stage = stage
        self.cause = cause


class ConsolidationStageError(Exception):
    """One explicit consolidation stage failed."""

    def __init__(self, stage: str, cause: Exception) -> None:
        super().__init__(str(cause))
        self.stage = stage
        self.cause = cause


class ConsolidationCancelled(Exception):
    """The caller cancelled consolidation before persistence began."""


class _EmbeddingFailure(Exception):
    pass


class _StageEmbeddingClient:
    def __init__(self, client: _EmbeddingClient) -> None:
        self._client = client
        self.settings = client.settings

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            return self._client.embed_documents(texts)
        except ConsolidationCancelled:
            raise
        except Exception as exc:
            raise _EmbeddingFailure(str(exc)) from exc

    def embed_query(self, text: str) -> list[float]:
        try:
            vector = self._client.embed_query(text)
            if len(vector) != self.settings.dimension:
                raise ValueError("query embedding dimension does not match settings")
            if any(not isfinite(value) for value in vector):
                raise ValueError("query embedding must contain finite numbers")
            if not any(vector):
                raise ValueError("query embedding cannot be zero")
            return vector
        except Exception as exc:
            raise _EmbeddingFailure(str(exc)) from exc


class _CancellationAwareLLMClient:
    def __init__(self, client: _JSONClient, cancel_event: Event) -> None:
        self._client = client
        self._cancel_event = cancel_event

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[OutputModel],
    ) -> OutputModel:
        self._require_active()
        result = self._client.generate_json(system_prompt, user_prompt, output_model)
        self._require_active()
        return result

    def _require_active(self) -> None:
        if self._cancel_event.is_set():
            raise ConsolidationCancelled("consolidation was cancelled")


class _CancellationAwareEmbeddingClient:
    def __init__(self, client: _EmbeddingClient, cancel_event: Event) -> None:
        self._client = client
        self._cancel_event = cancel_event
        self.settings = client.settings

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self._require_active()
        vectors = self._client.embed_documents(texts)
        self._require_active()
        return vectors

    def _require_active(self) -> None:
        if self._cancel_event.is_set():
            raise ConsolidationCancelled("consolidation was cancelled")


class MemoryServicePipeline:
    """Run complete Session writes and compact Agent retrievals."""

    def __init__(
        self,
        extractor: MemoryItemExtractor,
        embedding_client: _EmbeddingClient,
        retrieval_settings: RetrievalSettings,
        llm_client: _JSONClient,
    ) -> None:
        self._parser = VehicleRawDataParser()
        self._episode_builder = EpisodeBuilder()
        self._extractor = extractor
        self._embedding_client = embedding_client
        self._retrieval_settings = retrieval_settings
        self._llm_client = llm_client

    def validate_session(self, raw_session: JsonValue | None) -> SessionInput:
        """Strictly parse one Session without calling later pipeline stages."""

        if not isinstance(raw_session, dict):
            raise InvalidSessionFormatError(
                [
                    {
                        "path": "session",
                        "message": "Session must be a JSON object",
                        "type": "object_type",
                    }
                ]
            )
        try:
            return self._parser.parse(raw_session)
        except (ValidationError, ValueError) as exc:
            raise InvalidSessionFormatError(_validation_details(exc)) from exc

    def write_session(
        self,
        database_path: str | Path,
        bank: BankRecord,
        raw_session: JsonValue | None,
    ) -> tuple[str, int]:
        if bank.source_format != "vehicle_session_v1":
            raise ValueError(f"unsupported source format: {bank.source_format}")
        session = self.validate_session(raw_session)

        store = MemoryStore(database_path)
        try:
            try:
                store.get_source_session(session.session_ref)
            except KeyError:
                pass
            else:
                raise DuplicateSessionError(session.session_ref)

            try:
                [episode] = self._episode_builder.build(session)
            except Exception as exc:
                raise WriteStageError("episode_build", exc) from exc

            try:
                result = self._extractor.extract(
                    episode, claim_episode=build_vehicle_claim_episode(session, episode),
                    user_ref=raw_session["user_id"], raw_session=raw_session,
                )
            except Exception as exc:
                raise WriteStageError("memory_extraction", exc) from exc

            existing_ids = {item.id for item in store.list_memory_items()}
            try:
                written = store.write_complete_session(
                    raw_session,
                    session,
                    episode,
                    result,
                    _StageEmbeddingClient(self._embedding_client),
                    source_format=bank.source_format,
                )
            except _EmbeddingFailure as exc:
                raise WriteStageError("embedding", exc) from exc
            except EmbeddingValidationError as exc:
                raise WriteStageError("embedding", exc) from exc
            except Exception as exc:
                raise WriteStageError("persistence", exc) from exc

            created_ids = {
                item.memory_item_id
                for item in written.items
                if item.memory_item_id not in existing_ids
            }
            return session.session_ref, len(created_ids)
        finally:
            store.close()

    def retrieve(
        self,
        database_path: str | Path,
        request: RetrieveRequest,
    ) -> str:
        store = MemoryStore(database_path)
        try:
            resolver = QueryResolver(store)
            retriever = Retriever(
                store,
                resolver,
                _StageEmbeddingClient(self._embedding_client),  # type: ignore[arg-type]
            )
            internal_request = RetrievalRequest(
                query=request.query,
                context=request.context,
                limit=request.limit,
                **self._retrieval_settings.model_dump(),
            )
            return RetrievalResultFormatter(store).format(
                retriever.retrieve(internal_request),
                query=request.query,
                context=request.context,
            )
        except _EmbeddingFailure as exc:
            raise RetrievalStageError("embedding", exc) from exc
        except Exception as exc:
            raise RetrievalStageError("retrieval", exc) from exc
        finally:
            store.close()

    def list_pending_consolidation_subjects(
        self,
        database_path: str | Path,
        consolidator_type: ConsolidatorType,
    ) -> list[PendingConsolidationSubject]:
        store = MemoryStore(database_path)
        try:
            return store.list_pending_consolidation_subjects(consolidator_type)
        finally:
            store.close()

    @staticmethod
    def minimum_evidence_count(consolidator_type: ConsolidatorType) -> int:
        if consolidator_type == RepeatedBehaviorConsolidator.consolidator_type:
            return RepeatedBehaviorConsolidator.minimum_evidence_count
        raise ValueError(f"unsupported consolidator type: {consolidator_type}")

    def consolidate(
        self,
        database_path: str | Path,
        consolidator_type: ConsolidatorType,
        subject_entity_id: str,
        cancel_event: Event | None = None,
    ) -> ConsolidationResult:
        store = MemoryStore(database_path)
        try:
            if consolidator_type != RepeatedBehaviorConsolidator.consolidator_type:
                raise ValueError(f"unsupported consolidator type: {consolidator_type}")
            llm_client: _JSONClient = self._llm_client
            embedding_client: _EmbeddingClient = self._embedding_client
            before_persistence = None
            if cancel_event is not None:
                cancellable_llm = _CancellationAwareLLMClient(
                    self._llm_client,
                    cancel_event,
                )
                cancellable_embedding = _CancellationAwareEmbeddingClient(
                    self._embedding_client,
                    cancel_event,
                )
                llm_client = cancellable_llm
                embedding_client = cancellable_embedding

                def before_persistence() -> None:
                    if cancel_event.is_set():
                        raise ConsolidationCancelled("consolidation was cancelled")

            return RepeatedBehaviorConsolidator(
                store,
                llm_client,
                _StageEmbeddingClient(embedding_client),
                before_persistence=before_persistence,
            ).consolidate(subject_entity_id)
        except ConsolidationError as exc:
            if isinstance(exc.cause, ConsolidationCancelled):
                raise exc.cause from exc
            raise ConsolidationStageError(exc.stage, exc.cause) from exc
        finally:
            store.close()


def _validation_details(exc: ValidationError | ValueError) -> list[dict[str, str]]:
    if isinstance(exc, ValidationError):
        return [
            {
                "path": ".".join(str(value) for value in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
            for error in exc.errors()
        ]
    if isinstance(exc, RawDataParseError):
        return [
            {
                "path": ".".join(str(value) for value in exc.path),
                "message": str(exc),
                "type": type(exc).__name__,
            }
        ]
    return [
        {
            "path": "session",
            "message": str(exc),
            "type": type(exc).__name__,
        }
    ]
