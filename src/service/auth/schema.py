"""SQLite schema definition and migration logic for the auth store."""

from __future__ import annotations

import sqlite3


SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS users (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    key_hash     TEXT NOT NULL UNIQUE,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label        TEXT DEFAULT '',
    scopes       TEXT NOT NULL DEFAULT 'r,w',
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_keys_user ON api_keys(user_id);

CREATE TABLE IF NOT EXISTS databases (
    db_id            TEXT PRIMARY KEY,
    owner_user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    display_name     TEXT NOT NULL,
    db_path          TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    last_accessed_at TEXT,
    status           TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_db_owner ON databases(owner_user_id);
"""


def apply_pragmas(conn: sqlite3.Connection) -> None:
    """启用 WAL 与外键约束；连接级 pragma 需在每条 connection 上设置。"""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")


def migrate(conn: sqlite3.Connection) -> int:
    """按 user_version 增量迁移；返回最终 schema 版本。

    仅一个版本时是 noop；保留接口以便后续追加 V2/V3。
    """
    cursor = conn.execute("PRAGMA user_version")
    current = cursor.fetchone()[0]
    if current < 1:
        with conn:  # 隐式事务
            conn.executescript(SCHEMA_V1)
            conn.execute("PRAGMA user_version = 1")
        current = 1
    return current
