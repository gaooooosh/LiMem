# CLAUDE.md

This repository is centered on the current `create_ltm()` pipeline, not the removed legacy compatibility stack.

## Project Overview

LiMem is a long-term memory system built with Kuzu and LLM-backed extraction/retrieval for conversational AI scenarios.

The current mainline architecture is:

1. `Episode` ingestion
2. `Event` extraction and append-first write
3. incremental `Context` resolution
4. incremental `Pattern` induction
5. local `NEXT` linking between events
6. retrieval that mixes entity, context, pattern, recency, and support signals

## Active Modules

```text
src/limem/
├── config.py
├── factory.py
├── ltmemory_impl.py
├── db.py
├── utils.py
├── core/
├── builder/
├── retriever/
├── evolution/
├── migration.py
└── storage/
```

Important entry points:

- `src/limem/factory.py`: `create_ltm()`
- `src/limem/ltmemory_impl.py`: unified system interface
- `src/limem/builder/memory_builder.py`: ingestion/build pipeline
- `src/limem/retriever/memory_searcher.py`: retrieval pipeline
- `src/limem/evolution/dynamic_engine.py`: dynamic graph evolution

## Removed Legacy Surface

The following compatibility modules have been intentionally removed and should not be referenced in new work:

- `src/limem/ltm.py`
- `src/limem/search.py`
- `src/limem/models.py`
- `src/limem/data.py`
- `src/limem/demo.py`
- `src/limem/viz.py`
- `src/limem/web_api.py`

Related scripts removed:

- `src/script/build_ltm_from_example.py`
- `src/script/web_demo.py`
- `src/script/batch_test.py`

## Environment Notes

- Use the repository virtual environment if present: `source .venv/bin/activate`
- Python requirement: 3.12+
- Dependencies are managed with `uv`

## Common Commands

Build a DB from `trips.json`:

```bash
source .venv/bin/activate
python src/script/build_ltm_from_trips.py --clear-db
```

Interactive search:

```bash
python src/script/search_demo.py
```

Dynamic graph validation:

```bash
python src/script/test_dynamic_trips.py
```

DB/search diagnostics:

```bash
python src/script/test_search_fixes.py
```

## Configuration

Configuration is centralized in `src/limem/config.py`.

Key groups:

- write path: `APPEND_FIRST_MODE`, `ENABLE_DYNAMIC_EVOLUTION`
- retrieval: `SEARCH_TOP_K`, `SEARCH_MAX_TOKENS`, `SEARCH_TEMPERATURE`
- evolution: `CONTEXT_*`, `PATTERN_*`, `NEXT_*`
- consolidation: `ENABLE_AUTO_CONSOLIDATION`, `EVENT_CONSOLIDATION_*`
- runtime: `OFFLINE_MODE`, `DB_PATH`, `DASHSCOPE_API_KEY`

## Prompts

Prompt templates live in `src/prompts/` and are loaded through `src/limem/utils.py`.
