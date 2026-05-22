"""Tests for :class:`tradinglab.data.multi_interval_cache.MultiIntervalCache`.

Covers lazy-load semantics, in-flight de-duplication, error retry,
and the 1m fast path. Pure-logic; no Tk, no real network.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Optional

import pytest

from tradinglab.core.bars_buffer import BarsBuffer
from tradinglab.data.multi_interval_cache import MultiIntervalCache
from tradinglab.models import Candle


# --- helpers ---------------------------------------------------------------


def _mk(
    h: int, m: int, *,
    o: float = 100.0, hi: float = 101.0, lo: float = 99.0,
    c: float = 100.5, v: int = 1000,
    session: str = "regular",
) -> Candle:
    return Candle(
        date=datetime(2026, 5, 4, h, m),
        open=o, high=hi, low=lo, close=c, volume=v, session=session,
    )


def _hist_5m(n: int = 3) -> List[Candle]:
    """A trivial 3-bar 5m history starting at 09:30."""
    return [
        Candle(
            date=datetime(2026, 5, 4, 9, 30 + 5 * i),
            open=100.0 + i, high=101.0 + i, low=99.0 + i,
            close=100.5 + i, volume=10_000 + i,
            session="regular",
        )
        for i in range(n)
    ]


# --- lazy load --------------------------------------------------------------


def test_sync_executor_first_call_returns_none_then_buffer():
    calls = []

    def fetch(sym, iv):
        calls.append((sym, iv))
        return _hist_5m(3)

    cache = MultiIntervalCache(fetch_history=fetch, executor=None)
    # First call: triggers sync fetch, returns None.
    assert cache.get_bars("AAPL", "5m") is None
    # The buffer was actually populated synchronously during the first
    # call; the second call returns it.
    buf = cache.get_bars("AAPL", "5m")
    assert isinstance(buf, BarsBuffer)
    assert len(buf) == 3
    assert calls == [("AAPL", "5m")]


def test_async_executor_arrival_callback_fires():
    arrived: List = []
    arrival_event = threading.Event()

    def on_arrival(sym, iv):
        arrived.append((sym, iv))
        arrival_event.set()

    def fetch(sym, iv):
        time.sleep(0.05)
        return _hist_5m(2)

    with ThreadPoolExecutor(max_workers=1) as ex:
        cache = MultiIntervalCache(
            fetch_history=fetch, executor=ex, on_arrival=on_arrival,
        )
        # First call returns None; fetch is queued.
        assert cache.get_bars("AAPL", "5m") is None
        # Wait for the arrival callback.
        assert arrival_event.wait(timeout=2.0)
    assert arrived == [("AAPL", "5m")]
    # Subsequent get_bars returns the populated buffer.
    buf = cache.get_bars("AAPL", "5m")
    assert isinstance(buf, BarsBuffer)
    assert len(buf) == 2


def test_inflight_dedup_does_not_resubmit():
    fetch_count = 0
    started = threading.Event()
    release = threading.Event()

    def fetch(sym, iv):
        nonlocal fetch_count
        fetch_count += 1
        started.set()
        release.wait(timeout=2.0)
        return _hist_5m(1)

    with ThreadPoolExecutor(max_workers=1) as ex:
        cache = MultiIntervalCache(fetch_history=fetch, executor=ex)
        cache.get_bars("AAPL", "5m")
        assert started.wait(timeout=2.0)
        # While the first fetch is still running, repeat calls must
        # not re-submit.
        for _ in range(5):
            assert cache.get_bars("AAPL", "5m") is None
        assert fetch_count == 1
        release.set()


def test_failed_fetch_returns_none_clears_inflight_and_retries():
    attempts: List = []

    def fetch(sym, iv):
        attempts.append((sym, iv))
        # First attempt returns None (e.g. network blip); second
        # returns real data.
        if len(attempts) == 1:
            return None
        return _hist_5m(1)

    cache = MultiIntervalCache(fetch_history=fetch, executor=None)
    assert cache.get_bars("AAPL", "5m") is None
    # First fetch returned None — buffer is still missing, so a fresh
    # call retries.
    assert cache.get_bars("AAPL", "5m") is None
    # Now the second fetch (which we just kicked off) ran sync and
    # populated the buffer.
    assert cache.get_bars("AAPL", "5m") is not None
    assert len(attempts) == 2


def test_failed_fetch_raising_clears_inflight_and_retries():
    attempts: List = []

    def fetch(sym, iv):
        attempts.append((sym, iv))
        if len(attempts) == 1:
            raise RuntimeError("simulated network error")
        return _hist_5m(1)

    cache = MultiIntervalCache(fetch_history=fetch, executor=None)
    # The exception is caught & logged inside the cache.
    assert cache.get_bars("AAPL", "5m") is None
    # Retry path runs.
    assert cache.get_bars("AAPL", "5m") is None
    assert cache.get_bars("AAPL", "5m") is not None
    assert len(attempts) == 2


# --- 1m fast path -----------------------------------------------------------


def test_on_1m_tick_populates_1m_buffer_without_fetch():
    fetch_calls = []

    def fetch(sym, iv):
        fetch_calls.append((sym, iv))
        return None

    cache = MultiIntervalCache(fetch_history=fetch, executor=None)
    cache.on_1m_tick("AAPL", _mk(9, 30, v=100), forming=False)
    cache.on_1m_tick("AAPL", _mk(9, 31, v=200), forming=False)

    buf = cache.get_bars("AAPL", "1m")
    assert isinstance(buf, BarsBuffer)
    assert len(buf) == 2
    # No backfill should have been attempted for the 1m path.
    assert fetch_calls == []


def test_on_1m_tick_forming_then_closed_updates_last_row():
    cache = MultiIntervalCache()
    c = _mk(9, 30, c=100.0, v=10)
    cache.on_1m_tick("AAPL", c, forming=True)
    # Mutate same candle and re-send (simulating live forming updates).
    c.close = 102.0
    c.volume = 25
    cache.on_1m_tick("AAPL", c, forming=True)
    cache.on_1m_tick("AAPL", c, forming=False)
    buf = cache.get_bars("AAPL", "1m")
    assert buf is not None
    assert len(buf) == 1
    bars = buf.view(candles=None)
    assert bars.close[-1] == 102.0
    assert bars.volume[-1] == 25


# --- fan-out to resamplers --------------------------------------------------


def test_on_1m_tick_propagates_to_higher_interval_buffers():
    """A 5m buffer that's been backfilled gets updated by 1m fan-out."""
    # Pre-populate with one 5m bar at 09:25 so the resampler tracks an
    # earlier bucket; subsequent 1m ticks at 09:30..09:35 should append
    # the closed 09:30 bar.
    seed = [
        Candle(
            date=datetime(2026, 5, 4, 9, 25),
            open=99.0, high=100.0, low=98.0, close=99.5, volume=500,
            session="pre",
        )
    ]

    def fetch(sym, iv):
        return list(seed)

    cache = MultiIntervalCache(fetch_history=fetch, executor=None)
    # Lazy-load the 5m buffer (sync).
    cache.get_bars("AAPL", "5m")
    buf = cache.get_bars("AAPL", "5m")
    assert buf is not None
    assert len(buf) == 1

    # Now stream 1m ticks for 09:30..09:34 then 09:35.
    for m in range(30, 35):
        cache.on_1m_tick(
            "AAPL",
            _mk(9, m, o=100.0 + (m - 30) * 0.1, hi=101.0, lo=99.0,
                c=100.5, v=100),
            forming=False,
        )
    cache.on_1m_tick(
        "AAPL", _mk(9, 35, o=99.5, hi=100.0, lo=98.5, c=99.8, v=120),
        forming=False,
    )

    buf = cache.get_bars("AAPL", "5m")
    assert buf is not None
    # Original seed (09:25) + closed 09:30 + forming 09:35
    assert len(buf) == 3
    bars = buf.view(candles=None)
    assert int(bars.volume[1]) == 500   # 5×100
    # The 09:35 forming row reflects the single 1m so far
    assert int(bars.volume[2]) == 120


