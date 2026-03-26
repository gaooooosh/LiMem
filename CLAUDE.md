# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LiMem is a long-term memory system built with Kuzu (graph database) and LLM-backed extraction/retrieval for conversational AI scenarios. The system uses DashScope (Aliyun Qwen) for LLM operations.

### Core Data Flow

```
Episode (raw input) → Event (extracted) → Entity/Context/Pattern (derived)
                                      ↓
                              NEXT relations (temporal/causal links between events)
```

The write path follows append-first semantics: events are always written first, then deduplicated/consolidated asynchronously.

### Main Entry Point

```python
from limem import create_ltm

ltm = create_ltm(db_path="./DB/dynamic_trips.kz", config={"offline_mode": True})
result = ltm.search("What does the user usually do in meetings?")
```

## Architecture

| Module | Purpose |
|--------|---------|
| `factory.py` | `create_ltm()` - system assembly |
| `ltmemory_impl.py` | Unified interface (ingest, search, write, merge) |
| `builder/memory_builder.py` | Episode → Event extraction pipeline |
| `builder/extractor.py` | Two-stage LLM extraction (segments → structured events) |
| `builder/context_extractor.py` | Context extraction and resolution |
| `retriever/memory_searcher.py` | Four-stage retrieval pipeline |
| `evolution/dynamic_engine.py` | Incremental Context/Pattern/NEXT maintenance |
| `storage/kuzu_store.py` | Graph database operations |
| `ops.py` | MemoryGraphOps for write/remove/merge/snapshot |

## Common Commands

```bash
# Activate environment
source .venv/bin/activate

# Build DB from trips.json
python src/script/build_ltm_from_trips.py --clear-db

# Interactive search
python src/script/search_demo.py
python src/script/search_demo.py --demo  # preset queries

# Dynamic graph validation
python src/script/test_dynamic_trips.py

# DB/search diagnostics
python src/script/test_search_fixes.py

# Interactive debugger UI (http://127.0.0.1:8011)
PYTHONPATH=src uv run python src/script/run_trips_debugger.py
```

## Configuration

Configuration is in `src/limem/config.py`. Copy `.env.example` to `.env` and set:

Required:
- `DASHSCOPE_API_KEY` - Aliyun API key

Key toggles:
- `APPEND_FIRST_MODE=true` - always write events before consolidation
- `ENABLE_DYNAMIC_EVOLUTION=true` - enable Context/Pattern/NEXT evolution
- `ENABLE_AUTO_CONSOLIDATION=true` - automatic event merging

Retrieval:
- `SEARCH_TOP_K=5` - events to return
- `SEARCH_ENABLE_VECTOR_MATCH=true` - semantic entity matching

Consolidation:
- `SIMILARITY_THRESHOLD=0.65` - merge threshold (multi-dimensional score)
- `EVENT_CONSOLIDATION_THRESHOLD=0.78` - consolidation threshold

## Prompts

LLM prompt templates are in `src/prompts/` and loaded via `src/limem/utils.py:load_prompt()`.

Key prompts:
- `extract_event_*.txt` - event extraction (segments, struct, only variants)
- `extract_context_*.txt` - context extraction
- `entity_extraction_*.txt` - entity extraction
- `generate_answer_*.txt` - answer generation

## Removed Legacy Modules

Do not reference these removed files:
- `src/limem/ltm.py`, `search.py`, `models.py`, `data.py`, `demo.py`, `viz.py`, `web_api.py`
- `src/script/build_ltm_from_example.py`, `web_demo.py`, `batch_test.py`
