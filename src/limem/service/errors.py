"""Map service failures to stable HTTP errors and concise logs."""

from collections.abc import Sequence
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ServiceError(Exception):
    """One expected HTTP failure with fixed caller guidance."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        guidance: str,
        details: Sequence[dict[str, Any]] = (),
        docs_url: str | None = None,
        log_message: str | None = None,
        include_traceback: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.guidance = guidance
        self.details = list(details)
        self.docs_url = docs_url
        self.log_message = log_message
        self.include_traceback = include_traceback


def _error_content(request: Request, error: ServiceError) -> dict[str, object]:
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "guidance": error.guidance,
            "request_id": request.state.request_id,
            "details": error.details,
            "docs_url": error.docs_url,
        }
    }


def _request_validation_error(exc: RequestValidationError) -> ServiceError:
    details = [
        {
            "path": ".".join(str(value) for value in error["loc"] if value != "body"),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
    return ServiceError(
        status_code=422,
        code="invalid_request",
        message="请求格式不符合接口要求。",
        guidance="请根据 details 指出的字段修改请求后重新提交。",
        details=details,
        docs_url="/guide",
    )


def install_error_handlers(app: FastAPI, logger: logging.Logger) -> None:
    """Install the complete public error contract on one FastAPI app."""

    @app.exception_handler(ServiceError)
    async def handle_service_error(
        request: Request,
        exc: ServiceError,
    ) -> JSONResponse:
        if exc.log_message is not None:
            logger.error(exc.log_message, exc_info=exc.include_traceback)
        else:
            logger.error(
                "请求失败。请求 %s，接口：%s %s。原因：%s",
                request.state.request_id,
                request.method,
                request.url.path,
                exc.message,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_content(request, exc),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        error = _request_validation_error(exc)
        logger.error(
            "请求格式错误。请求 %s，接口：%s %s。原因：%s",
            request.state.request_id,
            request.method,
            request.url.path,
            error.message,
        )
        return JSONResponse(
            status_code=error.status_code,
            content=_error_content(request, error),
        )

    @app.exception_handler(404)
    async def handle_unknown_route(
        request: Request,
        _: StarletteHTTPException,
    ) -> JSONResponse:
        error = ServiceError(
            status_code=404,
            code="route_not_found",
            message="接口路径不存在。",
            guidance="请检查请求方法和路径，并对照服务接口文档。",
            docs_url="/guide",
        )
        logger.error(
            "接口路径不存在。请求 %s，接口：%s %s。",
            request.state.request_id,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=error.status_code,
            content=_error_content(request, error),
        )

    @app.exception_handler(405)
    async def handle_wrong_method(
        request: Request,
        _: StarletteHTTPException,
    ) -> JSONResponse:
        error = ServiceError(
            status_code=405,
            code="method_not_allowed",
            message="该接口不接受当前请求方法。",
            guidance="请检查 HTTP 方法，并对照服务接口文档。",
            docs_url="/guide",
        )
        logger.error(
            "请求方法错误。请求 %s，接口：%s %s。",
            request.state.request_id,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=error.status_code,
            content=_error_content(request, error),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.exception(
            "服务内部错误。请求 %s，接口：%s %s。原因：%s",
            request.state.request_id,
            request.method,
            request.url.path,
            exc,
        )
        error = ServiceError(
            status_code=500,
            code="internal_error",
            message="服务内部错误。",
            guidance="请记录 request_id 并联系服务管理员查看日志。",
        )
        return JSONResponse(
            status_code=error.status_code,
            content=_error_content(request, error),
        )
