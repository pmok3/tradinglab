"""Unit tests for :mod:`tradinglab.data.fetch_service` prefetch apply.

The in-memory cache is **disk-authoritative**: ``_load_data_async`` saves the
merged result to disk BEFORE notifying the Tk thread, so the entry in
``full_cache`` already reflects the on-disk file. ``apply_prefetch_result``
therefore reuses that in-memory copy as the merge base instead of re-reading
+ re-parsing the JSONL (~26ms on an 11k-bar file on the Tk thread), and only
falls back to a disk read when the key was never loaded into memory.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from tradinglab.data.fetch_service import FetchService
from tradinglab.disk_cache import merge_candles
from tradinglab.models import Candle


def _candle(i: int, close: float | None = None) -> Candle:
    value = float(i if close is None else close)
    return Candle(
        date=datetime(2024, 1, 1, 9, 30) + timedelta(minutes=5 * i),
        open=value,
        high=value,
        low=value,
        close=value,
        volume=100 + i,
    )


class _FakeDiskCache:
    def __init__(self, existing: list[Candle] | None) -> None:
        self.existing = existing
        self.saved: list[tuple[str, str, str, list[Candle]]] = []
        self.load_calls = 0

    def load(self, source: str, ticker: str, interval: str) -> list[Candle] | None:
        del source, ticker, interval
        self.load_calls += 1
        return self.existing

    def merge_candles(
        self,
        old: list[Candle] | None,
        new: list[Candle] | None,
        *,
        presorted: bool = False,
    ) -> list[Candle]:
        return merge_candles(old, new, presorted=presorted)

    def save(self, source: str, ticker: str, interval: str, candles: list[Candle]) -> None:
        self.saved.append((source, ticker, interval, list(candles)))


def _apply(svc, key, fetched, full_cache, disk):
    stashed: list[list[Candle]] = []
    svc.apply_prefetch_result(
        key,
        fetched=list(fetched),
        full_cache=full_cache,
        disk_cache_mod=disk,
        stash_fn=lambda _key, candles: stashed.append(list(candles)),
    )
    return stashed


def test_apply_prefetch_result_skips_save_when_nothing_new():
    key = ("yfinance", "AAPL", "5m")
    existing = [_candle(0), _candle(1)]
    disk = _FakeDiskCache(existing=list(existing))
    svc = FetchService(worker_count=1)
    try:
        stashed = _apply(svc, key, existing, {key: list(existing)}, disk)
    finally:
        svc.shutdown()
    assert stashed == [existing]
    assert disk.saved == []


def test_apply_prefetch_result_reuses_inmemory_without_disk_read():
    """When the key is in ``full_cache`` the on-disk JSONL is never re-read."""
    key = ("yfinance", "AAPL", "5m")
    current = [_candle(0), _candle(1)]
    disk = _FakeDiskCache(existing=list(current))
    svc = FetchService(worker_count=1)
    try:
        _apply(svc, key, current, {key: list(current)}, disk)
    finally:
        svc.shutdown()
    assert disk.load_calls == 0


def test_apply_prefetch_result_saves_when_fetch_extends_inmemory():
    key = ("yfinance", "AAPL", "5m")
    current = [_candle(0)]
    fetched = [_candle(0), _candle(1)]
    disk = _FakeDiskCache(existing=list(current))
    svc = FetchService(worker_count=1)
    try:
        stashed = _apply(svc, key, fetched, {key: list(current)}, disk)
    finally:
        svc.shutdown()
    assert stashed == [fetched]
    assert len(disk.saved) == 1
    assert disk.saved[0][3] == fetched
    # In-memory copy was authoritative; no redundant disk read.
    assert disk.load_calls == 0


def test_apply_prefetch_result_reads_disk_when_key_absent_from_memory():
    """A watchlist prefetch for a never-viewed ticker falls back to disk."""
    key = ("yfinance", "MSFT", "5m")
    disk_existing = [_candle(0)]
    fetched = [_candle(0), _candle(1)]
    disk = _FakeDiskCache(existing=list(disk_existing))
    svc = FetchService(worker_count=1)
    try:
        stashed = _apply(svc, key, fetched, {}, disk)
    finally:
        svc.shutdown()
    assert disk.load_calls == 1
    assert stashed == [fetched]
    assert len(disk.saved) == 1
    assert disk.saved[0][3] == fetched


# --------------------------------------------------------------------------
# Increment 8a: memory-vs-disk split (Decision 5)
# --------------------------------------------------------------------------
def test_disk_only_skips_memory_stash_but_still_saves():
    """``memory_allowed=False`` (disk-only tiers) persists to disk but does NOT
    stash into the in-memory cache — so watchlist/universe/deep-band prefetch
    can't evict the active-chart working set."""
    key = ("yfinance", "MSFT", "5m")
    current = [_candle(0)]
    fetched = [_candle(0), _candle(1)]
    disk = _FakeDiskCache(existing=list(current))
    svc = FetchService(worker_count=1)
    stashed: list[list[Candle]] = []
    try:
        merged = svc.apply_prefetch_result(
            key, fetched=list(fetched), full_cache={key: list(current)},
            disk_cache_mod=disk,
            stash_fn=lambda _k, c: stashed.append(list(c)),
            memory_allowed=False,
        )
    finally:
        svc.shutdown()
    assert stashed == []                 # no memory stash
    assert len(disk.saved) == 1          # but persisted to disk
    assert disk.saved[0][3] == fetched
    assert merged == fetched             # returns merged bars for the driver


def test_apply_returns_merged_when_memory_allowed():
    key = ("yfinance", "AAPL", "5m")
    current = [_candle(0)]
    fetched = [_candle(0), _candle(1)]
    disk = _FakeDiskCache(existing=list(current))
    svc = FetchService(worker_count=1)
    try:
        merged = svc.apply_prefetch_result(
            key, fetched=list(fetched), full_cache={key: list(current)},
            disk_cache_mod=disk, stash_fn=lambda _k, _c: None,
        )
    finally:
        svc.shutdown()
    assert merged == fetched


def test_apply_returns_none_on_empty_fetch():
    key = ("yfinance", "AAPL", "5m")
    disk = _FakeDiskCache(existing=None)
    svc = FetchService(worker_count=1)
    try:
        merged = svc.apply_prefetch_result(
            key, fetched=[], full_cache={}, disk_cache_mod=disk,
            stash_fn=lambda _k, _c: None,
        )
    finally:
        svc.shutdown()
    assert merged is None
