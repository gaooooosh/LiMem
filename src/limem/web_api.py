# -*- coding: utf-8 -*-
"""Web API for LiMem LTM Search.

Provides FastAPI endpoints for the interactive search demo.
"""

import os
import sys
from dataclasses import asdict
from datetime import datetime
from typing import Any, Optional

# Add src to path if needed
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from .config import DB_PATH
from .db import init_db, open_connection
from .search import LTMSearcher, RetrievalConfig

# Initialize FastAPI app
app = FastAPI(
    title="LiMem LTM Search",
    description="Long-Term Memory Search Web Interface",
    version="0.1.0",
)

# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global variables for database connection and searcher
_conn = None
_searcher = None


def get_searcher() -> LTMSearcher:
    """Get or create the LTMSearcher instance."""
    global _conn, _searcher
    if _conn is None:
        _conn = open_connection(DB_PATH)
        init_db(_conn)
    if _searcher is None:
        config = RetrievalConfig(
            default_top_k=5,
            lambda_param=0.01,
            enable_vector_match=True,
        )
        _searcher = LTMSearcher(_conn, config)
    return _searcher


# Pydantic models for API
class SearchRequest(BaseModel):
    """Search request model."""
    query: str
    top_k: Optional[int] = 5


class EntityInfo(BaseModel):
    """Entity information model."""
    name: str
    match_type: str = "exact"
    similarity: Optional[float] = None


class EventDebugInfo(BaseModel):
    """Event debug information model."""
    event_id: str
    summary: str
    weight: float
    c_valid: int
    t_valid: int
    t_expired: Optional[int]
    t_invalid: Optional[int]
    t_now: int
    time_diff: int
    match_type: str
    entity_match_weights: dict[str, float]
    action: str = ""
    causality: str = ""
    participants: str = ""
    location: str = ""
    time_range: str = ""


class RankedEventResponse(BaseModel):
    """Ranked event response model."""
    event_id: str
    summary: str
    weight: float
    c_valid: int
    t_valid: int
    t_expired: Optional[int]
    t_invalid: Optional[int]
    action: str = ""
    causality: str = ""
    participants: str = ""
    location: str = ""
    time_range: str = ""


class SearchResponse(BaseModel):
    """Search response model."""
    query: str
    answer: str
    entities: list[str]
    ranked_events: list[RankedEventResponse]
    top_k_events: list[RankedEventResponse]
    debug: dict[str, Any]
    timestamp: str


class ChatMessage(BaseModel):
    """Chat message model."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: str
    debug_data: Optional[dict[str, Any]] = None


class StatsResponse(BaseModel):
    """Database statistics response model."""
    events: int
    entities: int
    episodes: int
    users: int
    involves_relations: int
    extracted_from_relations: int
    permanent_trait_relations: int


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main HTML page."""
    html_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(html_path):
        return FileResponse(html_path, media_type="text/html")
    return HTMLResponse(content="<html><body><h1>LiMem Search Interface</h1><p>Static files not found. Please create src/limem/static/index.html</p></body></html>")


@app.get("/api/stats", response_model=StatsResponse)
async def get_stats():
    """Get database statistics."""
    conn = open_connection(DB_PATH)
    init_db(conn)

    stats = {}

    # Count node types
    node_type_mapping = {
        "Event": "events",
        "Entity": "entities",
        "Episode": "episodes",
        "User": "users"
    }
    for node_type, key_name in node_type_mapping.items():
        resp = conn.execute(f"MATCH (n:{node_type}) RETURN count(*)")
        if resp.has_next():
            stats[key_name] = resp.get_next()[0]
        else:
            stats[key_name] = 0

    # Count relationships
    resp = conn.execute("MATCH ()-[r:INVOLVES]->() RETURN count(*)")
    stats["involves_relations"] = resp.get_next()[0] if resp.has_next() else 0

    resp = conn.execute("MATCH ()-[r:EXTRACTED_FROM]->() RETURN count(*)")
    stats["extracted_from_relations"] = resp.get_next()[0] if resp.has_next() else 0

    resp = conn.execute("MATCH ()-[r:PERMANENT_TRAIT]->() RETURN count(*)")
    stats["permanent_trait_relations"] = resp.get_next()[0] if resp.has_next() else 0

    return StatsResponse(**stats)


@app.post("/api/search", response_model=SearchResponse)
async def search(request: SearchRequest):
    """Execute a search query."""
    if not request.query or not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    try:
        searcher = get_searcher()
        result = searcher.search_debug(request.query, top_k=request.top_k)

        # Convert RankedEvent objects to dicts for JSON serialization
        ranked_events = []
        for event in result.get("ranked_events", []):
            if hasattr(event, '__dataclass_fields__'):
                ranked_events.append(asdict(event))
            else:
                ranked_events.append(event)

        top_k_events = []
        for event in result.get("top_k_events", []):
            if hasattr(event, '__dataclass_fields__'):
                top_k_events.append(asdict(event))
            else:
                top_k_events.append(event)

        # Convert weight_calculation_details RankedEvents if any
        debug = result.get("debug", {})
        weight_details = debug.get("weight_calculation_details", [])

        return SearchResponse(
            query=result["query"],
            answer=result["answer"],
            entities=result["entities"],
            ranked_events=ranked_events,
            top_k_events=top_k_events,
            debug={
                "entity_count": debug.get("entity_count", 0),
                "raw_event_count": debug.get("raw_event_count", 0),
                "ranked_event_count": debug.get("ranked_event_count", 0),
                "top_k_count": debug.get("top_k_count", 0),
                "weight_calculation_details": weight_details,
            },
            timestamp=datetime.now().isoformat(),
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the FastAPI server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
