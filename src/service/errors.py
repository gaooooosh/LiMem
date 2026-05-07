"""Centralized exception → JSON response mapping for the service layer."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .auth import (
    DatabaseAlreadyExistsError,
    KeyNotFoundError,
    UserAlreadyExistsError,
    UserNotFoundError,
)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(UserAlreadyExistsError)
    async def _user_exists(_: Request, exc: UserAlreadyExistsError):
        return JSONResponse(status_code=409, content={"detail": f"user already exists: {exc}"})

    @app.exception_handler(UserNotFoundError)
    async def _user_missing(_: Request, exc: UserNotFoundError):
        return JSONResponse(status_code=404, content={"detail": f"user not found: {exc}"})

    @app.exception_handler(KeyNotFoundError)
    async def _key_missing(_: Request, exc: KeyNotFoundError):
        return JSONResponse(status_code=404, content={"detail": f"not found: {exc}"})

    @app.exception_handler(DatabaseAlreadyExistsError)
    async def _db_exists(_: Request, exc: DatabaseAlreadyExistsError):
        return JSONResponse(status_code=409, content={"detail": f"database already exists: {exc}"})

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})
