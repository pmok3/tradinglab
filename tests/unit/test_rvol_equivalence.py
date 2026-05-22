"""Equivalence: unified RVOL family — ``compute(candles) == compute_arr(bars)``.

Generates intraday fixtures with multiple sessions so the warmup gate
(``≥ _MIN_WARMUP_SESSIONS`` prior sessions) clears for cum/tod modes.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import List

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators.rvol import RVOL
from tradinglab.models import Candle


def _intraday_candles(days: int = 12, seed: int = 5,
                      include_pre: bool = False) -> List[Candle]:
    rng = random.Random(seed)
    out: List[Candle] = []
    base_day = dt.date(2024, 1, 2)
    for d in range(days):
        day = base_day + dt.timedelta(days=d)
        if include_pre:
            for i in range(6):
                t = dt.datetime.combine(day, dt.time(9, 0)) + dt.timedelta(minutes=5 * i)
                out.append(Candle(
                    date=t, open=100.0, high=100.5, low=99.5, close=100.1,
                    volume=rng.randint(50, 200), session="pre",
                ))
        for i in range(78):  # 6.5h regular
            t = dt.datetime.combine(day, dt.time(9, 30)) + dt.timedelta(minutes=5 * i)
            out.append(Candle(
                date=t, open=100.0, high=100.5, low=99.5, close=100.1,
                volume=rng.randint(100, 1500), session="regular",
            ))
    return out


@pytest.mark.parametrize("aggregator", ["mean", "median"])
@pytest.mark.parametrize("session_filter",
                         ["regular_only", "regular_plus_premarket"])
def test_simple_rolling_rvol_equivalence(aggregator, session_filter):
    candles = _intraday_candles(days=4, include_pre=True)
    bars = Bars.from_candles(candles)
    ind = RVOL(mode="simple", length=10, aggregator=aggregator,
               session_filter=session_filter)
    a = ind.compute(candles)
    b = ind.compute_arr(bars)
    np.testing.assert_array_equal(a["rvol"], b["rvol"])


@pytest.mark.parametrize("aggregator", ["mean", "median"])
def test_tod_rvol_equivalence(aggregator):
    candles = _intraday_candles(days=12)
    bars = Bars.from_candles(candles)
    ind = RVOL(mode="time_of_day", length=5, aggregator=aggregator)
    a = ind.compute(candles)
    b = ind.compute_arr(bars)
    np.testing.assert_array_equal(a["rvol"], b["rvol"])


@pytest.mark.parametrize("aggregator", ["mean", "median"])
def test_cumulative_rvol_equivalence(aggregator):
    candles = _intraday_candles(days=12)
    bars = Bars.from_candles(candles)
    ind = RVOL(mode="cumulative", length=5, aggregator=aggregator)
    a = ind.compute(candles)
    b = ind.compute_arr(bars)
    np.testing.assert_array_equal(a["rvol"], b["rvol"])


def test_rvol_empty_and_short():
    for mode in ("simple", "time_of_day", "cumulative"):
        ind = RVOL(mode=mode)
        # Empty
        np.testing.assert_array_equal(
            ind.compute([])["rvol"],
            ind.compute_arr(Bars.from_candles([]))["rvol"],
        )
        # Too few sessions
        short = _intraday_candles(days=2)
        a = ind.compute(short)["rvol"]
        b = ind.compute_arr(Bars.from_candles(short))["rvol"]
        np.testing.assert_array_equal(a, b)
