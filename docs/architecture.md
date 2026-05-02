# Architecture

LiMem turns raw observations into a graph of long-term memories. The current implementation is append-first: online writes create new `Event` nodes first, and bounded evolution/consolidation later links, merges, archives, or rewrites graph structure.

## Components

```text
Raw JSON/Text
    |
    v
Episode
    |
    v
UnifiedExtractor
    |
    v
Event + inline Context drafts
    |
    v
KuzuStore
    |
    v
DynamicEvolutionEngine
    |
    v
Entity / Context / Event-Event relations
```

| Layer | Path | Responsibility |
| --- | --- | --- |
| Core models | `src/limem/core/` | `Episode`, `Event`, `Entity`, `Context`, and public memory contracts |
| Builder | `src/limem/builder/` | LLM extraction, normalization, and persistence orchestration |
| Evolution | `src/limem/evolution/` | Context reuse, event relations, consolidation, decay, archive, and merge behavior |
| Storage | `src/limem/storage/` | Graph store abstraction and Kuzu implementation |
| Retrieval | `src/limem/retrieval/` | Lightweight BM25 index used by the HTTP service |
| Service | `src/service/` | FastAPI service, audit logging, graph operations, and visualization endpoints |
| Scripts | `src/script/` | Batch imports, demos, debug pages, and visualization tools |

## Data Model

Primary nodes:

- `Episode`: raw observation, retained for traceability and cleanup.
- `Event`: structured memory unit extracted from an episode.
- `Entity`: explicit symbolic mention, such as a person, place, concept, or system.
- `Context`: reusable state, situation, constraint, habit, environment, or goal.
- `User`: reserved user anchor for permanent trait relations.

Primary relationships:

- `EXTRACTED_FROM`: `Event -> Episode`
- `INVOLVES`: `Event -> Entity`
- `IN_REL`: `Event -> Context`
- `EVENT_RELATION`: `Event -> Event`
- `EVENT_MERGE_TRACE`: merged event lineage
- `PERMANENT_TRAIT`: `User -> Event`

## Write Path

1. An `Episode` is created from text or flattened JSON.
2. `UnifiedExtractor` calls the configured generation model and extracts one or more events.
3. Events are normalized and written to Kuzu.
4. Participant/entity candidates are linked through `INVOLVES`.
5. The dynamic engine resolves context links and event-event relations when enabled.
6. Optional consolidation merges compatible memories and archives stale graph state.

The design favors predictable online ingestion. Expensive graph cleanup is kept in consolidation so production writes do not require a full graph pass.

## Retrieval Path

The Python API exposes `retrieve_memories(query, top_k=5)` when dynamic evolution is enabled. The HTTP service uses a local `BM25Index` over active events and rebuilds it after graph edits or consolidation.

The service retrieval path is intentionally lightweight:

```text
query text -> BM25 active-event search -> ranked event summaries
```

For richer application behavior, call the Python API directly and combine returned event/context graph state with your own answer generator.

