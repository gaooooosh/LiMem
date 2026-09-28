"""Own memory-bank files, lifecycle rules, and per-bank serialization."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from threading import Lock
from uuid import uuid4

from limem.memory_store import MemoryStore

from .models import BankRecord, CreateBankRequest
from .repository import ServiceRepository

_ACTIVE_LIFETIME = timedelta(hours=24)
_PHYSICAL_LIFETIME = timedelta(hours=48)


class KeyRevocationError(Exception):
    """One revoked key still has bank files that could not be deleted."""

    def __init__(self, failed_memory_bank_ids: list[str]) -> None:
        super().__init__(", ".join(failed_memory_bank_ids))
        self.failed_memory_bank_ids = failed_memory_bank_ids


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryBankManager:
    """Create, serialize, expire, and delete isolated SQLite memory banks."""

    def __init__(
        self,
        repository: ServiceRepository,
        data_dir: Path,
        logger: logging.Logger,
    ) -> None:
        self._repository = repository
        self._banks_dir = data_dir / "memory-banks"
        self._banks_dir.mkdir(parents=True, exist_ok=True)
        self._logger = logger
        self._locks: dict[str, Lock] = {}
        self._locks_guard = Lock()
        self._key_lifecycle_lock = Lock()

    def create_bank(
        self,
        owner_key_id: str,
        request: CreateBankRequest,
    ) -> BankRecord:
        with self._key_lifecycle_lock:
            if self._repository.get_key(owner_key_id).status != "active":
                raise KeyError(owner_key_id)
            created_at = _now()
            temporary = request.type == "temporary"
            record = BankRecord(
                memory_bank_id=f"bank_{uuid4().hex}",
                owner_key_id=owner_key_id,
                name=request.name,
                type=request.type,
                source_format=request.source_format,
                created_at=created_at,
                expires_at=created_at + _ACTIVE_LIFETIME if temporary else None,
                deletes_at=created_at + _PHYSICAL_LIFETIME if temporary else None,
            )
            store = MemoryStore(self.database_path(record.memory_bank_id))
            store.close()
            self._repository.create_bank(record)
        self._logger.info(
            "创建记忆库成功。API Key %s，记忆库 %s，类型：%s。",
            owner_key_id,
            record.memory_bank_id,
            record.type,
        )
        return record

    def list_banks(self, owner_key_id: str) -> list[BankRecord]:
        now = _now()
        return [
            bank
            for bank in self._repository.list_banks(owner_key_id)
            if self._is_active(bank, now)
        ]

    def require_owned_active(
        self,
        owner_key_id: str,
        memory_bank_id: str,
    ) -> BankRecord:
        """Check control-plane ownership without taking the Bank data lock."""

        return self._require_owned_active(owner_key_id, memory_bank_id)

    @contextmanager
    def use_owned_bank(
        self,
        owner_key_id: str,
        memory_bank_id: str,
    ) -> Iterator[tuple[BankRecord, Path]]:
        self._require_owned_active(owner_key_id, memory_bank_id)
        with self._bank_lock(memory_bank_id):
            bank = self._require_owned_active(owner_key_id, memory_bank_id)
            yield bank, self._require_database_path(memory_bank_id)

    @contextmanager
    def use_admin_bank(
        self,
        memory_bank_id: str,
    ) -> Iterator[tuple[BankRecord, Path]]:
        self._require_admin_active(memory_bank_id)
        with self._bank_lock(memory_bank_id):
            bank = self._require_admin_active(memory_bank_id)
            yield bank, self._require_database_path(memory_bank_id)

    def delete_owned_bank(self, owner_key_id: str, memory_bank_id: str) -> None:
        self._require_owned(owner_key_id, memory_bank_id)
        with self._bank_lock(memory_bank_id):
            self._require_owned(owner_key_id, memory_bank_id)
            self.database_path(memory_bank_id).unlink()
            self._repository.delete_bank(owner_key_id, memory_bank_id)
        self._logger.info(
            "删除记忆库成功。API Key %s，记忆库 %s。",
            owner_key_id,
            memory_bank_id,
        )

    def delete_admin_bank(self, memory_bank_id: str) -> None:
        self._delete_bank(memory_bank_id)
        self._logger.info("管理员删除记忆库成功。记忆库 %s。", memory_bank_id)

    def revoke_key_and_delete_banks(self, key_id: str) -> None:
        with self._key_lifecycle_lock:
            self._repository.revoke_key(key_id)
            failures: list[str] = []
            for bank in self._repository.list_banks(key_id):
                try:
                    self.delete_admin_bank(bank.memory_bank_id)
                except Exception:
                    failures.append(bank.memory_bank_id)
                    self._logger.exception(
                        "撤销 API Key 时删除记忆库失败。API Key %s，记忆库 %s。",
                        key_id,
                        bank.memory_bank_id,
                    )
        if failures:
            raise KeyRevocationError(failures)
        self._logger.info("撤销 API Key 成功。API Key %s。", key_id)

    def cleanup_expired_banks(self) -> None:
        now = _now()
        for bank in self._repository.list_all_banks():
            if bank.deletes_at is None or bank.deletes_at > now:
                continue
            try:
                self._delete_bank(bank.memory_bank_id)
                self._logger.info(
                    "清理到期记忆库成功。记忆库 %s。",
                    bank.memory_bank_id,
                )
            except Exception:
                self._logger.exception(
                    "清理到期记忆库失败。记忆库 %s。",
                    bank.memory_bank_id,
                )
                raise

    def database_path(self, memory_bank_id: str) -> Path:
        return self._banks_dir / f"{memory_bank_id}.db"

    def _require_database_path(self, memory_bank_id: str) -> Path:
        path = self.database_path(memory_bank_id)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def _delete_bank(self, memory_bank_id: str) -> None:
        self._repository.get_bank(memory_bank_id)
        with self._bank_lock(memory_bank_id):
            self._repository.get_bank(memory_bank_id)
            self.database_path(memory_bank_id).unlink()
            self._repository.delete_bank_any(memory_bank_id)

    def _require_owned_active(
        self,
        owner_key_id: str,
        memory_bank_id: str,
    ) -> BankRecord:
        bank = self._require_owned(owner_key_id, memory_bank_id)
        if not self._is_active(bank, _now()):
            raise KeyError(memory_bank_id)
        return bank

    def _require_owned(
        self,
        owner_key_id: str,
        memory_bank_id: str,
    ) -> BankRecord:
        if self._repository.get_key(owner_key_id).status != "active":
            raise KeyError(memory_bank_id)
        return self._repository.get_owned_bank(owner_key_id, memory_bank_id)

    def _require_admin_active(self, memory_bank_id: str) -> BankRecord:
        bank = self._repository.get_bank(memory_bank_id)
        if not self._is_active(bank, _now()):
            raise KeyError(memory_bank_id)
        return bank

    @staticmethod
    def _is_active(bank: BankRecord, now: datetime) -> bool:
        return bank.expires_at is None or now < bank.expires_at

    @contextmanager
    def _bank_lock(self, memory_bank_id: str) -> Iterator[None]:
        with self._locks_guard:
            lock = self._locks.setdefault(memory_bank_id, Lock())
        with lock:
            yield
