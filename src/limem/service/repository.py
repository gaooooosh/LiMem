"""Persist API-key hashes and memory-bank ownership metadata in SQLite."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import secrets
import sqlite3
from threading import RLock
from uuid import uuid4

from .auth import hash_api_key
from .models import BankRecord, IssuedKey, KeyRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS service_api_key (
    key_id      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
    created_at  TEXT NOT NULL,
    revoked_at  TEXT,
    CHECK (
        (status = 'active' AND revoked_at IS NULL)
        OR (status = 'revoked' AND revoked_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS service_memory_bank (
    memory_bank_id TEXT PRIMARY KEY,
    owner_key_id   TEXT NOT NULL REFERENCES service_api_key(key_id),
    name           TEXT NOT NULL,
    type           TEXT NOT NULL CHECK (type IN ('temporary', 'persistent')),
    source_format  TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    expires_at     TEXT,
    deletes_at     TEXT
);

CREATE INDEX IF NOT EXISTS service_memory_bank_owner
ON service_memory_bank(owner_key_id, created_at, memory_bank_id);
"""


class ServiceRepository:
    """Store service identities and memory-bank metadata."""

    def __init__(self, database_path: str | Path) -> None:
        self._connection = sqlite3.connect(database_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(_SCHEMA)
        self._lock = RLock()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def issue_key(self, name: str) -> IssuedKey:
        """Create one key identity and return its plaintext token once."""

        api_key = f"limem_{secrets.token_urlsafe(32)}"
        record = IssuedKey(
            key_id=f"key_{uuid4().hex}",
            name=name,
            status="active",
            created_at=datetime.now(timezone.utc),
            revoked_at=None,
            api_key=api_key,
        )
        with self._lock:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO service_api_key (
                        key_id, name, api_key_hash, status, created_at, revoked_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.key_id,
                        record.name,
                        hash_api_key(record.api_key),
                        record.status,
                        record.created_at.isoformat(),
                        None,
                    ),
                )
        return record

    def list_keys(self) -> list[KeyRecord]:
        """Return all key metadata without hashes or plaintext tokens."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM service_api_key ORDER BY created_at DESC, key_id DESC"
            ).fetchall()
        return [self._key_from_row(row) for row in rows]

    def find_key_by_hash(self, api_key_hash: str) -> KeyRecord | None:
        """Resolve one stored key hash for user authentication."""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM service_api_key WHERE api_key_hash = ?",
                (api_key_hash,),
            ).fetchone()
        return self._key_from_row(row) if row is not None else None

    def get_key(self, key_id: str) -> KeyRecord:
        """Return one issued key by its public ID."""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM service_api_key WHERE key_id = ?",
                (key_id,),
            ).fetchone()
        if row is None:
            raise KeyError(key_id)
        return self._key_from_row(row)

    def revoke_key(self, key_id: str) -> KeyRecord:
        """Revoke one active key while retaining its public metadata."""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM service_api_key WHERE key_id = ?",
                (key_id,),
            ).fetchone()
            if row is None:
                raise KeyError(key_id)
            if row["status"] == "revoked":
                return self._key_from_row(row)

            revoked_at = datetime.now(timezone.utc)
            with self._connection:
                self._connection.execute(
                    """
                    UPDATE service_api_key
                    SET status = 'revoked', revoked_at = ?
                    WHERE key_id = ?
                    """,
                    (revoked_at.isoformat(), key_id),
                )
            return KeyRecord(
                key_id=row["key_id"],
                name=row["name"],
                status="revoked",
                created_at=datetime.fromisoformat(row["created_at"]),
                revoked_at=revoked_at,
            )

    def create_bank(self, record: BankRecord) -> BankRecord:
        """Persist manager-produced ownership and lifecycle metadata."""

        with self._lock:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO service_memory_bank (
                        memory_bank_id, owner_key_id, name, type, source_format,
                        created_at, expires_at, deletes_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.memory_bank_id,
                        record.owner_key_id,
                        record.name,
                        record.type,
                        record.source_format,
                        record.created_at.isoformat(),
                        self._optional_datetime(record.expires_at),
                        self._optional_datetime(record.deletes_at),
                    ),
                )
        return record

    def get_bank(self, memory_bank_id: str) -> BankRecord:
        """Return one bank record without applying ownership or expiry policy."""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM service_memory_bank WHERE memory_bank_id = ?",
                (memory_bank_id,),
            ).fetchone()
        if row is None:
            raise KeyError(memory_bank_id)
        return self._bank_from_row(row)

    def get_owned_bank(self, owner_key_id: str, memory_bank_id: str) -> BankRecord:
        """Return a bank only when it belongs to the authenticated key identity."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM service_memory_bank
                WHERE memory_bank_id = ? AND owner_key_id = ?
                """,
                (memory_bank_id, owner_key_id),
            ).fetchone()
        if row is None:
            raise KeyError(memory_bank_id)
        return self._bank_from_row(row)

    def list_banks(self, owner_key_id: str) -> list[BankRecord]:
        """Return the existing bank records owned by one key identity."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM service_memory_bank
                WHERE owner_key_id = ?
                ORDER BY created_at DESC, memory_bank_id DESC
                """,
                (owner_key_id,),
            ).fetchall()
        return [self._bank_from_row(row) for row in rows]

    def list_all_banks(self) -> list[BankRecord]:
        """Return every bank for administrator cleanup."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM service_memory_bank
                ORDER BY created_at DESC, memory_bank_id DESC
                """
            ).fetchall()
        return [self._bank_from_row(row) for row in rows]

    def delete_bank(self, owner_key_id: str, memory_bank_id: str) -> None:
        """Delete one owned bank metadata record after manager cleanup."""

        with self._lock:
            with self._connection:
                cursor = self._connection.execute(
                    """
                    DELETE FROM service_memory_bank
                    WHERE memory_bank_id = ? AND owner_key_id = ?
                    """,
                    (memory_bank_id, owner_key_id),
                )
        if cursor.rowcount != 1:
            raise KeyError(memory_bank_id)

    def delete_bank_any(self, memory_bank_id: str) -> None:
        """Delete one bank metadata record for an administrator operation."""

        with self._lock:
            with self._connection:
                cursor = self._connection.execute(
                    "DELETE FROM service_memory_bank WHERE memory_bank_id = ?",
                    (memory_bank_id,),
                )
        if cursor.rowcount != 1:
            raise KeyError(memory_bank_id)

    @staticmethod
    def _key_from_row(row: sqlite3.Row) -> KeyRecord:
        revoked_at = row["revoked_at"]
        return KeyRecord(
            key_id=row["key_id"],
            name=row["name"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            revoked_at=datetime.fromisoformat(revoked_at) if revoked_at else None,
        )

    @staticmethod
    def _bank_from_row(row: sqlite3.Row) -> BankRecord:
        expires_at = row["expires_at"]
        deletes_at = row["deletes_at"]
        return BankRecord(
            memory_bank_id=row["memory_bank_id"],
            owner_key_id=row["owner_key_id"],
            name=row["name"],
            type=row["type"],
            source_format=row["source_format"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
            deletes_at=datetime.fromisoformat(deletes_at) if deletes_at else None,
        )

    @staticmethod
    def _optional_datetime(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None
