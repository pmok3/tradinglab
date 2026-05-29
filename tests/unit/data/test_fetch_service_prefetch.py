"""Unit tests for :mod:`tradinglab.data.fetch_service` prefetch apply."""

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

    def load(self, source: str, ticker: str, interval: str) -> list[Candle] | None:
        del source, ticker, interval
        return self.existing

    def merge_candles(
        self,
        old: list[Candle] | None,
        new: list[Candle] | None,
    ) -> list[Candle]:
        return merge_candles(old, new)

    def save(self, source: str, ticker: str, interval: str, candles: list[Candle]) -> None:
        self.saved.append((source, ticker, interval, list(candles)))


def test_apply_prefetch_result_skips_save_when_disk_is_unchanged():
    key = ("yfinance", "AAPL", "5m")
    existing = [_candle(0), _candle(1)]
    disk = _FakeDiskCache(existing=list(existing))
    stashed: list[list[Candle]] = []
    svc = FetchService(worker_count=1)
    try:
        svc.apply_prefetch_result(
            key,
            fetched=list(existing),
            full_cache={key: list(existing)},
            disk_cache_mod=disk,
            stash_fn=lambda _key, candles: stashed.append(list(candles)),
        )
    finally:
        svc.shutdown()

    assert stashed == [existing]
    assert disk.saved == []


def test_apply_prefetch_result_saves_when_memory_has_newer_bars_than_disk():
    key = ("yfinance", "AAPL", "5m")
    disk_existing = [_candle(0)]
    current = [_candle(0), _candle(1)]
    disk = _FakeDiskCache(existing=list(disk_existing))
    stashed: list[list[Candle]] = []
    svc = FetchService(worker_count=1)
    try:
        svc.apply_prefetch_result(
            key,
            fetched=list(current),
            full_cache={key: list(current)},
            disk_cache_mod=disk,
            stash_fn=lambda _key, candles: stashed.append(list(candles)),
        )
    finally:
        svc.shutdown()

    assert stashed == [current]
    assert len(disk.saved) == 1
    assert disk.saved[0][3] == current
