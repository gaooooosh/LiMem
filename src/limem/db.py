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
        CREATE REL TABLE IF NOT EXISTS INVOLVES(
            FROM Event TO Entity,
            t_created INT64,
            t_last_active INT64,
            c_valid INT64,
            weight DOUBLE
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
