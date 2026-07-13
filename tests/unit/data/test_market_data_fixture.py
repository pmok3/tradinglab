"""Integrity tests for the committed real-market-data fixtures.

Pins the ``testdata`` snapshot captured by ``tools/fetch_test_fixtures.py``:
6 tickers, 5 RTH trading days of 5m bars each, real yfinance prices. Fast,
offline, no GUI — just validates the committed data loads and has the shape
the end-to-end strategy smoke check (``check_st9``) relies on.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tests._fixtures.market_data import (
    INTERVAL,
    TICKERS,
    available,
    fetcher,
    load,
    manifest,
)

_ET = ZoneInfo("America/New_York")


def test_all_tickers_present():
    assert set(TICKERS) == {"SPY", "AMD", "NVDA", "INTC", "MSFT", "AAPL"}
    for t in TICKERS:
        assert available(t), f"missing committed fixture for {t}"


@pytest.mark.parametrize("ticker", TICKERS)
def test_fixture_shape_and_realism(ticker):
    candles = load(ticker, INTERVAL)
    # 5 RTH days x 78 five-minute bars = 390.
    assert len(candles) == 390, f"{ticker}: expected 390 RTH bars, got {len(candles)}"
    # Chronological, RTH-only, tz-aware ET, positive prices, valid OHLC.
    prev = None
    days = set()
    for c in candles:
        assert c.session == "regular", f"{ticker}: non-RTH bar leaked in"
        assert c.date.tzinfo is not None, f"{ticker}: naive datetime"
        et = c.date.astimezone(_ET)
        assert (9, 30) <= (et.hour, et.minute) <= (16, 0), f"{ticker}: bar outside RTH"
        days.add(et.date())
        assert c.open > 0 and c.high > 0 and c.low > 0 and c.close > 0
        assert c.high >= max(c.open, c.close) and c.low <= min(c.open, c.close)
        assert c.volume >= 0
        if prev is not None:
            assert c.date >= prev, f"{ticker}: bars not chronological"
        prev = c.date
    assert len(days) == 5, f"{ticker}: expected 5 distinct RTH days, got {len(days)}"


def test_fetcher_gates_unknown_symbol_and_interval():
    assert fetcher("SPY", INTERVAL) is not None
    assert fetcher("ZZZZ", INTERVAL) is None      # unknown ticker
    assert fetcher("SPY", "1d") is None           # not the captured interval
    # Returns a fresh list (mutating it must not corrupt the cache).
    a = fetcher("SPY", INTERVAL)
    a.clear()
    assert len(fetcher("SPY", INTERVAL)) == 390


def test_manifest_provenance():
    m = manifest()
    assert m.get("source") == "yfinance"
    assert m.get("interval") == INTERVAL
    assert set(m.get("tickers", {})) == set(TICKERS)
    # captured_at parses as an ISO datetime.
    datetime.fromisoformat(m["captured_at"])
    for t in TICKERS:
        assert m["tickers"][t]["bars"] == 390
        assert len(m["tickers"][t]["days"]) == 5