# --- set_bars / clear / stats ----------------------------------------------


def test_set_bars_injects_buffer_manually():
    cache = MultiIntervalCache()
    cache.set_bars("AAPL", "5m", _hist_5m(4))
    buf = cache.get_bars("AAPL", "5m")
    assert isinstance(buf, BarsBuffer)
    assert len(buf) == 4


def test_clear_empties_everything():
    cache = MultiIntervalCache(
        fetch_history=lambda s, i: _hist_5m(2), executor=None,
    )
    cache.get_bars("AAPL", "5m")
    cache.on_1m_tick("AAPL", _mk(9, 30), forming=False)
    assert cache.stats()["buffers"] >= 1
    cache.clear()
    s = cache.stats()
    assert s == {
        "buffers": 0, "resamplers": 0, "inflight": 0, "candles_total": 0,
    }


def test_stats_returns_sane_counters():
    cache = MultiIntervalCache(
        fetch_history=lambda s, i: _hist_5m(3), executor=None,
    )
    cache.get_bars("AAPL", "5m")
    # 1m tick at 09:45 — after the historical 5m bars (09:30/35/40) so
    # the resampler appends a fresh forming 5m bar at 09:45.
    cache.on_1m_tick("AAPL", _mk(9, 45), forming=False)
    s = cache.stats()
    assert s["buffers"] == 2          # 5m + 1m
    assert s["resamplers"] == 1       # 5m only
    assert s["inflight"] == 0
    # 5m buffer: 3 historical + 1 forming = 4. 1m buffer: 1 = 1. Total 5.
    assert s["candles_total"] == 5
