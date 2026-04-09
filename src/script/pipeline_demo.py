# -*- coding: utf-8 -*-
"""Pipeline demo web app - Algorithm data flow visualizer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse


def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"


@dataclass
class PipelineDemoConfig:
    """Configuration for the pipeline demo app."""

    db_path: str = ""
    offline_mode: bool = True


def create_pipeline_demo_app(
    config: Optional[PipelineDemoConfig] = None,
) -> FastAPI:
    """Create the pipeline demo FastAPI application."""
    config = config or PipelineDemoConfig()

    app = FastAPI(title="LiMem Pipeline Visualizer")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_path = _static_dir() / "pipeline_demo.html"
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))

    @app.get("/api/pipeline/info")
    async def pipeline_info() -> dict[str, Any]:
        """Return basic pipeline configuration info."""
        try:
            from limem.config import (
                APPEND_FIRST_MODE,
                CONTEXT_EXTRACTION_BATCH_SIZE,
                CONTEXT_REUSE_THRESHOLD,
                DECAY_RATE,
                EMBEDDING_DIM,
                EMBEDDING_MODEL,
                ENABLE_AUTO_CONSOLIDATION,
                ENABLE_DYNAMIC_EVOLUTION,
                ENABLE_EVENT_RELATIONS,
                EVENT_CONSOLIDATION_THRESHOLD,
                GENERATION_MODEL,
            )

            return {
                "generation_model": GENERATION_MODEL,
                "embedding_model": EMBEDDING_MODEL,
                "embedding_dim": EMBEDDING_DIM,
                "append_first_mode": APPEND_FIRST_MODE,
                "enable_dynamic_evolution": ENABLE_DYNAMIC_EVOLUTION,
                "enable_event_relations": ENABLE_EVENT_RELATIONS,
                "enable_auto_consolidation": ENABLE_AUTO_CONSOLIDATION,
                "decay_rate": DECAY_RATE,
                "event_consolidation_threshold": EVENT_CONSOLIDATION_THRESHOLD,
                "context_reuse_threshold": CONTEXT_REUSE_THRESHOLD,
                "context_extraction_batch_size": CONTEXT_EXTRACTION_BATCH_SIZE,
            }
        except Exception as exc:
            return {"error": str(exc)}

    return app
