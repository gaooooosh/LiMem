"""LTM 实例池：LRU + 空闲驱逐 + 引用计数。

每个 db_id 对应一个 LtmHandle（含 ltm、audit、bm25、write_lock）。
acquire() 返回一个上下文管理器，期间 refcount += 1，确保不会被 LRU 淘汰。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from .audit import ServiceAuditLogger

logger = logging.getLogger(__name__)


@dataclass
class LtmHandle:
    db_id: str
    ltm: Any
    audit: ServiceAuditLogger
    bm25: Any  # BM25Index
    write_lock: threading.Lock
    last_used: float = field(default_factory=time.time)
    refcount: int = 0


# loader 是 (db_record) -> LtmHandle 的回调，由 DatabaseManager 提供
LtmLoader = Callable[[Any], LtmHandle]


class LtmPool:
    """线程安全的 LTM 实例池。"""

    def __init__(
        self,
        loader: LtmLoader,
        max_size: int = 16,
        idle_timeout: float = 1800.0,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._loader = loader
        self.max_size = max_size
        self.idle_timeout = idle_timeout
        self._items: OrderedDict[str, LtmHandle] = OrderedDict()
        self._pending_evictions: set[str] = set()
        self._mu = threading.Lock()
        self._loader_locks: dict[str, threading.Lock] = {}
        self._loader_locks_mu = threading.Lock()
        self._reaper_stop = threading.Event()
        self._reaper_thread: threading.Thread | None = None

    # ---------- 公共接口 ----------

    @contextmanager
    def acquire(self, db_record: Any) -> Iterator[LtmHandle]:
        handle = self._acquire_handle(db_record)
        try:
            yield handle
        finally:
            to_release: LtmHandle | None = None
            with self._mu:
                handle.refcount = max(0, handle.refcount - 1)
                handle.last_used = time.time()
                if handle.refcount == 0 and handle.db_id in self._pending_evictions:
                    self._pending_evictions.discard(handle.db_id)
                    if self._items.get(handle.db_id) is handle:
                        self._items.pop(handle.db_id, None)
                        to_release = handle
            if to_release is not None:
                self._release(to_release)

    def evict(self, db_id: str, *, force: bool = False) -> bool:
        """显式淘汰一个库；force=True 时即便 refcount>0 也强行关闭。

        非 force 场景遇到 refcount>0 时只标记延迟淘汰，引用归零后关闭。
        返回是否已经执行关闭。
        """
        with self._mu:
            handle = self._items.get(db_id)
            if handle is None:
                self._pending_evictions.discard(db_id)
                return False
            if handle.refcount > 0 and not force:
                self._pending_evictions.add(db_id)
                return False
            self._pending_evictions.discard(db_id)
            self._items.pop(db_id, None)
        self._release(handle)
        return True

    def shutdown(self) -> None:
        self._reaper_stop.set()
        if self._reaper_thread and self._reaper_thread.is_alive():
            self._reaper_thread.join(timeout=2.0)
        with self._mu:
            handles = list(self._items.values())
            self._items.clear()
            self._pending_evictions.clear()
        for h in handles:
            self._release(h)

    def start_reaper(self) -> None:
        if self._reaper_thread and self._reaper_thread.is_alive():
            return
        interval = max(5.0, self.idle_timeout / 4.0)
        self._reaper_stop.clear()

        def _loop():
            while not self._reaper_stop.wait(interval):
                try:
                    self._evict_idle()
                except Exception:
                    logger.exception("LtmPool reaper iteration failed")

        self._reaper_thread = threading.Thread(
            target=_loop, name="ltm-pool-reaper", daemon=True
        )
        self._reaper_thread.start()

    def stats(self) -> dict[str, Any]:
        with self._mu:
            return {
                "max_size": self.max_size,
                "idle_timeout": self.idle_timeout,
                "size": len(self._items),
                "items": [
                    {
                        "db_id": h.db_id,
                        "refcount": h.refcount,
                        "last_used": h.last_used,
                        "pending_eviction": h.db_id in self._pending_evictions,
                    }
                    for h in self._items.values()
                ],
            }

    # ---------- 内部 ----------

    def _acquire_handle(self, db_record: Any) -> LtmHandle:
        """获取或加载 handle，并在返回前持有 1 个引用计数（占位防淘汰）。"""
        db_id = db_record.db_id
        # fast path: 已加载
        with self._mu:
            handle = self._items.get(db_id)
            if handle is not None:
                handle.refcount += 1
                handle.last_used = time.time()
                self._items.move_to_end(db_id)
                return handle

        loader_lock = self._get_loader_lock(db_id)
        with loader_lock:
            with self._mu:
                handle = self._items.get(db_id)
                if handle is not None:
                    handle.refcount += 1
                    handle.last_used = time.time()
                    self._items.move_to_end(db_id)
                    return handle
            # 真正的加载（耗时 IO，不持池锁）
            handle = self._loader(db_record)
            with self._mu:
                # 加载期间可能有别的线程也加载并放入 → 取已有那一个
                existing = self._items.get(db_id)
                if existing is not None and existing is not handle:
                    # 丢弃刚加载的副本
                    pass_release = handle
                    handle = existing
                else:
                    pass_release = None
                    self._items[db_id] = handle
                handle.refcount += 1
                handle.last_used = time.time()
                self._items.move_to_end(db_id)
            if pass_release is not None:
                self._release(pass_release)
            self._evict_if_over_capacity()
            return handle

    def _get_loader_lock(self, db_id: str) -> threading.Lock:
        with self._loader_locks_mu:
            lock = self._loader_locks.get(db_id)
            if lock is None:
                lock = threading.Lock()
                self._loader_locks[db_id] = lock
            return lock

    def _evict_if_over_capacity(self) -> None:
        to_release: list[LtmHandle] = []
        with self._mu:
            if len(self._items) <= self.max_size:
                return
            # 从 LRU 头开始扫，跳过 refcount > 0 的；可能软超过 max_size
            for db_id in list(self._items.keys()):
                if len(self._items) <= self.max_size:
                    break
                handle = self._items[db_id]
                if handle.refcount > 0:
                    continue
                self._pending_evictions.discard(db_id)
                self._items.pop(db_id, None)
                to_release.append(handle)
        for h in to_release:
            self._release(h)

    def _evict_idle(self) -> None:
        now = time.time()
        to_release: list[LtmHandle] = []
        with self._mu:
            for db_id in list(self._items.keys()):
                handle = self._items[db_id]
                if handle.refcount > 0:
                    continue
                if (now - handle.last_used) < self.idle_timeout:
                    continue
                self._pending_evictions.discard(db_id)
                self._items.pop(db_id, None)
                to_release.append(handle)
        for h in to_release:
            logger.info("LtmPool evicting idle handle db_id=%s", h.db_id)
            self._release(h)

    def _release(self, handle: LtmHandle) -> None:
        try:
            store = getattr(handle.ltm, "store", None)
            inner = store
            # 如果被 audit 代理包了一层，拿原始 store
            real = getattr(store, "_store", None)
            if real is not None:
                inner = real
            close_fn = getattr(inner, "close", None)
            if callable(close_fn):
                close_fn()
        except Exception:
            logger.exception("LtmPool release: failed to close store for %s", handle.db_id)
