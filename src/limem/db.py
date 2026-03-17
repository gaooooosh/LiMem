# -*- coding: utf-8 -*-
import os

import kuzu


def _normalize_db_path(db_path):
    # Kuzu uses a single database file. Store it under DB/ and default to database.kz.
    db_path = os.path.expanduser(db_path)
    if db_path.endswith(os.sep) or (os.path.exists(db_path) and os.path.isdir(db_path)):
        db_path = os.path.join(db_path, "database.kz")
    parent = os.path.dirname(db_path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
    return db_path


def open_connection(db_path):
    db_path = _normalize_db_path(db_path)
    print(f"📁 Using Kuzu DB file: {db_path}")
    db = kuzu.Database(db_path)
    return kuzu.Connection(db)


def init_db(conn):
    # Schema mirrors the paper's memory graph: Episodes (raw), Events (summaries),
    # Entities (symbols), and relations for memory consolidation and provenance.
    conn.execute(
        """
        CREATE NODE TABLE IF NOT EXISTS Episode(
            id STRING,
            content STRING,
            timestamp INT64,
            PRIMARY KEY(id)
        )
        """
    )
    conn.execute(
        """
        CREATE NODE TABLE IF NOT EXISTS Event(
            id STRING,
            summary STRING,
            participants STRING,
            time_range STRING,
            action STRING,
            causality STRING,
            payload STRING,
            evidence STRING,
            timestamp INT64,
            last_active INT64,
            created_at INT64,
            updated_at INT64,
            valid_from INT64,
            valid_to INT64,
            status STRING,
            support_count INT64,
            embedding FLOAT[1536],
            PRIMARY KEY(id)
        )
        """
    )
    conn.execute(
        """
        CREATE NODE TABLE IF NOT EXISTS Entity(
            id STRING,
            type STRING,
            PRIMARY KEY(id)
        )
        """
    )
    conn.execute(
        """
        CREATE NODE TABLE IF NOT EXISTS User(
            id STRING,
            PRIMARY KEY(id)
        )
        """
    )
    conn.execute(
        """
        CREATE NODE TABLE IF NOT EXISTS Context(
            id STRING,
            context_type STRING,
            subtype STRING,
            summary STRING,
            structured_slots STRING,
            confidence DOUBLE,
            support_count INT64,
            created_at INT64,
            updated_at INT64,
            valid_from INT64,
            valid_to INT64,
            last_seen_at INT64,
            status STRING,
            source_refs STRING,
            merged_from STRING,
            embedding FLOAT[1536],
            PRIMARY KEY(id)
        )
        """
    )
    conn.execute(
        """
        CREATE REL TABLE IF NOT EXISTS INVOLVES(
            FROM Event TO Entity,
            t_created INT64,
            t_expired INT64,
            t_valid INT64,
            t_invalid INT64,
            c_valid INT64
        )
        """
    )
    conn.execute(
        """
        CREATE REL TABLE IF NOT EXISTS EXTRACTED_FROM(
            FROM Event TO Episode
        )
        """
    )
    conn.execute(
        """
        CREATE REL TABLE IF NOT EXISTS PERMANENT_TRAIT(
            FROM User TO Event,
            t_created INT64
        )
        """
    )
    conn.execute(
        """
        CREATE REL TABLE IF NOT EXISTS IN_REL(
            FROM Event TO Context,
            confidence DOUBLE,
            weight DOUBLE,
            original_signal STRING,
            evidence_span STRING,
            created_at INT64,
            updated_at INT64,
            last_seen_at INT64
        )
        """
    )
    # Best-effort migration for older databases.
    for stmt in [
        "ALTER TABLE Event ADD participants STRING",
        "ALTER TABLE Event ADD time_range STRING",
        "ALTER TABLE Event ADD action STRING",
        "ALTER TABLE Event ADD causality STRING",
        "ALTER TABLE Event ADD payload STRING",
        "ALTER TABLE Event ADD evidence STRING",
        "ALTER TABLE Event ADD timestamp INT64",
        "ALTER TABLE Event ADD last_active INT64",
        "ALTER TABLE Event ADD created_at INT64",
        "ALTER TABLE Event ADD updated_at INT64",
        "ALTER TABLE Event ADD valid_from INT64",
        "ALTER TABLE Event ADD valid_to INT64",
        "ALTER TABLE Event ADD status STRING",
        "ALTER TABLE Event ADD support_count INT64",
        "ALTER TABLE Entity ADD embedding FLOAT[1536]",
        "ALTER TABLE INVOLVES ADD t_expired INT64",
        "ALTER TABLE INVOLVES ADD t_valid INT64",
        "ALTER TABLE INVOLVES ADD t_invalid INT64",
        "ALTER TABLE INVOLVES ADD c_valid INT64",
        "ALTER TABLE User ADD id STRING",
        "ALTER TABLE Context ADD structured_slots STRING",
        "ALTER TABLE Context ADD confidence DOUBLE",
        "ALTER TABLE Context ADD support_count INT64",
        "ALTER TABLE Context ADD created_at INT64",
        "ALTER TABLE Context ADD updated_at INT64",
        "ALTER TABLE Context ADD valid_from INT64",
        "ALTER TABLE Context ADD valid_to INT64",
        "ALTER TABLE Context ADD last_seen_at INT64",
        "ALTER TABLE Context ADD status STRING",
        "ALTER TABLE Context ADD source_refs STRING",
        "ALTER TABLE Context ADD merged_from STRING",
        "ALTER TABLE Context ADD embedding FLOAT[1536]",
        "ALTER TABLE IN_REL ADD original_signal STRING",
        "ALTER TABLE IN_REL ADD evidence_span STRING",
        "ALTER TABLE IN_REL ADD confidence DOUBLE",
        "ALTER TABLE IN_REL ADD weight DOUBLE",
        "ALTER TABLE IN_REL ADD created_at INT64",
        "ALTER TABLE IN_REL ADD updated_at INT64",
        "ALTER TABLE IN_REL ADD last_seen_at INT64",
    ]:
        try:
            conn.execute(stmt)
        except Exception:
            continue
