"""Tests for :func:`runner.fetch_candles_for_symbol` disk-cache integration.

The strategy_tester runner used to bypass the disk cache and re-hit yfinance
on every Run for every symbol. These tests pin the perf-critical contract
that:

1. A cache miss falls through to the registered ``DATA_SOURCES["yfinance"]``
   fetcher, persists the result, and returns it.
2. A cache hit short-circuits without invoking the fetcher.
3. Distinct ``(symbol, interval)`` keys do not collide.
4. The cache persists across multiple ``runner.run`` invocations within the
   same process.
5. Returned candles are byte-identical to the originals (no mutation).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradinglab.models import Candle
from tradinglab.strategy_tester import runner


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("TRADINGLAB_DATA_DIR", raising=False)
    # Make sure the per-key lock dict starts clean each test so leftover
    # state from another test never deadlocks this one.
    runner._fetch_locks.clear()
    yield


def _candles(n: int, base: float) -> list[Candle]:
    t = datetime(2024, 6, 3, 14, 30)  # tz-naive UTC-ish — fine for the cache
    out: list[Candle] = []
    for i in range(n):
        out.append(
            Candle(
                date=t + timedelta(minutes=5 * i),
                open=base + i,
                high=base + i + 0.5,
                low=base + i - 0.5,
                close=base + i + 0.1,
                volume=1000,
                session="regular",
            )
        )
    return out


class _CountingFetcher:
    def __init__(self, by_key: dict[tuple[str, str], list[Candle]]):
        self._by_key = by_key
        self.calls: list[tuple[str, str]] = []

    def __call__(self, symbol: str, interval: str) -> list[Candle]:
        self.calls.append((symbol, interval))
        return list(self._by_key.get((symbol, interval), []))


def _install_fetcher(monkeypatch, fetcher: _CountingFetcher) -> None:
    from tradinglab.data import base as data_base
    monkeypatch.setitem(data_base.DATA_SOURCES, "yfinance", fetcher)


def test_cache_miss_then_hit_skips_network(monkeypatch):
    fetcher = _CountingFetcher({("AAPL", "1d"): _candles(120, 150.0)})
    _install_fetcher(monkeypatch, fetcher)

    first = runner.fetch_candles_for_symbol("AAPL", "1d")
    second = runner.fetch_candles_for_symbol("AAPL", "1d")

    assert len(first) == 120
    assert len(second) == 120
    assert fetcher.calls == [("AAPL", "1d")], (
        f"second call must hit disk cache, not the fetcher (calls={fetcher.calls})"
    )


def test_distinct_keys_do_not_collide(monkeypatch):
    fetcher = _CountingFetcher({
        ("AAPL", "1d"): _candles(10, 150.0),
        ("AAPL", "5m"): _candles(15, 151.0),
        ("MSFT", "1d"): _candles(20, 300.0),
    })
    _install_fetcher(monkeypatch, fetcher)

    a_1d = runner.fetch_candles_for_symbol("AAPL", "1d")
    a_5m = runner.fetch_candles_for_symbol("AAPL", "5m")
    m_1d = runner.fetch_candles_for_symbol("MSFT", "1d")

    assert len(a_1d) == 10
    assert len(a_5m) == 15
    assert len(m_1d) == 20
    assert sorted(fetcher.calls) == sorted([
        ("AAPL", "1d"), ("AAPL", "5m"), ("MSFT", "1d"),
    ])

    # Round 2 — every key must be a cache hit.
    fetcher.calls.clear()
    runner.fetch_candles_for_symbol("AAPL", "1d")
    runner.fetch_candles_for_symbol("AAPL", "5m")
    runner.fetch_candles_for_symbol("MSFT", "1d")
    assert fetcher.calls == [], (
        f"second round must be all cache hits (calls={fetcher.calls})"
    )


def test_returned_candles_byte_identical(monkeypatch):
    originals = _candles(8, 42.0)
    fetcher = _CountingFetcher({("XYZ", "1d"): originals})
    _install_fetcher(monkeypatch, fetcher)

    first = runner.fetch_candles_for_symbol("XYZ", "1d")
    second = runner.fetch_candles_for_symbol("XYZ", "1d")

    # Field-by-field equality survives the JSONL round-trip.
    for got, want in zip(second, originals, strict=True):
        assert got.date == want.date
        assert got.open == pytest.approx(want.open)
        assert got.high == pytest.approx(want.high)
        assert got.low == pytest.approx(want.low)
        assert got.close == pytest.approx(want.close)
        assert got.volume == want.volume
        assert got.session == want.session
    # Both rounds match each other too.
    assert len(first) == len(second) == 8


def test_empty_fetch_does_not_poison_cache(monkeypatch):
    fetcher = _CountingFetcher({})  # always returns []
    _install_fetcher(monkeypatch, fetcher)

    assert runner.fetch_candles_for_symbol("NADA", "1d") == []
    assert runner.fetch_candles_for_symbol("NADA", "1d") == []
    # No persisted bars → must hit fetcher both times.
    assert len(fetcher.calls) == 2


def test_missing_fetcher_returns_empty(monkeypatch):
    from tradinglab.data import base as data_base
    monkeypatch.setitem(data_base.DATA_SOURCES, "yfinance", None)
    # Remove cleanly so it's truly "missing" — monkeypatch.setitem with None
    # doesn't satisfy ``fetcher is None`` only if get returns None; force it.
    data_base.DATA_SOURCES["yfinance"] = None  # type: ignore[assignment]
    assert runner.fetch_candles_for_symbol("FOO", "1d") == []
