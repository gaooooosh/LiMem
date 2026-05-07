"""LtmPool eviction behavior tests."""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from types import SimpleNamespace


SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from service.pool import LtmHandle, LtmPool  # noqa: E402


class _FakeStore:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _FakeLtm:
    def __init__(self) -> None:
        self.store = _FakeStore()


def _make_pool() -> LtmPool:
    def load(dbr):
        return LtmHandle(
            db_id=dbr.db_id,
            ltm=_FakeLtm(),
            audit=object(),
            bm25=object(),
            write_lock=threading.Lock(),
        )

    return LtmPool(loader=load, max_size=2, idle_timeout=60)


def test_soft_evict_defers_close_until_refcount_reaches_zero():
    pool = _make_pool()
    db = SimpleNamespace(db_id="db1")

    with pool.acquire(db) as handle:
        store = handle.ltm.store
        assert pool.evict("db1", force=False) is False
        assert store.closed == 0
        stats = pool.stats()
        assert stats["size"] == 1
        assert stats["items"][0]["pending_eviction"] is True

    assert store.closed == 1
    assert pool.stats()["size"] == 0


def test_soft_evict_closes_immediately_when_not_in_use():
    pool = _make_pool()
    db = SimpleNamespace(db_id="db1")

    with pool.acquire(db) as handle:
        store = handle.ltm.store

    assert store.closed == 0
    assert pool.evict("db1", force=False) is True
    assert store.closed == 1
    assert pool.stats()["size"] == 0
