"""SQLite-backed repository for users / api_keys / databases."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .hashing import generate_token, hash_token
from .schema import apply_pragmas, migrate


# ---------- 数据模型 ----------


@dataclass
class User:
    id: str
    name: str
    created_at: str


@dataclass
class ApiKey:
    id: str
    user_id: str
    label: str
    scopes: str  # csv: r,w,admin
    created_at: str
    last_used_at: Optional[str] = None
    revoked_at: Optional[str] = None

    @property
    def is_revoked(self) -> bool:
        return bool(self.revoked_at)

    @property
    def scope_set(self) -> set[str]:
        return {s.strip() for s in (self.scopes or "").split(",") if s.strip()}


@dataclass
class Database:
    db_id: str
    owner_user_id: str
    display_name: str
    db_path: str
    created_at: str
    last_accessed_at: Optional[str] = None
    status: str = "active"


# ---------- 异常 ----------


class UserAlreadyExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class KeyNotFoundError(Exception):
    pass


class DatabaseAlreadyExistsError(Exception):
    pass


# ---------- 工具 ----------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


# ---------- Repository ----------


class AuthRepository:
    """单文件 SQLite 鉴权仓库。

    线程安全：内部 RLock 序列化写；WAL 模式下读不被阻塞。
    last_used_at 写抖动通过进程内字典节流（60 秒粒度）。
    """

    LAST_USED_THROTTLE_SEC = 60

    def __init__(self, sqlite_path: str) -> None:
        parent = os.path.dirname(sqlite_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.path = sqlite_path
        self._conn = sqlite3.connect(sqlite_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        apply_pragmas(self._conn)
        migrate(self._conn)
        self._lock = threading.RLock()
        self._last_used_cache: dict[str, float] = {}

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ---- users ----

    def create_user(self, name: str) -> User:
        if not name or not name.strip():
            raise ValueError("user name is required")
        user = User(id=_new_id(), name=name.strip(), created_at=_now_iso())
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO users(id, name, created_at) VALUES(?, ?, ?)",
                    (user.id, user.name, user.created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise UserAlreadyExistsError(name) from exc
        return user

    def get_user(self, user_id: str) -> Optional[User]:
        row = self._conn.execute(
            "SELECT id, name, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return User(**dict(row)) if row else None

    def get_user_by_name(self, name: str) -> Optional[User]:
        row = self._conn.execute(
            "SELECT id, name, created_at FROM users WHERE name = ?", (name,)
        ).fetchone()
        return User(**dict(row)) if row else None

    def list_users(self) -> list[User]:
        rows = self._conn.execute(
            "SELECT id, name, created_at FROM users ORDER BY created_at"
        ).fetchall()
        return [User(**dict(r)) for r in rows]

    # ---- api keys ----

    def issue_key(self, user_id: str, label: str = "", scopes: str = "r,w") -> tuple[str, ApiKey]:
        """签发新 key。返回 (明文 token, ApiKey 元数据)。明文仅本次返回，不会再次出现。"""
        if not self.get_user(user_id):
            raise UserNotFoundError(user_id)
        token = generate_token()
        key = ApiKey(
            id=_new_id(),
            user_id=user_id,
            label=label or "",
            scopes=_normalize_scopes(scopes),
            created_at=_now_iso(),
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO api_keys(id, key_hash, user_id, label, scopes, created_at)"
                " VALUES(?, ?, ?, ?, ?, ?)",
                (key.id, hash_token(token), key.user_id, key.label, key.scopes, key.created_at),
            )
        return token, key

    def lookup_by_token(self, token: str) -> Optional[ApiKey]:
        if not token:
            return None
        row = self._conn.execute(
            "SELECT id, user_id, label, scopes, created_at, last_used_at, revoked_at"
            " FROM api_keys WHERE key_hash = ?",
            (hash_token(token),),
        ).fetchone()
        if not row:
            return None
        key = ApiKey(**dict(row))
        if key.is_revoked:
            return None
        return key

    def get_key(self, key_id: str) -> Optional[ApiKey]:
        row = self._conn.execute(
            "SELECT id, user_id, label, scopes, created_at, last_used_at, revoked_at"
            " FROM api_keys WHERE id = ?",
            (key_id,),
        ).fetchone()
        return ApiKey(**dict(row)) if row else None

    def list_keys_by_user(self, user_id: str) -> list[ApiKey]:
        rows = self._conn.execute(
            "SELECT id, user_id, label, scopes, created_at, last_used_at, revoked_at"
            " FROM api_keys WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()
        return [ApiKey(**dict(r)) for r in rows]

    def touch_last_used(self, key_id: str) -> None:
        """节流更新 last_used_at；同 key 60s 内不重复落盘。"""
        now = time.time()
        last = self._last_used_cache.get(key_id, 0.0)
        if now - last < self.LAST_USED_THROTTLE_SEC:
            return
        self._last_used_cache[key_id] = now
        with self._lock:
            self._conn.execute(
                "UPDATE api_keys SET last_used_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_now_iso(), key_id),
            )

    def revoke_key(self, key_id: str) -> None:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (_now_iso(), key_id),
            )
            if cur.rowcount == 0:
                # 不存在或已撤销
                if not self.get_key(key_id):
                    raise KeyNotFoundError(key_id)

    # ---- databases ----

    def create_database(
        self, db_id: str, owner_user_id: str, display_name: str, db_path: str
    ) -> Database:
        if not self.get_user(owner_user_id):
            raise UserNotFoundError(owner_user_id)
        record = Database(
            db_id=db_id,
            owner_user_id=owner_user_id,
            display_name=display_name,
            db_path=db_path,
            created_at=_now_iso(),
            status="active",
        )
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO databases(db_id, owner_user_id, display_name, db_path, created_at, status)"
                    " VALUES(?, ?, ?, ?, ?, ?)",
                    (
                        record.db_id,
                        record.owner_user_id,
                        record.display_name,
                        record.db_path,
                        record.created_at,
                        record.status,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DatabaseAlreadyExistsError(db_id) from exc
        return record

    def get_database(self, db_id: str) -> Optional[Database]:
        row = self._conn.execute(
            "SELECT db_id, owner_user_id, display_name, db_path, created_at, last_accessed_at, status"
            " FROM databases WHERE db_id = ?",
            (db_id,),
        ).fetchone()
        return Database(**dict(row)) if row else None

    def list_databases_by_user(self, user_id: str, include_archived: bool = False) -> list[Database]:
        if include_archived:
            rows = self._conn.execute(
                "SELECT db_id, owner_user_id, display_name, db_path, created_at, last_accessed_at, status"
                " FROM databases WHERE owner_user_id = ? ORDER BY created_at",
                (user_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT db_id, owner_user_id, display_name, db_path, created_at, last_accessed_at, status"
                " FROM databases WHERE owner_user_id = ? AND status = 'active' ORDER BY created_at",
                (user_id,),
            ).fetchall()
        return [Database(**dict(r)) for r in rows]

    def list_all_databases(self, include_archived: bool = True) -> list[Database]:
        if include_archived:
            rows = self._conn.execute(
                "SELECT db_id, owner_user_id, display_name, db_path, created_at, last_accessed_at, status"
                " FROM databases ORDER BY created_at"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT db_id, owner_user_id, display_name, db_path, created_at, last_accessed_at, status"
                " FROM databases WHERE status = 'active' ORDER BY created_at"
            ).fetchall()
        return [Database(**dict(r)) for r in rows]

    def archive_database(self, db_id: str) -> None:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE databases SET status = 'archived' WHERE db_id = ? AND status = 'active'",
                (db_id,),
            )
            if cur.rowcount == 0 and not self.get_database(db_id):
                raise KeyNotFoundError(db_id)

    def touch_database(self, db_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE databases SET last_accessed_at = ? WHERE db_id = ?",
                (_now_iso(), db_id),
            )


_VALID_SCOPES = {"r", "w", "admin"}


def _normalize_scopes(scopes: str) -> str:
    if not scopes:
        return "r,w"
    parts = []
    seen = set()
    for raw in scopes.split(","):
        item = raw.strip().lower()
        if not item:
            continue
        if item not in _VALID_SCOPES:
            raise ValueError(f"unknown scope: {item}")
        if item in seen:
            continue
        seen.add(item)
        parts.append(item)
    if not parts:
        return "r,w"
    return ",".join(parts)
