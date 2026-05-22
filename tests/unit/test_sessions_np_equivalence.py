"""Phase 2b equivalence: session helpers — candle vs Bars-view paths."""

from __future__ import annotations

import datetime as dt
import random
from typing import List

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators.sessions import (
    is_intraday,
    is_intraday_np,
    session_filter_mask_np,
    session_filter_predicate,
    session_groups,
    session_groups_np,
    tod_key,
    tod_key_np,
)
from tradinglab.models import Candle


def _intraday_candles(days: int = 3, per_day: int = 78, seed: int = 7) -> List[Candle]:
    rng = random.Random(seed)
    out: List[Candle] = []
    base_day = dt.date(2024, 1, 2)
    for d in range(days):
        day = base_day + dt.timedelta(days=d)
        for i in range(per_day):
            t = dt.datetime.combine(day, dt.time(9, 30)) + dt.timedelta(minutes=5 * i)
            sess = "regular"
            if i < 6:
                sess = "pre"
            elif i > per_day - 6:
                sess = "post"
            out.append(Candle(
                date=t, open=100.0, high=100.5, low=99.5, close=100.1,
                volume=rng.randint(100, 1000), session=sess,
            ))
    return out


def _daily_candles(n: int = 50) -> List[Candle]:
    out: List[Candle] = []
    base = dt.datetime(2023, 1, 3)
    for i in range(n):
        out.append(Candle(
            date=base + dt.timedelta(days=i),
            open=100.0, high=101.0, low=99.0, close=100.5,
            volume=10_000, session="regular",
        ))
    return out


@pytest.mark.parametrize("regular_only", [True, False])
def test_session_groups_np_matches_candle(regular_only):
    candles = _intraday_candles()
    bars = Bars.from_candles(candles)
    py_groups = session_groups(candles, regular_only=regular_only)
    np_groups = session_groups_np(bars, regular_only=regular_only)
    assert len(py_groups) == len(np_groups)
    for a, b in zip(py_groups, np_groups):
        np.testing.assert_array_equal(np.asarray(a, dtype=np.int64), b)


def test_session_groups_np_empty():
    assert session_groups_np(Bars.from_candles([])) == []


@pytest.mark.parametrize("candles_factory", [_intraday_candles, _daily_candles])
def test_is_intraday_np_matches_candle(candles_factory):
    candles = candles_factory()
    bars = Bars.from_candles(candles)
    assert is_intraday(candles) == is_intraday_np(bars)


def test_is_intraday_np_empty_and_short():
    assert is_intraday_np(Bars.from_candles([])) is False
    assert is_intraday_np(Bars.from_candles(_intraday_candles()[:1])) is False


@pytest.mark.parametrize("mode", ["regular_only", "regular_plus_premarket", "extended", "weird_unknown"])
def test_session_filter_mask_np_matches_predicate(mode):
    candles = _intraday_candles()
    bars = Bars.from_candles(candles)
    pred = session_filter_predicate(mode)
    expected = np.array([pred(c) for c in candles], dtype=bool)
    actual = session_filter_mask_np(bars, mode)
    np.testing.assert_array_equal(actual, expected)


def test_tod_key_np_matches_tuple():
    candles = _intraday_candles()
    bars = Bars.from_candles(candles)
    keys_np = tod_key_np(bars)
    for i, c in enumerate(candles):
        h, m = tod_key(c)  # type: ignore[misc]
        assert keys_np[i] == h * 60 + m
