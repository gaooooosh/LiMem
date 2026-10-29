"""Build the authenticated FastAPI application for Agent memory service calls."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Body, Depends, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from fastapi.security import APIKeyHeader
from pydantic import JsonValue

from limem.embedding_client import EmbeddingClient
from limem.llm_client import LLMClient, QwenLLMClient
from limem.memory_item_extractor.factory import create_workflow1_extractor
from limem.memory_store import MemoryStore

from .auth import (
    API_KEY_HEADER,
    AdminAuthenticationError,
    AdminAuthenticator,
    ApiKeyAuthenticationError,
    ApiKeyAuthenticator,
)
from .config import PROJECT_ROOT, ServiceSettings, load_service_settings
from .consolidation import (
    ConsolidationAlreadyRunningError,
    ConsolidationCoordinator,
    ConsolidationRunNotActiveError,
    ConsolidationRunNotFoundError,
)
from .errors import ServiceError, install_error_handlers
from .logging import create_service_logger
from .manager import KeyRevocationError, MemoryBankManager
from .models import (
    BankListResponse,
    BankView,
    CreateBankRequest,
    CreateKeyRequest,
    ConsolidationRunResponse,
    EntityListResponse,
    EntityView,
    ErrorResponse,
    HealthResponse,
    IssuedKey,
    KeyListResponse,
    KeyRecord,
    ManualConsolidationRequest,
    ManualConsolidationResponse,
    ProfileResponse,
    PutProfileRequest,
    RetrieveRequest,
    RetrieveResponse,
    SessionValidationResponse,
    StartConsolidationRunRequest,
    WriteSessionResponse,
)
from .pipeline import (
    DuplicateSessionError,
    ConsolidationStageError,
    InvalidSessionFormatError,
    MemoryServicePipeline,
    RetrievalStageError,
    WriteStageError,
)
from .repository import ServiceRepository

_GUIDE_FILES = {
    "/guide": PROJECT_ROOT / "docs/service/README.md",
    "/guide/authentication": PROJECT_ROOT / "docs/service/authentication.md",
    "/guide/memory-banks": PROJECT_ROOT / "docs/service/memory-banks.md",
    "/guide/consolidations": PROJECT_ROOT / "docs/service/consolidations.md",
    "/guide/session-formats/vehicle-session-v1": (
        PROJECT_ROOT / "docs/service/session-formats/vehicle-session-v1.md"
    ),
    "/guide/errors": PROJECT_ROOT / "docs/service/errors.md",
}

_API_KEY_SCHEME = APIKeyHeader(
    name=API_KEY_HEADER,
    scheme_name="XApiKey",
    auto_error=False,
)

_PROFILE_PUT_RESPONSES = {
    401: {"model": ErrorResponse, "description": "invalid_api_key"},
    404: {"model": ErrorResponse, "description": "memory_bank_not_found"},
    422: {"model": ErrorResponse, "description": "invalid_request"},
}
_PROFILE_GET_RESPONSES = {
    401: {"model": ErrorResponse, "description": "invalid_api_key"},
    404: {
        "model": ErrorResponse,
        "description": "memory_bank_not_found 或 profile_not_found",
    },
    422: {"model": ErrorResponse, "description": "invalid_request"},
}


@dataclass(frozen=True)
class _Caller:
    kind: Literal["admin", "user"]
    key_id: str | None


def _unauthorized() -> ServiceError:
    return ServiceError(
        status_code=401,
        code="invalid_api_key",
        message="缺少或无法识别 API key。",
        guidance=f"请在 {API_KEY_HEADER} 请求头中提供有效的 API key。",
        docs_url="/guide/authentication",
    )


def _bank_not_found() -> ServiceError:
    return ServiceError(
        status_code=404,
        code="memory_bank_not_found",
        message="记忆库不存在。",
        guidance="请检查 memory_bank_id，或确认记忆库尚未过期和删除。",
        docs_url="/guide/memory-banks",
    )


def _profile_not_found() -> ServiceError:
    return ServiceError(
        status_code=404,
        code="profile_not_found",
        message="该实体尚未写入 Profile。",
        guidance="请先通过 PUT 接口写入该实体的 Profile。",
        docs_url="/guide/memory-banks",
    )


def _safe_reason(exc: Exception) -> str:
    first_line = str(exc).splitlines()[0].strip()
    return first_line or type(exc).__name__


def _session_label(raw_session: JsonValue | None) -> str:
    raw_session_id = (
        raw_session.get("session_id") if isinstance(raw_session, dict) else None
    )
    if (
        isinstance(raw_session_id, str)
        and "\r" not in raw_session_id
        and "\n" not in raw_session_id
    ):
        return raw_session_id
    return "未知"


def _invalid_session_error(
    exc: InvalidSessionFormatError,
    *,
    log_message: str,
) -> ServiceError:
    return ServiceError(
        status_code=422,
        code="invalid_session_format",
        message="Session 格式不符合 vehicle_session_v1 要求。",
        guidance=(
            "请根据 details 指出的字段修改 Session，并对照 "
            "vehicle_session_v1 格式文档后重新提交。"
        ),
        details=exc.details,
        docs_url="/guide/session-formats/vehicle-session-v1",
        log_message=log_message,
    )


def create_app(
    settings: ServiceSettings | None = None,
    llm_client: LLMClient | None = None,
    embedding_client: EmbeddingClient | None = None,
    *,
    qwen_client: QwenLLMClient | None = None,
    workflow1_extractor=None,
) -> FastAPI:
    """Create one single-process service application and its dependencies."""

    runtime = settings if settings is not None else load_service_settings()
    runtime.data_dir.mkdir(parents=True, exist_ok=True)
    logger = create_service_logger(runtime.log_path)
    repository = ServiceRepository(runtime.data_dir / "service.db")
    manager = MemoryBankManager(repository, runtime.data_dir, logger)
    user_authenticator = ApiKeyAuthenticator(repository)
    admin_authenticator = AdminAuthenticator(runtime.admin_api_key.get_secret_value())
    resolved_llm_client = llm_client if llm_client is not None else LLMClient()
    resolved_embedding_client = (
        embedding_client if embedding_client is not None else EmbeddingClient()
    )
    extractor = workflow1_extractor if workflow1_extractor is not None else create_workflow1_extractor(
        qwen_client if qwen_client is not None else QwenLLMClient(),
        trace_root=runtime.data_dir / "debug/workflow1-v4",
    )
    pipeline = MemoryServicePipeline(
        extractor,
        resolved_embedding_client,
        runtime.retrieval,
        resolved_llm_client,
    )
    consolidations = ConsolidationCoordinator(manager, pipeline, logger)

    async def cleanup_loop() -> None:
        while True:
            await asyncio.sleep(runtime.cleanup_interval_seconds)
            await asyncio.to_thread(manager.cleanup_expired_banks)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logger.info("LiMem 服务启动。")
        await asyncio.to_thread(manager.cleanup_expired_banks)
        cleanup_task = asyncio.create_task(cleanup_loop())
        try:
            yield
        finally:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
            await consolidations.shutdown()
            repository.close()
            logger.info("LiMem 服务停止。")
            for handler in logger.handlers:
                handler.close()

    app = FastAPI(
        title="LiMem Agent Memory Service",
        version="1",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = runtime
    app.state.repository = repository
    app.state.manager = manager
    app.state.pipeline = pipeline
    app.state.consolidations = consolidations
    app.state.logger = logger

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request.state.request_id = f"req_{uuid4().hex}"
        return await call_next(request)

    install_error_handlers(app, logger)

    def presented_api_key(
        api_key: str | None = Depends(_API_KEY_SCHEME),
    ) -> str:
        if api_key is None or not api_key:
            raise _unauthorized()
        return api_key

    def require_admin(api_key: str = Depends(presented_api_key)) -> None:
        try:
            admin_authenticator.authenticate(api_key)
        except AdminAuthenticationError as exc:
            raise _unauthorized() from exc

    def require_user(api_key: str = Depends(presented_api_key)) -> KeyRecord:
        try:
            return user_authenticator.authenticate(api_key)
        except ApiKeyAuthenticationError as exc:
            raise _unauthorized() from exc

    def require_caller(api_key: str = Depends(presented_api_key)) -> _Caller:
        try:
            admin_authenticator.authenticate(api_key)
        except AdminAuthenticationError:
            try:
                key = user_authenticator.authenticate(api_key)
            except ApiKeyAuthenticationError as exc:
                raise _unauthorized() from exc
            return _Caller(kind="user", key_id=key.key_id)
        return _Caller(kind="admin", key_id=None)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse()

    @app.post(
        "/v1/admin/api-keys",
        response_model=IssuedKey,
        status_code=201,
        dependencies=[Depends(require_admin)],
    )
    def issue_api_key(body: CreateKeyRequest) -> IssuedKey:
        issued = repository.issue_key(body.name)
        logger.info(
            "签发 API Key 成功。API Key %s，名称：%s。", issued.key_id, body.name
        )
        return issued

    @app.get(
        "/v1/admin/api-keys",
        response_model=KeyListResponse,
        dependencies=[Depends(require_admin)],
    )
    def list_api_keys() -> KeyListResponse:
        return KeyListResponse(api_keys=repository.list_keys())

    @app.delete(
        "/v1/admin/api-keys/{key_id}",
        status_code=204,
        dependencies=[Depends(require_admin)],
    )
    def revoke_api_key(key_id: str) -> Response:
        try:
            manager.revoke_key_and_delete_banks(key_id)
        except KeyError as exc:
            raise ServiceError(
                status_code=404,
                code="api_key_not_found",
                message="API key 记录不存在。",
                guidance="请通过 API key 列表检查 key_id 后重新提交。",
                docs_url="/guide/authentication",
            ) from exc
        except KeyRevocationError as exc:
            raise ServiceError(
                status_code=500,
                code="key_revocation_failed",
                message="API key 已撤销，但部分记忆库删除失败。",
                guidance="请记录 request_id 并联系服务管理员查看日志。",
                details=[
                    {"memory_bank_id": memory_bank_id}
                    for memory_bank_id in exc.failed_memory_bank_ids
                ],
                log_message=(
                    f"撤销 API Key 未完整删除数据。API Key {key_id}，"
                    f"失败记忆库：{', '.join(exc.failed_memory_bank_ids)}。"
                ),
                include_traceback=True,
            ) from exc
        return Response(status_code=204)

    @app.post("/v1/limem", response_model=BankView, status_code=201)
    def create_memory_bank(
        body: CreateBankRequest,
        key: KeyRecord = Depends(require_user),
    ) -> BankView:
        try:
            return BankView.from_record(manager.create_bank(key.key_id, body))
        except KeyError as exc:
            raise _unauthorized() from exc

    @app.get("/v1/limem", response_model=BankListResponse)
    def list_memory_banks(
        key: KeyRecord = Depends(require_user),
    ) -> BankListResponse:
        return BankListResponse(
            memory_banks=[
                BankView.from_record(bank) for bank in manager.list_banks(key.key_id)
            ]
        )

    @app.delete("/v1/limem/{memory_bank_id}", status_code=204)
    def delete_memory_bank(
        memory_bank_id: str,
        caller: _Caller = Depends(require_caller),
    ) -> Response:
        try:
            with consolidations.reserve_bank_for_deletion(memory_bank_id):
                if caller.kind == "admin":
                    manager.delete_admin_bank(memory_bank_id)
                else:
                    manager.delete_owned_bank(caller.key_id or "", memory_bank_id)
        except KeyError as exc:
            raise _bank_not_found() from exc
        except ConsolidationAlreadyRunningError as exc:
            raise ServiceError(
                status_code=409,
                code="consolidation_already_running",
                message="该记忆库已有巩固运行。",
                guidance="请取消或等待巩固运行结束后再删除。",
                docs_url="/guide",
            ) from exc
        except Exception as exc:
            raise ServiceError(
                status_code=500,
                code="memory_bank_deletion_failed",
                message="记忆库删除失败。",
                guidance="请记录 request_id 并联系服务管理员查看日志。",
                log_message=(
                    f"删除记忆库失败。记忆库 {memory_bank_id}。"
                    f"原因：{_safe_reason(exc)}。"
                ),
                include_traceback=True,
            ) from exc
        return Response(status_code=204)

    @app.post(
        "/v1/limem/session-formats/vehicle-session-v1/validate",
        response_model=SessionValidationResponse,
    )
    def validate_session_format(
        request: Request,
        raw_session: Annotated[JsonValue | None, Body()] = None,
        _: KeyRecord = Depends(require_user),
    ) -> SessionValidationResponse:
        session_label = _session_label(raw_session)
        try:
            session = pipeline.validate_session(raw_session)
        except InvalidSessionFormatError as exc:
            raise _invalid_session_error(
                exc,
                log_message=(
                    f"Session 格式校验失败。请求 {request.state.request_id}，"
                    f"Session {session_label}。"
                ),
            ) from exc
        logger.info(
            "Session 格式校验成功。请求 %s，Session %s，Record %d 条。",
            request.state.request_id,
            session.session_ref,
            session.record_count,
        )
        return SessionValidationResponse(
            session_id=session.session_ref,
            record_count=session.record_count,
        )

    @app.post(
        "/v1/limem/{memory_bank_id}/sessions",
        response_model=WriteSessionResponse,
    )
    def write_session(
        request: Request,
        memory_bank_id: str,
        raw_session: Annotated[JsonValue | None, Body()] = None,
        key: KeyRecord = Depends(require_user),
    ) -> WriteSessionResponse:
        request.state.memory_bank_id = memory_bank_id
        session_label = _session_label(raw_session)
        request.state.session_id = session_label
        try:
            with manager.use_owned_bank(key.key_id, memory_bank_id) as (bank, path):
                session_id, created = pipeline.write_session(path, bank, raw_session)
        except KeyError as exc:
            raise _bank_not_found() from exc
        except InvalidSessionFormatError as exc:
            raise _invalid_session_error(
                exc,
                log_message=(
                    f"Session 格式错误。请求 {request.state.request_id}，"
                    f"记忆库 {memory_bank_id}，Session {session_label}。"
                ),
            ) from exc
        except DuplicateSessionError as exc:
            raise ServiceError(
                status_code=409,
                code="duplicate_session",
                message="该 Session 已成功处理，不能重复写入。",
                guidance="请检查是否使用了重复的 session_id。",
                docs_url="/guide/errors",
                log_message=(
                    f"拒绝重复 Session。请求 {request.state.request_id}，"
                    f"记忆库 {memory_bank_id}，Session {session_label}。"
                ),
            ) from exc
        except WriteStageError as exc:
            status_code = (
                502 if exc.stage in {"memory_extraction", "embedding"} else 500
            )
            raise ServiceError(
                status_code=status_code,
                code="write_failed",
                message="Session 写入失败，本次请求未写入任何数据。",
                guidance="请记录 request_id 并联系服务管理员查看日志。",
                details=[{"stage": exc.stage}],
                docs_url="/guide/errors",
                log_message=(
                    f"写入 Session 失败。请求 {request.state.request_id}，"
                    f"记忆库 {memory_bank_id}，Session {session_label}，"
                    f"失败阶段：{exc.stage}。原因：{_safe_reason(exc.cause)}。"
                ),
                include_traceback=True,
            ) from exc

        logger.info(
            "写入 Session 成功。请求 %s，记忆库 %s，Session %s，新增记忆 %d 条。",
            request.state.request_id,
            memory_bank_id,
            session_id,
            created,
        )
        return WriteSessionResponse(
            memory_bank_id=memory_bank_id,
            session_id=session_id,
            memory_items_created=created,
        )

    @app.get(
        "/v1/limem/{memory_bank_id}/entities",
        response_model=EntityListResponse,
    )
    def list_entities(
        memory_bank_id: str,
        key: KeyRecord = Depends(require_user),
    ) -> EntityListResponse:
        try:
            with manager.use_owned_bank(key.key_id, memory_bank_id) as (_, path):
                store = MemoryStore(path)
                try:
                    entities: list[EntityView] = []
                    for entity in store.list_entities():
                        try:
                            store.get_profile(entity.id)
                        except KeyError:
                            has_profile = False
                        else:
                            has_profile = True
                        entities.append(
                            EntityView(
                                entity_id=entity.id,
                                name=entity.name,
                                aliases=entity.aliases,
                                types=entity.types,
                                has_profile=has_profile,
                            )
                        )
                finally:
                    store.close()
        except KeyError as exc:
            raise _bank_not_found() from exc
        return EntityListResponse(entities=entities)

    @app.put(
        "/v1/limem/{memory_bank_id}/entities/{entity_id}/profile",
        response_model=ProfileResponse,
        responses=_PROFILE_PUT_RESPONSES,
    )
    def put_profile(
        memory_bank_id: str,
        entity_id: str,
        body: PutProfileRequest,
        key: KeyRecord = Depends(require_user),
    ) -> ProfileResponse:
        try:
            with manager.use_owned_bank(key.key_id, memory_bank_id) as (_, path):
                store = MemoryStore(path)
                try:
                    profile = store.put_profile(entity_id, body.text)
                finally:
                    store.close()
        except KeyError as exc:
            raise _bank_not_found() from exc
        return ProfileResponse.model_validate(profile.model_dump())

    @app.get(
        "/v1/limem/{memory_bank_id}/entities/{entity_id}/profile",
        response_model=ProfileResponse,
        responses=_PROFILE_GET_RESPONSES,
    )
    def get_profile(
        memory_bank_id: str,
        entity_id: str,
        key: KeyRecord = Depends(require_user),
    ) -> ProfileResponse:
        try:
            with manager.use_owned_bank(key.key_id, memory_bank_id) as (_, path):
                store = MemoryStore(path)
                try:
                    try:
                        profile = store.get_profile(entity_id)
                    except KeyError as exc:
                        raise _profile_not_found() from exc
                finally:
                    store.close()
        except KeyError as exc:
            raise _bank_not_found() from exc
        return ProfileResponse.model_validate(profile.model_dump())

    @app.post(
        "/v1/limem/{memory_bank_id}/retrieve",
        response_model=RetrieveResponse,
    )
    def retrieve_memory(
        request: Request,
        memory_bank_id: str,
        body: RetrieveRequest,
        caller: _Caller = Depends(require_caller),
    ) -> RetrieveResponse:
        request.state.memory_bank_id = memory_bank_id
        try:
            bank_context = (
                manager.use_admin_bank(memory_bank_id)
                if caller.kind == "admin"
                else manager.use_owned_bank(caller.key_id or "", memory_bank_id)
            )
            with bank_context as (_, path):
                content = pipeline.retrieve(path, body)
        except KeyError as exc:
            raise _bank_not_found() from exc
        except RetrievalStageError as exc:
            raise ServiceError(
                status_code=502 if exc.stage == "embedding" else 500,
                code="retrieval_failed",
                message="记忆检索失败。",
                guidance="请记录 request_id 并联系服务管理员查看日志。",
                details=[{"stage": exc.stage}],
                docs_url="/guide/errors",
                log_message=(
                    f"检索记忆失败。请求 {request.state.request_id}，"
                    f"记忆库 {memory_bank_id}，失败阶段：{exc.stage}。"
                    f"原因：{_safe_reason(exc.cause)}。"
                ),
                include_traceback=True,
            ) from exc
        return RetrieveResponse(memory_bank_id=memory_bank_id, content=content)

    @app.post(
        "/v1/limem/{memory_bank_id}/consolidations",
        response_model=ManualConsolidationResponse,
    )
    def consolidate_subject(
        request: Request,
        memory_bank_id: str,
        body: ManualConsolidationRequest,
        key: KeyRecord = Depends(require_user),
    ) -> ManualConsolidationResponse:
        request.state.memory_bank_id = memory_bank_id
        try:
            pending_evidence_count, result = consolidations.consolidate_subject(
                key.key_id,
                memory_bank_id,
                body.consolidator_type,
                body.subject_entity_id,
            )
        except KeyError as exc:
            raise _bank_not_found() from exc
        except ConsolidationAlreadyRunningError as exc:
            raise ServiceError(
                status_code=409,
                code="consolidation_already_running",
                message="该记忆库已有巩固运行。",
                guidance="请查询现有运行，或等待它结束后再提交。",
                docs_url="/guide",
            ) from exc
        except ConsolidationStageError as exc:
            status_code = 500 if exc.stage in {"source_loading", "persistence"} else 502
            raise ServiceError(
                status_code=status_code,
                code="consolidation_failed",
                message="记忆巩固失败，本次未写入候选记忆。",
                guidance="请记录 request_id 并联系服务管理员查看日志。",
                details=[{"stage": exc.stage}],
                docs_url="/guide/errors",
                log_message=(
                    f"巩固失败。请求 {request.state.request_id}，"
                    f"记忆库 {memory_bank_id}，实体 {body.subject_entity_id}，"
                    f"阶段：{exc.stage}。原因：{_safe_reason(exc.cause)}。"
                ),
                include_traceback=True,
            ) from exc
        return ManualConsolidationResponse(
            memory_bank_id=memory_bank_id,
            subject_entity_id=body.subject_entity_id,
            consolidator_type=body.consolidator_type,
            pending_evidence_count=pending_evidence_count,
            outcome="consolidated" if result.candidates else "no_consolidation",
            candidate_memory_item_ids=[candidate.id for candidate in result.candidates],
        )

    @app.post(
        "/v1/limem/{memory_bank_id}/auto-consolidations",
        response_model=ConsolidationRunResponse,
        status_code=202,
    )
    async def start_consolidation_run(
        memory_bank_id: str,
        body: StartConsolidationRunRequest,
        key: KeyRecord = Depends(require_user),
    ) -> ConsolidationRunResponse:
        try:
            manager.require_owned_active(key.key_id, memory_bank_id)
            return consolidations.start_run(
                key.key_id,
                memory_bank_id,
                body.consolidator_type,
                body.max_entities,
            )
        except KeyError as exc:
            raise _bank_not_found() from exc
        except ConsolidationAlreadyRunningError as exc:
            raise ServiceError(
                status_code=409,
                code="consolidation_already_running",
                message="该记忆库已有巩固运行。",
                guidance="请查询现有运行，或等待它结束后再提交。",
                docs_url="/guide",
            ) from exc

    @app.get(
        "/v1/limem/{memory_bank_id}/auto-consolidations/{run_id}",
        response_model=ConsolidationRunResponse,
    )
    def get_consolidation_run(
        memory_bank_id: str,
        run_id: str,
        key: KeyRecord = Depends(require_user),
    ) -> ConsolidationRunResponse:
        try:
            manager.require_owned_active(key.key_id, memory_bank_id)
            return consolidations.get_run(memory_bank_id, run_id)
        except KeyError as exc:
            raise _bank_not_found() from exc
        except ConsolidationRunNotFoundError as exc:
            raise ServiceError(
                status_code=404,
                code="consolidation_run_not_found",
                message="巩固运行不存在。",
                guidance="请检查 run_id 和 memory_bank_id。",
                docs_url="/guide",
            ) from exc

    @app.post(
        "/v1/limem/{memory_bank_id}/auto-consolidations/{run_id}/cancel",
        response_model=ConsolidationRunResponse,
        status_code=202,
    )
    def cancel_consolidation_run(
        memory_bank_id: str,
        run_id: str,
        key: KeyRecord = Depends(require_user),
    ) -> ConsolidationRunResponse:
        try:
            manager.require_owned_active(key.key_id, memory_bank_id)
            return consolidations.cancel_run(memory_bank_id, run_id)
        except KeyError as exc:
            raise _bank_not_found() from exc
        except ConsolidationRunNotFoundError as exc:
            raise ServiceError(
                status_code=404,
                code="consolidation_run_not_found",
                message="巩固运行不存在。",
                guidance="请检查 run_id 和 memory_bank_id。",
                docs_url="/guide",
            ) from exc
        except ConsolidationRunNotActiveError as exc:
            raise ServiceError(
                status_code=409,
                code="consolidation_run_not_active",
                message="巩固运行已经结束，不能取消。",
                guidance="请查询运行状态。",
                docs_url="/guide",
            ) from exc

    def build_guide_handler(path: Path):
        def guide() -> PlainTextResponse:
            return PlainTextResponse(
                path.read_text(encoding="utf-8"),
                media_type="text/markdown",
            )

        return guide

    for guide_path, file_path in _GUIDE_FILES.items():
        app.add_api_route(
            guide_path,
            build_guide_handler(file_path),
            methods=["GET"],
            include_in_schema=False,
        )

    return app
