"""SQLite persistence for L2 entities, memory items, and evidence."""

from collections.abc import Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import struct
from typing import Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

import jieba
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    JsonValue,
    ValidationError,
)

from limem.episode_builder import EpisodeInput
from limem.memory_item_extractor import ExtractionItem, L2Result
from limem.raw_data_parser import RawEvent, SessionInput

from .models import (
    ConsolidationSupport,
    Entity,
    EntityIdentityConfirmation,
    EntityRef,
    Evidence,
    LexicalSearchHit,
    MemoryEmbedding,
    MemoryItem,
    MemoryRelation,
    PendingConsolidationSubject,
    Profile,
    SourceSession,
    WriteItem,
    WriteResult,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_session (
    session_id TEXT PRIMARY KEY,
    source_format TEXT NOT NULL,
    raw_session_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    external_ref TEXT UNIQUE,
    aliases_json TEXT NOT NULL,
    types_json TEXT NOT NULL,
    canonical_entity_id TEXT REFERENCES entity(id),
    scope_episode_id TEXT,
    scope_text TEXT,
    UNIQUE (scope_episode_id, scope_text),
    CHECK (
        (scope_episode_id IS NULL AND scope_text IS NULL)
        OR (scope_episode_id IS NOT NULL AND scope_text IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS profile (
    entity_id TEXT PRIMARY KEY,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_item (
    id TEXT PRIMARY KEY,
    subject_entity_id TEXT NOT NULL REFERENCES entity(id),
    statement TEXT NOT NULL,
    condition TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'candidate', 'inactive')),
    content_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS memory_relation (
    memory_item_id TEXT NOT NULL REFERENCES memory_item(id),
    position INTEGER NOT NULL,
    predicate TEXT NOT NULL,
    target_text TEXT,
    target_entity_id TEXT REFERENCES entity(id),
    PRIMARY KEY (memory_item_id, position)
);

CREATE TABLE IF NOT EXISTS memory_entity_ref (
    memory_item_id TEXT NOT NULL REFERENCES memory_item(id),
    position INTEGER NOT NULL,
    text TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entity(id),
    PRIMARY KEY (memory_item_id, position)
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    memory_item_id TEXT NOT NULL REFERENCES memory_item(id),
    support_key TEXT NOT NULL UNIQUE,
    source_session_id TEXT NOT NULL,
    raw_event_ids_json TEXT NOT NULL,
    context TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consolidation_support (
    consolidator_type TEXT NOT NULL,
    candidate_memory_item_id TEXT NOT NULL REFERENCES memory_item(id),
    source_evidence_id TEXT NOT NULL REFERENCES evidence(id),
    fragments_json TEXT NOT NULL,
    PRIMARY KEY (candidate_memory_item_id, source_evidence_id)
);

CREATE TABLE IF NOT EXISTS memory_embedding (
    memory_item_id TEXT PRIMARY KEY REFERENCES memory_item(id),
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    vector BLOB NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_item_fts USING fts5(
    memory_item_id UNINDEXED,
    document
);

CREATE INDEX IF NOT EXISTS memory_item_subject_idx
ON memory_item(subject_entity_id);

CREATE INDEX IF NOT EXISTS memory_entity_ref_entity_idx
ON memory_entity_ref(entity_id);

CREATE INDEX IF NOT EXISTS memory_relation_target_entity_idx
ON memory_relation(target_entity_id);

CREATE INDEX IF NOT EXISTS evidence_memory_item_idx
ON evidence(memory_item_id);

CREATE INDEX IF NOT EXISTS consolidation_support_source_idx
ON consolidation_support(source_evidence_id);
"""


class _EmbeddingSettings(Protocol):
    model: str
    dimension: int


class _DocumentEmbeddingClient(Protocol):
    settings: _EmbeddingSettings

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...


class _PreparedEmbedding(BaseModel):
    """One strictly validated document vector ready for persistence."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    model: str = Field(min_length=1, pattern=r"\S")
    dimensions: int = Field(gt=0)
    vector: list[FiniteFloat] = Field(min_length=1)


class EmbeddingValidationError(ValueError):
    """An embedding response cannot be stored with the declared settings."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _memory_text(statement: str, condition: str | None) -> str:
    return "\n".join(value for value in (statement, condition) if value is not None)


def _tokens(text: str) -> list[str]:
    return [token.strip() for token in jieba.lcut_for_search(text) if token.strip()]


class MemoryStore:
    """Persist source Sessions and their extracted memory in SQLite."""

    def __init__(self, database_path: str | Path) -> None:
        self._connection = sqlite3.connect(database_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(_SCHEMA)
        support_columns = {
            row["name"]
            for row in self._connection.execute(
                "PRAGMA table_info(consolidation_support)"
            )
        }
        if "consolidator_type" not in support_columns:
            self._connection.close()
            raise RuntimeError(
                "MemoryStore schema is incompatible: consolidation_support "
                "is missing consolidator_type"
            )

    def close(self) -> None:
        self._connection.close()

    def write(
        self,
        session: SessionInput,
        episode: EpisodeInput,
        result: L2Result,
    ) -> WriteResult:
        self._require_episode_belongs_to_session(session, episode)
        with self._connection:
            return self._write_l2_result(session, episode, result)

    def write_complete_session(
        self,
        raw_session_json: dict[str, JsonValue],
        session: SessionInput,
        episode: EpisodeInput,
        result: L2Result,
        embedding_client: _DocumentEmbeddingClient,
        *,
        source_format: str,
    ) -> WriteResult:
        """Atomically store one source Session and its retrieval-ready memory."""

        source_session = SourceSession(
            session_id=session.session_ref,
            source_format=source_format,
            raw_session_json=raw_session_json,
            created_at=datetime.now(timezone.utc),
        )
        self._require_episode_belongs_to_session(session, episode)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO source_session(
                    session_id, source_format, raw_session_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    source_session.session_id,
                    source_session.source_format,
                    _json(source_session.raw_session_json),
                    source_session.created_at.isoformat(),
                ),
            )
            written = self._write_l2_result(session, episode, result)
            memory_item_ids = list(
                dict.fromkeys(item.memory_item_id for item in written.items)
            )
            items = self.get_memory_items(memory_item_ids)
            prepared_embeddings = self._prepare_embeddings(
                self._embedding_texts(items),
                memory_item_ids,
                embedding_client,
            )
            self._write_prepared_embeddings(
                memory_item_ids,
                prepared_embeddings,
            )
        return written

    def get_source_session(self, session_id: str) -> SourceSession:
        row = self._connection.execute(
            "SELECT * FROM source_session WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return SourceSession(
            session_id=row["session_id"],
            source_format=row["source_format"],
            raw_session_json=json.loads(row["raw_session_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def get_entity(self, entity_id: str) -> Entity:
        row = self._connection.execute(
            "SELECT * FROM entity WHERE id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            raise KeyError(entity_id)
        return self._entity_from_row(row)

    def list_entities(self) -> list[Entity]:
        rows = self._connection.execute("SELECT * FROM entity ORDER BY id").fetchall()
        return [self._entity_from_row(row) for row in rows]

    def put_profile(self, entity_id: str, text: str) -> Profile:
        profile = Profile(entity_id=entity_id, text=text)
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO profile (entity_id, text)
                VALUES (?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET text = excluded.text
                """,
                (profile.entity_id, profile.text),
            )
        return profile

    def get_profile(self, entity_id: str) -> Profile:
        row = self._connection.execute(
            "SELECT entity_id, text FROM profile WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            raise KeyError(entity_id)
        return Profile(entity_id=row["entity_id"], text=row["text"])

    @staticmethod
    def _entity_from_row(row: sqlite3.Row) -> Entity:
        return Entity(
            id=row["id"],
            name=row["name"],
            external_ref=row["external_ref"],
            aliases=json.loads(row["aliases_json"]),
            types=json.loads(row["types_json"]),
            canonical_entity_id=row["canonical_entity_id"],
            scope_text=row["scope_text"],
        )

    def get_memory_item(self, memory_item_id: str) -> MemoryItem:
        items = self.get_memory_items([memory_item_id])
        if not items:
            raise KeyError(memory_item_id)
        return items[0]

    def list_memory_items(self) -> list[MemoryItem]:
        rows = self._connection.execute(
            "SELECT id FROM memory_item ORDER BY id"
        ).fetchall()
        return self.get_memory_items([row["id"] for row in rows])

    def get_active_direct_memory_items(
        self,
        subject_entity_id: str,
    ) -> list[MemoryItem]:
        """Return active direct claims owned by one confirmed canonical subject."""

        subject = self.get_entity(subject_entity_id)
        if subject.canonical_entity_id != subject.id:
            raise ValueError(
                "consolidation subject must be a confirmed canonical Entity: "
                f"{subject_entity_id}"
            )
        rows = self._connection.execute(
            """
            SELECT memory_item.id
            FROM memory_item
            JOIN entity
              ON entity.id = memory_item.subject_entity_id
            WHERE entity.canonical_entity_id = ?
              AND memory_item.status = 'active'
            ORDER BY memory_item.id
            """,
            (subject_entity_id,),
        ).fetchall()
        return self.get_memory_items([row["id"] for row in rows])

    def get_adjacent_memory_items(
        self,
        entity_ids: Sequence[str],
    ) -> list[MemoryItem]:
        """Return items that use any Entity as subject, target, or mention."""

        unique_entity_ids = list(dict.fromkeys(entity_ids))
        if not unique_entity_ids:
            return []
        placeholders = ",".join("?" for _ in unique_entity_ids)
        rows = self._connection.execute(
            f"""
            SELECT id AS memory_item_id
            FROM memory_item
            WHERE subject_entity_id IN ({placeholders})
            UNION
            SELECT memory_item_id
            FROM memory_relation
            WHERE target_entity_id IN ({placeholders})
            UNION
            SELECT memory_item_id
            FROM memory_entity_ref
            WHERE entity_id IN ({placeholders})
            ORDER BY memory_item_id
            """,
            unique_entity_ids * 3,
        ).fetchall()
        return self.get_memory_items([row["memory_item_id"] for row in rows])

    def get_memory_items(self, memory_item_ids: Sequence[str]) -> list[MemoryItem]:
        """Load existing items in first-occurrence input order."""

        unique_ids = list(dict.fromkeys(memory_item_ids))
        if not unique_ids:
            return []
        placeholders = ",".join("?" for _ in unique_ids)
        item_rows = self._connection.execute(
            f"SELECT * FROM memory_item WHERE id IN ({placeholders})",
            unique_ids,
        ).fetchall()
        rows_by_id = {row["id"]: row for row in item_rows}
        relation_rows = self._connection.execute(
            f"""
            SELECT memory_item_id, predicate, target_text, target_entity_id
            FROM memory_relation
            WHERE memory_item_id IN ({placeholders})
            ORDER BY memory_item_id, position
            """,
            unique_ids,
        ).fetchall()
        entity_ref_rows = self._connection.execute(
            f"""
            SELECT memory_item_id, text, entity_id
            FROM memory_entity_ref
            WHERE memory_item_id IN ({placeholders})
            ORDER BY memory_item_id, position
            """,
            unique_ids,
        ).fetchall()
        relations_by_id: dict[str, list[MemoryRelation]] = {
            memory_item_id: [] for memory_item_id in unique_ids
        }
        refs_by_id: dict[str, list[EntityRef]] = {
            memory_item_id: [] for memory_item_id in unique_ids
        }
        for row in relation_rows:
            relations_by_id[row["memory_item_id"]].append(
                MemoryRelation(
                    predicate=row["predicate"],
                    target=row["target_text"],
                    target_entity_id=row["target_entity_id"],
                )
            )
        for row in entity_ref_rows:
            refs_by_id[row["memory_item_id"]].append(
                EntityRef(text=row["text"], entity_id=row["entity_id"])
            )
        return [
            MemoryItem(
                id=rows_by_id[memory_item_id]["id"],
                subject_entity_id=rows_by_id[memory_item_id]["subject_entity_id"],
                relations=relations_by_id[memory_item_id],
                statement=rows_by_id[memory_item_id]["statement"],
                condition=rows_by_id[memory_item_id]["condition"],
                entity_refs=refs_by_id[memory_item_id],
                status=rows_by_id[memory_item_id]["status"],
            )
            for memory_item_id in unique_ids
            if memory_item_id in rows_by_id
        ]

    def search_lexical(
        self,
        query: str | None,
        context: str | None,
        limit: int,
    ) -> list[LexicalSearchHit]:
        """Search statement and condition terms with FTS5 OR semantics."""

        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        for name, value in (("query", query), ("context", context)):
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be nonblank when provided")
        tokens = list(
            dict.fromkeys(
                _tokens(
                    "\n".join(value for value in (query, context) if value is not None)
                )
            )
        )
        if not tokens:
            raise ValueError("query and context cannot both be null")
        match_expression = " OR ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens
        )
        rows = self._connection.execute(
            """
            SELECT memory_item_id, bm25(memory_item_fts) AS bm25_score
            FROM memory_item_fts
            WHERE memory_item_fts MATCH ?
            ORDER BY bm25_score, memory_item_id
            LIMIT ?
            """,
            (match_expression, limit),
        ).fetchall()
        return [
            LexicalSearchHit(
                memory_item_id=row["memory_item_id"],
                bm25_score=row["bm25_score"],
            )
            for row in rows
        ]

    def get_direct_evidence(self, memory_item_id: str) -> list[Evidence]:
        """Read the independent occurrences supporting one MemoryItem."""
        rows = self._connection.execute(
            "SELECT * FROM evidence WHERE memory_item_id = ? ORDER BY id",
            (memory_item_id,),
        ).fetchall()
        return [self._evidence_from_row(row) for row in rows]

    def get_evidence(self, evidence_id: str) -> Evidence:
        row = self._connection.execute(
            "SELECT * FROM evidence WHERE id = ?",
            (evidence_id,),
        ).fetchone()
        if row is None:
            raise KeyError(evidence_id)
        return self._evidence_from_row(row)

    def count_direct_evidence(
        self,
        memory_item_ids: Sequence[str],
    ) -> dict[str, int]:
        """Return each requested MemoryItem's current Direct Evidence count."""

        unique_ids = list(dict.fromkeys(memory_item_ids))
        if not unique_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self._connection.execute(
            f"""
            SELECT memory_item_id, COUNT(*) AS evidence_count
            FROM evidence
            WHERE memory_item_id IN ({placeholders})
            GROUP BY memory_item_id
            """,
            unique_ids,
        ).fetchall()
        counts = {row["memory_item_id"]: row["evidence_count"] for row in rows}
        return {memory_item_id: counts.get(memory_item_id, 0) for memory_item_id in unique_ids}

    def get_unconsolidated_evidence(
        self,
        memory_item_ids: Sequence[str],
        *,
        consolidator_type: str,
    ) -> list[Evidence]:
        """Return Evidence unused by one consolidator in requested item order."""

        if not consolidator_type.strip():
            raise ValueError("consolidator_type cannot be blank")
        unique_ids = list(dict.fromkeys(memory_item_ids))
        if not unique_ids:
            return []
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self._connection.execute(
            f"""
            SELECT evidence.*
            FROM evidence
            WHERE evidence.memory_item_id IN ({placeholders})
              AND NOT EXISTS (
                  SELECT 1
                  FROM consolidation_support
                  WHERE consolidation_support.source_evidence_id = evidence.id
                    AND consolidation_support.consolidator_type = ?
              )
            ORDER BY evidence.id
            """,
            [*unique_ids, consolidator_type],
        ).fetchall()
        evidence_by_memory_item: dict[str, list[Evidence]] = {
            memory_item_id: [] for memory_item_id in unique_ids
        }
        for row in rows:
            evidence_by_memory_item[row["memory_item_id"]].append(
                self._evidence_from_row(row)
            )
        return [
            evidence
            for memory_item_id in unique_ids
            for evidence in evidence_by_memory_item[memory_item_id]
        ]

    def list_pending_consolidation_subjects(
        self,
        consolidator_type: str,
    ) -> list[PendingConsolidationSubject]:
        """Count active direct Evidence pending for each canonical subject."""

        if not consolidator_type.strip():
            raise ValueError("consolidator_type cannot be blank")
        rows = self._connection.execute(
            """
            SELECT
                entity.canonical_entity_id AS subject_entity_id,
                COUNT(evidence.id) AS evidence_count
            FROM entity
            JOIN memory_item
              ON memory_item.subject_entity_id = entity.id
            JOIN evidence
              ON evidence.memory_item_id = memory_item.id
            WHERE entity.canonical_entity_id IS NOT NULL
              AND memory_item.status = 'active'
              AND NOT EXISTS (
                  SELECT 1
                  FROM consolidation_support
                  WHERE consolidation_support.source_evidence_id = evidence.id
                    AND consolidation_support.consolidator_type = ?
              )
            GROUP BY entity.canonical_entity_id
            ORDER BY evidence_count DESC, entity.canonical_entity_id
            """,
            (consolidator_type,),
        ).fetchall()
        return [
            PendingConsolidationSubject(
                subject_entity_id=row["subject_entity_id"],
                evidence_count=row["evidence_count"],
            )
            for row in rows
        ]

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> Evidence:
        return Evidence(
            id=row["id"],
            memory_item_id=row["memory_item_id"],
            source_session_id=row["source_session_id"],
            raw_event_ids=json.loads(row["raw_event_ids_json"]),
            context=row["context"],
        )

    def get_consolidation_supports(
        self,
        candidate_memory_item_id: str,
    ) -> list[ConsolidationSupport]:
        rows = self._connection.execute(
            """
            SELECT *
            FROM consolidation_support
            WHERE candidate_memory_item_id = ?
            ORDER BY source_evidence_id
            """,
            (candidate_memory_item_id,),
        ).fetchall()
        return [self._consolidation_support_from_row(row) for row in rows]

    def get_consolidation_supports_for_evidence(
        self,
        source_evidence_id: str,
    ) -> list[ConsolidationSupport]:
        rows = self._connection.execute(
            """
            SELECT *
            FROM consolidation_support
            WHERE source_evidence_id = ?
            ORDER BY candidate_memory_item_id
            """,
            (source_evidence_id,),
        ).fetchall()
        return [self._consolidation_support_from_row(row) for row in rows]

    def get_consolidation_source_memory_items(
        self,
        candidate_memory_item_id: str,
    ) -> list[MemoryItem]:
        rows = self._connection.execute(
            """
            SELECT DISTINCT evidence.memory_item_id
            FROM consolidation_support
            JOIN evidence
              ON evidence.id = consolidation_support.source_evidence_id
            WHERE consolidation_support.candidate_memory_item_id = ?
            ORDER BY evidence.memory_item_id
            """,
            (candidate_memory_item_id,),
        ).fetchall()
        return self.get_memory_items([row["memory_item_id"] for row in rows])

    def get_consolidation_candidate_source_memory_item_ids(
        self,
        source_memory_item_ids: Sequence[str],
    ) -> dict[str, list[str]]:
        """Map supported candidate IDs to matching source IDs in input order."""

        unique_ids = list(dict.fromkeys(source_memory_item_ids))
        if not unique_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self._connection.execute(
            f"""
            SELECT DISTINCT
                consolidation_support.candidate_memory_item_id,
                evidence.memory_item_id
            FROM consolidation_support
            JOIN evidence
              ON evidence.id = consolidation_support.source_evidence_id
            WHERE evidence.memory_item_id IN ({placeholders})
            ORDER BY consolidation_support.candidate_memory_item_id,
                     evidence.memory_item_id
            """,
            unique_ids,
        ).fetchall()
        source_ids_by_candidate: dict[str, set[str]] = {}
        for row in rows:
            source_ids_by_candidate.setdefault(
                row["candidate_memory_item_id"], set()
            ).add(row["memory_item_id"])
        return {
            candidate_memory_item_id: [
                source_memory_item_id
                for source_memory_item_id in unique_ids
                if source_memory_item_id in source_ids
            ]
            for candidate_memory_item_id, source_ids in source_ids_by_candidate.items()
        }

    @staticmethod
    def _consolidation_support_from_row(
        row: sqlite3.Row,
    ) -> ConsolidationSupport:
        return ConsolidationSupport(
            consolidator_type=row["consolidator_type"],
            candidate_memory_item_id=row["candidate_memory_item_id"],
            source_evidence_id=row["source_evidence_id"],
            fragments=json.loads(row["fragments_json"]),
        )

    def write_consolidation(
        self,
        candidates: Sequence[MemoryItem],
        supports: Sequence[ConsolidationSupport],
        embeddings: Sequence[MemoryEmbedding],
        *,
        placeholder_entities: Sequence[Entity] = (),
        identity_confirmations: Sequence[EntityIdentityConfirmation] = (),
    ) -> None:
        """Atomically persist one validated consolidation batch."""

        candidate_ids = [candidate.id for candidate in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate MemoryItem IDs must be unique")
        for candidate in candidates:
            if candidate.status != "candidate":
                raise ValueError(
                    f"consolidated MemoryItem must have candidate status: {candidate.id}"
                )

        candidate_id_set = set(candidate_ids)
        support_types_by_candidate = {
            candidate_id: set() for candidate_id in candidate_ids
        }
        for support in supports:
            if support.candidate_memory_item_id not in candidate_id_set:
                raise ValueError(
                    "ConsolidationSupport references a candidate outside this batch: "
                    f"{support.candidate_memory_item_id}"
                )
            support_types_by_candidate[support.candidate_memory_item_id].add(
                support.consolidator_type
            )
        for candidate_id, consolidator_types in support_types_by_candidate.items():
            if not consolidator_types:
                raise ValueError(
                    f"consolidated candidate has no support: {candidate_id}"
                )
            if len(consolidator_types) != 1:
                raise ValueError(
                    "consolidated candidate has mixed consolidator types: "
                    f"{candidate_id}"
                )
        for entity in placeholder_entities:
            if (
                entity.external_ref is not None
                or entity.canonical_entity_id is not None
                or entity.scope_text is not None
            ):
                raise ValueError(
                    "consolidation placeholder Entity must be unresolved and unscoped: "
                    f"{entity.id}"
                )

        prepared_embeddings = self._prepare_precomputed_embeddings(
            candidate_ids,
            embeddings,
        )
        with self._connection:
            confirmed_ids: set[str] = set()
            for confirmation in identity_confirmations:
                canonical_id = confirmation.entity_ids[0]
                for entity_id in confirmation.entity_ids:
                    if entity_id in confirmed_ids:
                        raise ValueError("identity confirmations must have distinct members")
                    entity = self.get_entity(entity_id)
                    if (
                        entity.canonical_entity_id is not None
                        or entity.external_ref is not None
                    ):
                        raise ValueError("identity confirmation requires unresolved Entities")
                    confirmed_ids.add(entity_id)
                    self._connection.execute(
                        "UPDATE entity SET canonical_entity_id = ? WHERE id = ?",
                        (canonical_id, entity_id),
                    )
            self._insert_consolidation_entities(placeholder_entities)
            for candidate in candidates:
                self._insert_memory_item(candidate)
            self._connection.executemany(
                """
                INSERT INTO consolidation_support(
                    consolidator_type, candidate_memory_item_id,
                    source_evidence_id, fragments_json
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        support.consolidator_type,
                        support.candidate_memory_item_id,
                        support.source_evidence_id,
                        _json(support.fragments),
                    )
                    for support in supports
                ],
            )
            for candidate_id in candidate_ids:
                self._index_memory_item_fts(candidate_id)
            self._write_prepared_embeddings(candidate_ids, prepared_embeddings)

    def index_embeddings(
        self,
        memory_item_ids: Sequence[str],
        client: _DocumentEmbeddingClient,
    ) -> None:
        """Compute all document vectors before storing them in one transaction."""

        unique_ids = list(dict.fromkeys(memory_item_ids))
        items = self.get_memory_items(unique_ids)
        found_ids = {item.id for item in items}
        for memory_item_id in unique_ids:
            if memory_item_id not in found_ids:
                raise KeyError(memory_item_id)

        prepared_embeddings = self._prepare_embeddings(
            self._embedding_texts(items),
            [item.id for item in items],
            client,
        )
        with self._connection:
            self._write_prepared_embeddings(
                [item.id for item in items],
                prepared_embeddings,
            )

    def get_embeddings(
        self,
        memory_item_ids: Sequence[str],
    ) -> list[MemoryEmbedding]:
        """Load existing embeddings in first-occurrence input order."""

        unique_ids = list(dict.fromkeys(memory_item_ids))
        if not unique_ids:
            return []
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self._connection.execute(
            f"SELECT * FROM memory_embedding WHERE memory_item_id IN ({placeholders})",
            unique_ids,
        ).fetchall()
        rows_by_id = {row["memory_item_id"]: row for row in rows}
        return [
            self._embedding_from_row(rows_by_id[memory_item_id])
            for memory_item_id in unique_ids
            if memory_item_id in rows_by_id
        ]

    @staticmethod
    def _embedding_from_row(row: sqlite3.Row) -> MemoryEmbedding:
        dimensions = row["dimensions"]
        return MemoryEmbedding(
            memory_item_id=row["memory_item_id"],
            model=row["model"],
            dimensions=dimensions,
            vector=list(struct.unpack(f"<{dimensions}f", row["vector"])),
        )

    @staticmethod
    def _require_episode_belongs_to_session(
        session: SessionInput,
        episode: EpisodeInput,
    ) -> None:
        expected_episode_id = f"{session.session_ref}:episode:0"
        if episode.episode_id != expected_episode_id:
            raise ValueError(
                f"EpisodeInput {episode.episode_id!r} does not belong to "
                f"SessionInput {session.session_ref!r}"
            )

    def _write_l2_result(
        self,
        session: SessionInput,
        episode: EpisodeInput,
        result: L2Result,
    ) -> WriteResult:
        session_events = {event.raw_event_id: event for event in session.raw_events}
        episode_event_ids = {event.raw_event_id for event in episode.events}
        written: list[WriteItem] = []

        for position, item in enumerate(result.items):
            self._require_evidence_members(
                item,
                session_events=session_events,
                episode_event_ids=episode_event_ids,
            )
            subject_entity_id = self._resolve_subject(
                item,
                episode_id=episode.episode_id,
                session_events=session_events,
            )
            target_texts = {
                relation.target
                for relation in item.relations
                if relation.target is not None
            }
            target_entity_ids: dict[str, str] = {}
            entity_refs: list[EntityRef] = []
            for mention in item.entity_mentions:
                entity_id = self._resolve_placeholder(episode.episode_id, mention)
                if mention in target_texts:
                    target_entity_ids[mention] = entity_id
                else:
                    entity_refs.append(EntityRef(text=mention, entity_id=entity_id))
            memory_item_id = self._write_memory_item(
                item,
                subject_entity_id=subject_entity_id,
                entity_refs=entity_refs,
                target_entity_ids=target_entity_ids,
            )
            evidence_id = self._write_evidence(
                episode_id=episode.episode_id,
                source_session_id=session.session_ref,
                item_position=position,
                item=item,
                memory_item_id=memory_item_id,
            )
            self._index_memory_item_fts(memory_item_id)
            written.append(
                WriteItem(
                    subject_entity_id=subject_entity_id,
                    memory_item_id=memory_item_id,
                    evidence_id=evidence_id,
                )
            )

        return WriteResult(items=written)

    def _prepare_embeddings(
        self,
        texts: list[str],
        labels: list[str],
        client: _DocumentEmbeddingClient,
    ) -> list[tuple[_PreparedEmbedding, bytes]]:
        if not texts:
            return []
        vectors = client.embed_documents(texts)
        if len(vectors) != len(texts):
            raise EmbeddingValidationError(
                "EmbeddingClient returned an unexpected vector count: "
                f"expected {len(texts)}, got {len(vectors)}"
            )

        prepared: list[tuple[_PreparedEmbedding, bytes]] = []
        for position, vector in enumerate(vectors):
            try:
                embedding = _PreparedEmbedding(
                    model=client.settings.model,
                    dimensions=client.settings.dimension,
                    vector=vector,
                )
            except ValidationError as exc:
                raise EmbeddingValidationError(str(exc)) from exc
            if len(embedding.vector) != embedding.dimensions:
                raise EmbeddingValidationError(
                    "EmbeddingClient returned an unexpected dimension for "
                    f"{labels[position]}: expected {embedding.dimensions}, "
                    f"got {len(embedding.vector)}"
                )
            if not any(embedding.vector):
                raise EmbeddingValidationError(
                    f"EmbeddingClient returned a zero vector for {labels[position]}"
                )
            try:
                packed = struct.pack(
                    f"<{embedding.dimensions}f",
                    *embedding.vector,
                )
            except (OverflowError, struct.error) as exc:
                raise EmbeddingValidationError(str(exc)) from exc
            prepared.append((embedding, packed))
        return prepared

    def _embedding_texts(self, items: Sequence[MemoryItem]) -> list[str]:
        if not items:
            return []
        item_ids = [item.id for item in items]
        placeholders = ",".join("?" for _ in item_ids)
        rows = self._connection.execute(
            f"""
            SELECT memory_item_id, context
            FROM evidence
            WHERE memory_item_id IN ({placeholders})
            ORDER BY memory_item_id, id
            """,
            item_ids,
        ).fetchall()
        contexts_by_id: dict[str, list[str]] = {item_id: [] for item_id in item_ids}
        for row in rows:
            contexts_by_id[row["memory_item_id"]].append(row["context"])
        return [
            "\n".join([_memory_text(item.statement, item.condition), contexts[0]])
            if len(contexts := contexts_by_id[item.id]) == 1
            else _memory_text(item.statement, item.condition)
            for item in items
        ]

    def _prepare_precomputed_embeddings(
        self,
        candidate_ids: list[str],
        embeddings: Sequence[MemoryEmbedding],
    ) -> list[tuple[_PreparedEmbedding, bytes]]:
        embedding_ids = [embedding.memory_item_id for embedding in embeddings]
        if len(embedding_ids) != len(set(embedding_ids)):
            raise ValueError("embedding MemoryItem IDs must be unique")
        if set(embedding_ids) != set(candidate_ids):
            raise ValueError(
                "precomputed embeddings must match the consolidation candidates"
            )

        embeddings_by_id = {
            embedding.memory_item_id: embedding for embedding in embeddings
        }
        prepared: list[tuple[_PreparedEmbedding, bytes]] = []
        for candidate_id in candidate_ids:
            source = embeddings_by_id[candidate_id]
            embedding = _PreparedEmbedding(
                model=source.model,
                dimensions=source.dimensions,
                vector=source.vector,
            )
            if len(embedding.vector) != embedding.dimensions:
                raise EmbeddingValidationError(
                    "precomputed embedding has an unexpected dimension for "
                    f"{candidate_id}: expected {embedding.dimensions}, "
                    f"got {len(embedding.vector)}"
                )
            if not any(embedding.vector):
                raise EmbeddingValidationError(
                    f"precomputed embedding is a zero vector for {candidate_id}"
                )
            try:
                packed = struct.pack(
                    f"<{embedding.dimensions}f",
                    *embedding.vector,
                )
            except (OverflowError, struct.error) as exc:
                raise EmbeddingValidationError(str(exc)) from exc
            prepared.append((embedding, packed))
        return prepared

    def _write_prepared_embeddings(
        self,
        memory_item_ids: Sequence[str],
        prepared_embeddings: list[tuple[_PreparedEmbedding, bytes]],
    ) -> None:
        rows = [
            (memory_item_id, embedding.model, embedding.dimensions, packed)
            for memory_item_id, (embedding, packed) in zip(
                memory_item_ids,
                prepared_embeddings,
                strict=True,
            )
        ]
        self._connection.executemany(
            """
            INSERT INTO memory_embedding(memory_item_id, model, dimensions, vector)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(memory_item_id) DO UPDATE SET
                model = excluded.model,
                dimensions = excluded.dimensions,
                vector = excluded.vector
            """,
            rows,
        )

    def _require_evidence_members(
        self,
        item: ExtractionItem,
        *,
        session_events: dict[str, RawEvent],
        episode_event_ids: set[str],
    ) -> None:
        for raw_event_id in item.raw_event_ids:
            if raw_event_id not in episode_event_ids:
                raise ValueError(f"raw_event_id is not in EpisodeInput: {raw_event_id}")
            if raw_event_id not in session_events:
                raise ValueError(f"raw_event_id is not in SessionInput: {raw_event_id}")

    def _resolve_subject(
        self,
        item: ExtractionItem,
        *,
        episode_id: str,
        session_events: dict[str, RawEvent],
    ) -> str:
        prefix = "用户 "
        if not item.subject.startswith(prefix):
            return self._resolve_placeholder(episode_id, item.subject)

        external_ref = item.subject.removeprefix(prefix)
        if not external_ref:
            raise ValueError("user subject must include an external reference")
        for raw_event_id in item.raw_event_ids:
            if session_events[raw_event_id].user_ref != external_ref:
                raise ValueError(
                    f"subject {item.subject!r} does not match user_ref in {raw_event_id}"
                )
        row = self._connection.execute(
            "SELECT id FROM entity WHERE external_ref = ?",
            (external_ref,),
        ).fetchone()
        if row is not None:
            return row["id"]

        entity_id = str(uuid4())
        self._connection.execute(
            """
            INSERT INTO entity(
                id, name, external_ref, aliases_json, types_json, canonical_entity_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entity_id, item.subject, external_ref, "[]", '["Person"]', entity_id),
        )
        return entity_id

    def _resolve_placeholder(self, episode_id: str, text: str) -> str:
        name = f"{episode_id}中的{text}"
        row = self._connection.execute(
            """
            SELECT id FROM entity
            WHERE scope_episode_id = ? AND scope_text = ?
            """,
            (episode_id, text),
        ).fetchone()
        if row is not None:
            return row["id"]

        entity_id = str(uuid4())
        self._connection.execute(
            """
            INSERT INTO entity(
                id, name, external_ref, aliases_json, types_json, canonical_entity_id,
                scope_episode_id, scope_text
            ) VALUES (?, ?, NULL, '[]', '[]', NULL, ?, ?)
            """,
            (entity_id, name, episode_id, text),
        )
        return entity_id

    def _insert_consolidation_entities(
        self,
        entities: Sequence[Entity],
    ) -> None:
        self._connection.executemany(
            """
            INSERT INTO entity(
                id, name, external_ref, aliases_json, types_json, canonical_entity_id,
                scope_episode_id, scope_text
            ) VALUES (?, ?, NULL, ?, ?, NULL, NULL, NULL)
            """,
            [
                (
                    entity.id,
                    entity.name,
                    _json(entity.aliases),
                    _json(entity.types),
                )
                for entity in entities
            ],
        )

    def _insert_memory_item(self, item: MemoryItem) -> None:
        content_key = _json(
            {
                "subject_entity_id": item.subject_entity_id,
                "relations": [
                    relation.model_dump(mode="json") for relation in item.relations
                ],
                "statement": item.statement,
                "condition": item.condition,
                "entity_refs": [
                    entity_ref.model_dump(mode="json")
                    for entity_ref in item.entity_refs
                ],
                "status": item.status,
            }
        )
        self._connection.execute(
            """
            INSERT INTO memory_item(
                id, subject_entity_id, statement, condition, status, content_key
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.subject_entity_id,
                item.statement,
                item.condition,
                item.status,
                content_key,
            ),
        )
        self._connection.executemany(
            """
            INSERT INTO memory_relation(
                memory_item_id, position, predicate, target_text, target_entity_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    item.id,
                    position,
                    relation.predicate,
                    relation.target,
                    relation.target_entity_id,
                )
                for position, relation in enumerate(item.relations)
            ],
        )
        self._connection.executemany(
            """
            INSERT INTO memory_entity_ref(memory_item_id, position, text, entity_id)
            VALUES (?, ?, ?, ?)
            """,
            [
                (item.id, position, entity_ref.text, entity_ref.entity_id)
                for position, entity_ref in enumerate(item.entity_refs)
            ],
        )

    def _write_memory_item(
        self,
        item: ExtractionItem,
        *,
        subject_entity_id: str,
        entity_refs: list[EntityRef],
        target_entity_ids: dict[str, str],
    ) -> str:
        relations = [
            {
                "predicate": relation.predicate,
                "target": relation.target,
                "target_entity_id": target_entity_ids.get(relation.target),
            }
            for relation in item.relations
        ]
        content = {
            "subject_entity_id": subject_entity_id,
            "relations": relations,
            "statement": item.statement,
            "condition": item.condition,
            "entity_refs": [value.model_dump(mode="json") for value in entity_refs],
            "status": "active",
        }
        content_key = _json(content)
        row = self._connection.execute(
            "SELECT id FROM memory_item WHERE content_key = ?",
            (content_key,),
        ).fetchone()
        if row is not None:
            return row["id"]

        memory_item_id = str(uuid4())
        self._connection.execute(
            """
            INSERT INTO memory_item(
                id, subject_entity_id, statement, condition, status, content_key
            ) VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (
                memory_item_id,
                subject_entity_id,
                item.statement,
                item.condition,
                content_key,
            ),
        )
        self._connection.executemany(
            """
            INSERT INTO memory_relation(
                memory_item_id, position, predicate, target_text, target_entity_id
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    memory_item_id,
                    position,
                    relation.predicate,
                    relation.target,
                    target_entity_ids.get(relation.target),
                )
                for position, relation in enumerate(item.relations)
            ],
        )
        self._connection.executemany(
            """
            INSERT INTO memory_entity_ref(memory_item_id, position, text, entity_id)
            VALUES (?, ?, ?, ?)
            """,
            [
                (memory_item_id, position, value.text, value.entity_id)
                for position, value in enumerate(entity_refs)
            ],
        )
        return memory_item_id

    def _write_evidence(
        self,
        *,
        episode_id: str,
        source_session_id: str,
        item_position: int,
        item: ExtractionItem,
        memory_item_id: str,
    ) -> str:
        support_key = _json(
            {
                "episode_id": episode_id,
                "item_position": item_position,
                "item": item.model_dump(mode="json"),
            }
        )
        evidence_id = str(uuid5(NAMESPACE_URL, support_key))
        row = self._connection.execute(
            "SELECT id FROM evidence WHERE support_key = ?",
            (support_key,),
        ).fetchone()
        if row is not None:
            return row["id"]

        self._connection.execute(
            """
            INSERT INTO evidence(
                id, memory_item_id, support_key, source_session_id,
                raw_event_ids_json, context
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                memory_item_id,
                support_key,
                source_session_id,
                _json(item.raw_event_ids),
                item.context,
            ),
        )
        return evidence_id

    def _index_memory_item_fts(self, memory_item_id: str) -> None:
        row = self._connection.execute(
            "SELECT statement, condition FROM memory_item WHERE id = ?",
            (memory_item_id,),
        ).fetchone()
        evidence_rows = self._connection.execute(
            "SELECT context FROM evidence WHERE memory_item_id = ? ORDER BY id",
            (memory_item_id,),
        ).fetchall()
        support_rows = self._connection.execute(
            """
            SELECT fragments_json
            FROM consolidation_support
            WHERE candidate_memory_item_id = ?
            ORDER BY source_evidence_id
            """,
            (memory_item_id,),
        ).fetchall()
        support_fragments = [
            fragment
            for support in support_rows
            for fragment in json.loads(support["fragments_json"])
        ]
        text = "\n".join(
            [
                _memory_text(row["statement"], row["condition"]),
                *(value["context"] for value in evidence_rows),
                *support_fragments,
            ]
        )
        self._connection.execute(
            "DELETE FROM memory_item_fts WHERE memory_item_id = ?",
            (memory_item_id,),
        )
        self._connection.execute(
            "INSERT INTO memory_item_fts(memory_item_id, document) VALUES (?, ?)",
            (memory_item_id, " ".join(_tokens(text))),
        )
