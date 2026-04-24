"""Uvicorn entry point for LiMem service."""

from __future__ import annotations

import os

import uvicorn

from service.app import create_app


app = create_app()


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("service.main:app", host=host, port=port, workers=1)
