"""Phase 2d equivalence: VWAP / AVWAP — compute(candles) == compute_arr(bars)."""

from __future__ import annotations

import datetime as dt
import random
from typing import List

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators.avwap import AnchoredVWAP
from tradinglab.indicators.vwap import VWAP
from tradinglab.models import Candle


def _intraday_candles(days: int = 3, seed: int = 9,
                      include_pre: bool = True) -> list[Candle]:
    rng = random.Random(seed)
    out: list[Candle] = []
    base_day = dt.date(2024, 3, 4)
    for d in range(days):
        day = base_day + dt.timedelta(days=d)
        if include_pre:
            for i in range(6):
                t = dt.datetime.combine(day, dt.time(9, 0)) + dt.timedelta(minutes=5 * i)
                out.append(Candle(
                    date=t, open=100.0, high=100.5, low=99.5, close=100.1,
                    volume=rng.randint(50, 200), session="pre",
                ))
        for i in range(78):
            t = dt.datetime.combine(day, dt.time(9, 30)) + dt.timedelta(minutes=5 * i)
            o = 100.0 + rng.uniform(-1, 1)
            c_ = o + rng.uniform(-0.5, 0.5)
            h = max(o, c_) + abs(rng.uniform(0, 0.3))
            l = min(o, c_) - abs(rng.uniform(0, 0.3))
            out.append(Candle(
                date=t, open=o, high=h, low=l, close=c_,
                volume=rng.randint(100, 1500), session="regular",
            ))
    return out


@pytest.mark.parametrize("source", ["typical", "close", "ohlc4"])
def test_vwap_equivalence(source):
    candles = _intraday_candles()
    bars = Bars.from_candles(candles)
    ind = VWAP(price_source=source)
    a = ind.compute(candles)["vwap"]
    b = ind.compute_arr(bars)["vwap"]
    np.testing.assert_allclose(a, b, rtol=1e-12, atol=1e-12, equal_nan=True)


@pytest.mark.parametrize("bands", ["off", "1σ", "2σ", "both"])
@pytest.mark.parametrize("source", ["typical", "close"])
def test_avwap_equivalence(bands, source):
    candles = _intraday_candles()
    # Anchor 30 minutes after first regular candle to exercise start_idx.
    anchor = "2024-03-04T10:00:00"
    bars = Bars.from_candles(candles)
    ind = AnchoredVWAP(anchor_ts=anchor, bands=bands, price_source=source)
    a = ind.compute(candles)
    b = ind.compute_arr(bars)
    assert a.keys() == b.keys()
    for k in a:
        np.testing.assert_allclose(a[k], b[k], rtol=1e-12, atol=1e-12, equal_nan=True)


def test_vwap_avwap_empty():
    for cls in (VWAP, AnchoredVWAP):
        ind = cls()
        a = ind.compute([])
        b = ind.compute_arr(Bars.from_candles([]))
        for k in a:
            np.testing.assert_array_equal(a[k], b[k])


def test_vwap_daily_returns_nan():
    daily = [
        Candle(date=dt.datetime(2024, 1, 2 + i), open=100.0, high=101.0,
               low=99.0, close=100.5, volume=10000, session="regular")
        for i in range(20)
    ]
    bars = Bars.from_candles(daily)
    ind = VWAP()
    out = ind.compute_arr(bars)["vwap"]
    assert np.all(np.isnan(out))
