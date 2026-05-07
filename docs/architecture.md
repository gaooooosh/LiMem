# Architecture

LiMem turns raw observations into a graph of long-term memories. The current
service supports multiple users with isolated memory workspaces: each user owns
their databases, API keys carry scoped permissions, and each active database is
loaded through a bounded in-process LTM pool.

The memory engine is append-first. Online writes create new `Event` nodes first,
then evolution and consolidation link, merge, archive, or rewrite graph
structure in bounded maintenance steps.

## System Overview

```text
Browser / Agent / Client
    |
    | X-API-Key or Authorization: Bearer ...
    v
FastAPI service
    |
    +-- auth repository (SQLite)
    |      users / api_keys / database records
    |
    +-- database manager
    |      user-owned Kuzu database paths
    |
    +-- LTM pool
           loaded ResearchLTM handles
           audit writer
           BM25 index
```

| Layer | Path | Responsibility |
| --- | --- | --- |
| Core models | `src/limem/core/` | `Episode`, `Event`, `Entity`, `Context`, and public memory contracts |
| Builder | `src/limem/builder/` | LLM extraction, normalization, and persistence orchestration |
| Evolution | `src/limem/evolution/` | Context reuse, event relations, consolidation, decay, archive, and merge behavior |
| Storage | `src/limem/storage/` | Graph store abstraction and Kuzu implementation |
| Retrieval | `src/limem/retrieval/` | Lightweight BM25 index used by the HTTP service |
| Auth service | `src/service/auth/` | SQLite-backed users, API keys, database ownership, token hashing, and scope checks |
| API routers | `src/service/routers/` | Admin, user self-service, databases, memory operations, graph operations, debug UI, and SPA mount |
| Web console | `web/` | React/Vite control plane for users and admins |
| Scripts | `src/script/` | Batch imports, demos, debug pages, and visualization tools |

## User And Workspace Model

The service has three persistent service-level concepts:

- `User`: an identity that owns databases and API keys.
- `ApiKey`: a hashed token tied to one user and a scope set.
- `Database`: a Kuzu database record owned by one user.

Default paths:

```text
AUTH_DB_PATH=./DB/auth.sqlite
MULTI_DB_BASE_DIR=./DB
MULTI_AUDIT_BASE_DIR=./outputs/audit
```

Per-user runtime paths are derived from repository records:

```text
Kuzu DB:   {MULTI_DB_BASE_DIR}/users/{user_id}/{db_id}.kz
Audit log: {MULTI_AUDIT_BASE_DIR}/{user_id}/{db_id}.jsonl
```

The root key is loaded from `ROOT_API_KEY` and is not stored in SQLite. It can
access `/admin/*` and all databases. Normal user keys are stored as hashes and
can be revoked without exposing plaintext tokens.

## Permission Model

Scopes are intentionally small:

| Scope | Meaning |
| --- | --- |
| `r` | Read owned databases, query, health, stats, graph snapshots, and audit logs |
| `w` | Create/archive owned databases, ingest, evolve, rebuild indexes, and mutate graph nodes. `w` also reads |
| `admin` | Access `/admin/*` for user, key, and global database management |

User self-service routes never allow scope escalation. A user can issue a child
key only when the requested scopes are a subset of the caller's scopes.

## Memory Data Model

Primary nodes:

- `Episode`: raw observation, retained for traceability and cleanup.
- `Event`: structured memory unit extracted from an episode.
- `Entity`: explicit symbolic mention, such as a person, place, concept, or system.
- `Context`: reusable state, situation, constraint, habit, environment, or goal.
- `User`: reserved user anchor for permanent trait relations inside the memory graph.

Primary relationships:

- `EXTRACTED_FROM`: `Event -> Episode`
- `INVOLVES`: `Event -> Entity`
- `IN_REL`: `Event -> Context`
- `EVENT_RELATION`: `Event -> Event`
- `EVENT_MERGE_TRACE`: merged event lineage
- `PERMANENT_TRAIT`: `User -> Event`

## Write Path

```text
POST /db/{db_id}/ingest
    |
    v
get_caller -> check database ownership -> acquire LTM handle
    |
    v
flatten JSON payload to text
    |
    v
UnifiedExtractor -> Event + inline Context drafts
    |
    v
KuzuStore write + BM25 event update + audit trace
```

The design favors predictable online ingestion. Expensive graph cleanup is kept
in explicit maintenance endpoints so production writes do not require a full
graph pass.

## Retrieval Path

The Python API exposes `retrieve_memories(query, top_k=5)` when dynamic
evolution is enabled. The HTTP service uses a local `BM25Index` over active
events and rebuilds it after graph edits or consolidation.

```text
POST /db/{db_id}/query
    |
    v
ownership check -> BM25 active-event search -> ranked event summaries
```

For richer application behavior, call the Python API directly and combine
returned event/context graph state with your own answer generator.

## Web Console And Static UI

The React console lives in `web/` and is built by the first Docker stage. The
runtime image copies `web/dist` into:

```text
/app/src/service/static/ui
```

`src/service/routers/ui.py` mounts that directory at `/ui`. Missing static
paths fall back to `index.html` so React Router can handle deep links such as
`/ui/console/db/{db_id}`.

Top-level debug pages still exist:

- `/graph?db={db_id}&key={api_key}`
- `/logs?db={db_id}&key={api_key}`

These pages are static HTML entry points. Data access still goes through scoped
API routes under `/db/{db_id}/api/...`.
