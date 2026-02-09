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
            priority STRING,
            participants STRING,
            time_range STRING,
            location STRING,
            action STRING,
            causality STRING,
            evidence STRING,
            consistency STRING,
            privacy_handling STRING,
            last_active INT64,
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

    # Best-effort migration for older databases.
    for stmt in [
        "ALTER TABLE Event ADD COLUMN priority STRING",
        "ALTER TABLE Event ADD COLUMN participants STRING",
        "ALTER TABLE Event ADD COLUMN time_range STRING",
        "ALTER TABLE Event ADD COLUMN location STRING",
        "ALTER TABLE Event ADD COLUMN action STRING",
        "ALTER TABLE Event ADD COLUMN causality STRING",
        "ALTER TABLE Event ADD COLUMN evidence STRING",
        "ALTER TABLE Event ADD COLUMN consistency STRING",
        "ALTER TABLE Event ADD COLUMN privacy_handling STRING",
        "ALTER TABLE Event ADD COLUMN last_active INT64",
        "ALTER TABLE INVOLVES ADD COLUMN t_expired INT64",
        "ALTER TABLE INVOLVES ADD COLUMN t_valid INT64",
        "ALTER TABLE INVOLVES ADD COLUMN t_invalid INT64",
        "ALTER TABLE INVOLVES ADD COLUMN c_valid INT64",
        "ALTER TABLE User ADD COLUMN id STRING",
    ]:
        try:
            conn.execute(stmt)
        except Exception:
            continue
