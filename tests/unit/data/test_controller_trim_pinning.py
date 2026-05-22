"""Regression tests for ``DataController.trim`` ticker-pinning behavior.

The trim path LRU-evicts cache entries to keep ``_full_cache`` bounded.
We pin watchlist tickers AND the active chart ticker so their companion
intervals (e.g. the 5m partner of an active 1d view used by the
volume-TOD overlay and synthetic today-bar) survive eviction caused by
unrelated stashes.

This complements the smoke test
``tests/smoke/test_smoke_volume_tod.py::
test_volume_tod_live_wall_clock_overlay_renders_from_warm_cache``
which exercises the end-to-end path; here we cover the controller
primitive in isolation.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from tradinglab.data.controller import DataController
from tradinglab.models import Candle


def _candles(n: int, start: datetime) -> list[Candle]:
    out = []
    t = start
    for i in range(n):
        out.append(
            Candle(
                date=t,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1000 + i,
                session="regular",
            ),
        )
        t = t + timedelta(minutes=5)
    return out


def test_trim_evicts_unpinned_entries() -> None:
    """Trim removes the LRU non-pinned entry when over capacity."""
    ctl = DataController(full_cache_size=2)
    base = datetime(2026, 1, 1, 9, 30)
    ctl._full_cache[("yfinance", "AAA", "1d")] = _candles(10, base)
    ctl._full_cache[("yfinance", "BBB", "5m")] = _candles(10, base)
    ctl._full_cache[("yfinance", "CCC", "1d")] = _candles(10, base)
    ctl.trim(pinned_tickers=frozenset(), protected_key=("yfinance", "CCC", "1d"))
    assert ("yfinance", "CCC", "1d") in ctl._full_cache
    assert len(ctl._full_cache) == 2


def test_trim_preserves_pinned_ticker_across_intervals() -> None:
    """A pinned ticker's entries survive eviction at any interval."""
    ctl = DataController(full_cache_size=2)
    base = datetime(2026, 1, 1, 9, 30)
    # Active ticker has both 1d (protected) and its 5m companion.
    ctl._full_cache[("yfinance", "ACTIVE", "1d")] = _candles(10, base)
    ctl._full_cache[("yfinance", "ACTIVE", "5m")] = _candles(10, base)
    ctl._full_cache[("yfinance", "OTHER", "1d")] = _candles(10, base)
    ctl.trim(
        pinned_tickers=frozenset({"ACTIVE"}),
        protected_key=("yfinance", "ACTIVE", "1d"),
    )
    assert ("yfinance", "ACTIVE", "1d") in ctl._full_cache
    assert ("yfinance", "ACTIVE", "5m") in ctl._full_cache
    assert ("yfinance", "OTHER", "1d") not in ctl._full_cache


def test_trim_no_op_when_everything_pinned() -> None:
    """If every entry's ticker is pinned, trim cannot evict — exits cleanly."""
    ctl = DataController(full_cache_size=1)
    base = datetime(2026, 1, 1, 9, 30)
    ctl._full_cache[("yfinance", "AAA", "1d")] = _candles(10, base)
    ctl._full_cache[("yfinance", "AAA", "5m")] = _candles(10, base)
    ctl.trim(
        pinned_tickers=frozenset({"AAA"}),
        protected_key=("yfinance", "AAA", "1d"),
    )
    assert len(ctl._full_cache) == 2
