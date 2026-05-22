"""Phase 2a equivalence: compute(candles) == compute_arr(Bars.from_candles(candles)).

For each migrated indicator, assert the new array-native ``compute_arr``
path returns array-equal output to the legacy ``compute(candles)`` path.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from typing import List

import numpy as np
import pytest

from tradinglab.core.bars import Bars
from tradinglab.indicators.adx import ADX
from tradinglab.indicators.atr import ATR
from tradinglab.indicators.bollinger import BollingerBands
from tradinglab.indicators.lrsi import LRSI
from tradinglab.indicators.moving_averages import EMA, SMA
from tradinglab.indicators.rsi import RSI
from tradinglab.indicators.smi import SMI
from tradinglab.models import Candle


def _make_candles(n: int, seed: int = 42) -> list[Candle]:
    rng = random.Random(seed)
    out: list[Candle] = []
    base = 100.0
    t0 = dt.datetime(2024, 1, 2, 9, 30)
    for i in range(n):
        delta = rng.uniform(-1.0, 1.0)
        o = base
        c = max(0.5, base + delta)
        h = max(o, c) + abs(rng.uniform(0, 0.5))
        l = min(o, c) - abs(rng.uniform(0, 0.5))
        out.append(
            Candle(
                date=t0 + dt.timedelta(minutes=i),
                open=o, high=h, low=l, close=c,
                volume=int(rng.randint(1000, 5000)),
                session="regular",
            )
        )
        base = c
    return out


def _eq(a: dict, b: dict) -> None:
    assert a.keys() == b.keys()
    for k in a:
        np.testing.assert_array_equal(a[k], b[k], err_msg=f"key {k}")


@pytest.mark.parametrize("n", [0, 1, 5, 50, 200])
@pytest.mark.parametrize(
    "indicator",
    [
        SMA(length=10),
        EMA(length=10),
        RSI(length=14),
        BollingerBands(length=20, std_length=20, num_std=2.0),
        ATR(length=14),  # rolling default
        ADX(length=14),
        SMI(length=10, smooth1=3, smooth2=3, signal_length=3),
        LRSI(gamma=0.5),
    ],
    ids=lambda x: type(x).__name__,
)
def test_compute_arr_matches_compute(indicator, n):
    candles = _make_candles(n)
    bars = Bars.from_candles(candles)
    a = indicator.compute(candles)
    b = indicator.compute_arr(bars)
    _eq(a, b)
