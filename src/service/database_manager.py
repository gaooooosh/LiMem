"""DatabaseManager：把 AuthRepository、LtmPool 和文件路径策略胶合起来。"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import threading
from pathlib import Path
from typing import Any

from limem.factory import create_ltm
from limem.retrieval import BM25Index

from .audit import ServiceAuditLogger, install_store_audit_proxy
from .auth import AuthRepository, Database, DatabaseAlreadyExistsError
from .pool import LtmHandle, LtmPool

logger = logging.getLogger(__name__)


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_DB_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")


def _slugify(text: str) -> str:
    text = (text or "").strip().lower()
    text = _SLUG_RE.sub("-", text).strip("-")
    if not text:
        text = "db"
    return text[:30]


class DatabaseManager:
    """协调用户建库 / 路径生成 / 池加载。"""

    def __init__(
        self,
        repo: AuthRepository,
        *,
        base_db_dir: str,
        base_audit_dir: str,
        pool_max_size: int = 16,
        pool_idle_timeout: float = 1800.0,
    ) -> None:
        self.repo = repo
        self.base_db_dir = Path(base_db_dir)
        self.base_audit_dir = Path(base_audit_dir)
        self.pool = LtmPool(
            loader=self._load_handle,
            max_size=pool_max_size,
            idle_timeout=pool_idle_timeout,
        )

    # ---------- 生命周期 ----------

    def start(self) -> None:
        self.pool.start_reaper()

    def shutdown(self) -> None:
        self.pool.shutdown()

    # ---------- 用户建库 ----------

    def create_for_user(self, user_id: str, display_name: str) -> Database:
        if not display_name or not display_name.strip():
            raise ValueError("display_name is required")
        slug_root = _slugify(display_name)
        # 重试少量次数避免随机后缀冲突
        last_exc: Exception | None = None
        for _ in range(5):
            db_id = f"{slug_root}-{secrets.token_hex(3)}"
            if not _DB_ID_RE.fullmatch(db_id):
                # 极端情况下 slug_root 太短，补齐
                db_id = f"db-{secrets.token_hex(3)}"
            db_path = str(self._user_db_path(user_id, db_id))
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            try:
                return self.repo.create_database(db_id, user_id, display_name.strip(), db_path)
            except DatabaseAlreadyExistsError as exc:
                last_exc = exc
                continue
        assert last_exc is not None
        raise last_exc

    def archive(self, db_id: str) -> None:
        self.repo.archive_database(db_id)
        # 状态已归档会阻止新 acquire；已在途请求结束后由池延迟关闭。
        self.pool.evict(db_id, force=False)

    def hard_delete(self, db_id: str) -> bool:
        """彻底删除一个库：池驱逐 → 文件系统清理 → sqlite 行删除。

        与 archive 区别：archive 仅翻 status；hard_delete 是不可逆销毁。
        Kuzu 的 db_path 在不同版本下既可能是"单文件"（生产实际形态）也可能是
        目录，所以这里两种都兼容；并连带清理同前缀的 .wal / .shadow 等旁路文件。
        幂等：库不存在时返回 False，不抛错（便于 delete_user 循环调用）。
        """
        record = self.repo.get_database(db_id)
        if record is None:
            return False
        # 1) 强制驱逐池中 handle（关闭 kuzu connection 才能删文件）
        self.pool.evict(db_id, force=True)
        # 2) 清理 db_path 与同前缀的旁路文件
        db_path = Path(record.db_path)
        try:
            if db_path.is_dir():
                shutil.rmtree(db_path)
            elif db_path.exists():
                db_path.unlink()
            # Kuzu 单文件模式会写出 xxx.kz.wal / xxx.kz.shadow 等旁路文件
            parent = db_path.parent
            if parent.exists():
                for sibling in parent.glob(db_path.name + ".*"):
                    try:
                        if sibling.is_dir():
                            shutil.rmtree(sibling)
                        else:
                            sibling.unlink()
                    except OSError:
                        logger.exception(
                            "hard_delete: cleanup sibling failed: %s", sibling
                        )
        except OSError:
            logger.exception("hard_delete: cleanup failed for %s", db_path)
            raise
        # 3) 删除审计日志文件
        audit_path = self._user_audit_path(record.owner_user_id, db_id)
        if audit_path.exists():
            try:
                audit_path.unlink()
            except OSError:
                logger.exception("hard_delete: unlink audit failed for %s", audit_path)
        # 4) 删除 sqlite 行
        self.repo.delete_database(db_id)
        return True

    # ---------- 路径策略 ----------

    def _user_db_path(self, user_id: str, db_id: str) -> Path:
        return self.base_db_dir / "users" / user_id / f"{db_id}.kz"

    def _user_audit_path(self, user_id: str, db_id: str) -> Path:
        return self.base_audit_dir / user_id / f"{db_id}.jsonl"

    # ---------- LtmPool loader ----------

    def _load_handle(self, dbr: Database) -> LtmHandle:
        logger.info("DatabaseManager loading LTM for db_id=%s", dbr.db_id)
        ltm = create_ltm(db_path=dbr.db_path)
        audit_path = self._user_audit_path(dbr.owner_user_id, dbr.db_id)
        os.makedirs(audit_path.parent, exist_ok=True)
        audit = ServiceAuditLogger(path=str(audit_path))
        install_store_audit_proxy(ltm, audit)

        bm25 = BM25Index()
        try:
            events = ltm.store.list_events(limit=100000, statuses=["active"])
            bm25.rebuild(events)
        except Exception:
            logger.exception("BM25 rebuild failed for db_id=%s", dbr.db_id)

        try:
            self.repo.touch_database(dbr.db_id)
        except Exception:
            logger.debug("touch_database failed for %s", dbr.db_id, exc_info=True)

        return LtmHandle(
            db_id=dbr.db_id,
            ltm=ltm,
            audit=audit,
            bm25=bm25,
            write_lock=threading.Lock(),
        )
