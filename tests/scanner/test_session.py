"""Tests for :mod:`tradinglab.scanner.session`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import numpy as np

from tradinglab.core.bars import Bars
from tradinglab.models import Candle
from tradinglab.scanner.session import find_session_open_index


def _make_intraday(days: int, bars_per_day: int = 5,
                   interval_min: int = 5) -> Bars:
    """``days * bars_per_day`` intraday candles starting 9:30 AM UTC."""
    out: List[Candle] = []
    base = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)
    for d in range(days):
        for b in range(bars_per_day):
            ts = base + timedelta(days=d, minutes=b * interval_min)
            out.append(Candle(date=ts, open=100.0, high=101.0, low=99.0,
                              close=100.0, volume=1000.0, session="regular"))
    return Bars.from_candles(out)


def test_find_session_open_first_bar_of_session_returns_self():
    b = _make_intraday(days=1, bars_per_day=10)
    # The first bar of the only session is its own session-open.
    assert find_session_open_index(b, 0) == 0


def test_find_session_open_intraday_returns_first_bar_of_today():
    b = _make_intraday(days=2, bars_per_day=5)
    # Bars 0..4 are day 1, bars 5..9 are day 2.
    # Session-open of bar 9 should be bar 5.
    assert find_session_open_index(b, 9) == 5
    # Session-open of bar 7 should also be bar 5.
    assert find_session_open_index(b, 7) == 5
    # Session-open of bar 5 should be itself.
    assert find_session_open_index(b, 5) == 5


def test_find_session_open_yesterday_walks_to_yesterdays_first():
    b = _make_intraday(days=2, bars_per_day=5)
    # Bar 4 is yesterday's last bar; session-open should be bar 0.
    assert find_session_open_index(b, 4) == 0
    assert find_session_open_index(b, 2) == 0


def test_find_session_open_three_day_buffer():
    b = _make_intraday(days=3, bars_per_day=4)
    # Bars 0..3 day1, 4..7 day2, 8..11 day3.
    assert find_session_open_index(b, 11) == 8
    assert find_session_open_index(b, 8) == 8
    assert find_session_open_index(b, 7) == 4
    assert find_session_open_index(b, 4) == 4
    assert find_session_open_index(b, 3) == 0


def test_find_session_open_daily_interval_returns_self():
    # Daily candles: each on its own UTC date, so session-open is
    # always the bar itself (no clamp).
    out: List[Candle] = []
    base = datetime(2026, 5, 4, 0, 0, tzinfo=timezone.utc)
    for d in range(10):
        ts = base + timedelta(days=d)
        out.append(Candle(date=ts, open=100.0, high=101.0, low=99.0,
                          close=100.0, volume=1000.0, session="regular"))
    b = Bars.from_candles(out)
    for i in range(10):
        assert find_session_open_index(b, i) == i


def test_find_session_open_out_of_range_returns_unchanged():
    b = _make_intraday(days=1, bars_per_day=5)
    assert find_session_open_index(b, -1) == -1
    assert find_session_open_index(b, 999) == 999


def test_find_session_open_empty_bars_returns_unchanged():
    b = Bars.from_candles([])
    assert find_session_open_index(b, 0) == 0
    assert find_session_open_index(b, 5) == 5
