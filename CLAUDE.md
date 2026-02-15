# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LiMem is a **Long-Term Memory (LTM) system** built with Kuzu graph database and LLM abstraction. It implements episodic memory consolidation, entity-based retrieval, and weighted memory decay for conversational AI applications (specifically automotive/voice assistant scenarios).

### Core Architecture

The system follows a **four-stage pipeline**:

1. **Episode Ingestion** (`ltm.py`): Raw conversational episodes are processed to extract structured events and entities
2. **Memory Consolidation** (`ltm.py`): Events are consolidated via vector similarity, merging similar memories and updating relationship weights
3. **Memory Retrieval** (`search.py`): Four-stage search pipeline (entity extraction → graph path search → weight-based reranking → LLM summarization)
4. **Visualization** (`viz.py`): Export memory graphs as Cypher queries for Kuzu Explorer

### Data Model (src/limem/models.py)

Key concepts:
- **Episode**: Raw conversational record (temporary, TTL-based cleanup)
- **Event**: Consolidated semantic memory with temporal validity tracking
- **Entity**: Symbol node (people, locations, concepts) with embedding vectors
- **INVOLVES relation**: Weighted relationship between Events and Entities with temporal tracking (t_created, t_valid, t_expired, t_invalid, c_valid)
- **EXTRACTED_FROM relation**: Provenance link from Event to Episode
- **PERMANENT_TRAIT relation**: High-frequency memories promoted to user traits

Weight calculation: `weight = log(1 + c_valid) * exp(-DECAY_RATE * time_diff)`

## Environment Constraints

**CRITICAL**: This environment has specific limitations:
- **No sudo privileges**: All operations must be user-space only
- **uv-managed Python**: Virtual environment is pre-configured in `.venv/`
- **Activation required**: Always activate venv before running Python commands:

```bash
source .venv/bin/activate
```

After activation, `python` and `pip` commands will automatically use the correct environment.

## Development Commands

### Run Demo (Build Memory from Test Data)
```bash
source .venv/bin/activate
python main.py
```
This loads episodes from `example.json`, processes them through LTM, and exports visualization queries.

### Search Memory
```bash
source .venv/bin/activate
python src/script/search_demo.py
```
Interactive search interface for querying the memory graph.

### Environment Setup

**Required**: Copy `.env.example` to `.env` and configure:
```
DASHSCOPE_API_KEY=your-api-key-here
DB_PATH=./DB/demo_db.kz
TEST_DATA_PATH=./example.json
```

**Python**: Requires Python >=3.12 (see pyproject.toml)

**Dependencies**: Managed via uv (see pyproject.toml for dashscope, kuzu, pydantic)

## Configuration

All parameters are centralized in `src/limem/config.py` with environment variable overrides via `.env`. Key categories:

- **LTM parameters** (memory consolidation): `SIMILARITY_THRESHOLD`, `DECAY_RATE`, `EPISODE_TTL`, `PRUNE_C_VALID_THRESHOLD`
- **Search parameters** (retrieval): `SEARCH_TOP_K`, `SEARCH_LAMBDA`, `SEARCH_MAX_ENTITIES`
- **Vector matching**: `SEARCH_ENABLE_VECTOR_MATCH`, `SEARCH_VECTOR_THRESHOLD`
- **Models**: `GENERATION_MODEL`, `EMBEDDING_MODEL` (DashScope)

See `.env.example` for detailed explanations of each parameter's impact.

## File Structure

```
src/
├── limem/           # Core package
│   ├── config.py     # All configuration parameters
│   ├── models.py     # Data models (EpisodicEventFrame, RankedEvent, etc.)
│   ├── ltm.py        # ResearchLTM - memory consolidation (episode→event)
│   ├── search.py     # LTMSearcher - retrieval pipeline
│   ├── db.py         # Kuzu database schema and connection
│   ├── utils.py      # Utilities (JSON handling, prompt loading, time buckets)
│   ├── data.py       # Test data loader from example.json
│   ├── viz.py        # MemoryVisualizer - exports Cypher queries
│   └── demo.py       # Main demo orchestrator
├── prompts/         # LLM prompts (loaded dynamically via utils.py)
└── script/          # Standalone scripts (build_ltm_from_example.py, search_demo.py)
```

## Key Design Patterns

### Event-Entity Extraction (Two-Stage LLM)
The system splits extraction into two LLM calls (see ltm.py:48-110):
1. `extract_event_from_llm()`: Extracts structured event (summary, participants, time_range, location, action, causality, evidence)
2. `extract_entities_from_llm()`: Extracts entities separately

This separation prevents context overflow and allows independent optimization.

### Memory Consolidation Logic (ltm.py:256-404)
1. **Vector Similarity Search**: Find most similar existing event via cosine similarity
2. **Merge or Create**: If similarity > threshold, merge evidence and refresh embedding; otherwise create new event
3. **Relationship Update**: For each entity, create/update INVOLVES relation, incrementing c_valid
4. **Trait Promotion**: When c_valid exceeds PRUNE_C_VALID_THRESHOLD, promote to PERMANENT_TRAIT

### Hybrid Entity Matching (search.py:262-379)
Combines exact string matching with vector similarity matching:
1. Exact match: Cypher query with entity list (weight = 1.0)
2. Vector match: Generate embedding for query entities, find similar DB entities (weight = similarity²)
3. Combine: Events from both methods are merged, with match_type tracked as "exact", "vector", or "both"

### Weight-Based Reranking (search.py:435-507)
Events are scored using the decay formula and filtered by:
- Hard filters: t_expired is not None OR t_invalid is not None and t_now >= t_invalid → weight = 0
- Entity match factor: Multiplied by sum of entity_match_weights to boost precise matches

## Prompt Engineering

All prompts are stored in `src/prompts/` and loaded via `utils.py:load_prompt()`. Template variables use Python `.format()` syntax:

- `extract_event_only_user.txt`: `{episode_text}` - raw conversation
- `entity_extraction_user.txt`: `{query}` - user search query
- `generate_answer_user.txt`: `{events_context}` + `{query}` - retrieved events + question

**Modify prompts directly** in `.txt` files to adjust extraction/summarization behavior.

## Database Schema

Kuzu graph database with:
- **Node Tables**: Episode, Event, Entity, User
- **Relation Tables**: INVOLVES (Event→Entity), EXTRACTED_FROM (Event→Episode), PERMANENT_TRAIT (User→Event)

Schema initialization in `db.py:init_db()` includes best-effort ALTER TABLE migrations for older databases.

## Visualization

MemoryVisualizer exports Cypher queries to `outputs/` directory for visualization in Kuzu Explorer:
- `export_event_subgraph()`: Single event with its entities
- `export_memory_graph()`: Full memory graph (optionally include episodes)

Enable via config: `EXPORT_GRAPH=true`, `EXPORT_EVERY_EPISODE=true`
