"""Bollinger Bands.

A volatility band consisting of three lines:

- ``middle``: a moving average (``ma_type``) over closes (``length``).
- ``upper`` : middle + ``num_std`` × rolling stddev.
- ``lower`` : middle − ``num_std`` × rolling stddev.

The stddev is the **population** stddev of closes over the
``std_length`` window (matches the original Bollinger formulation;
numpy ``std`` default ``ddof=0``). The first ``max(length, std_length)
- 1`` entries are ``NaN`` for all three outputs.

The centerline can be any of four moving-average kernels selected via
the ``ma_type`` parameter (a dropdown in the indicator dialog):

* ``"SMA"`` — classic simple moving average (default).
* ``"EMA"`` — exponential moving average, ``alpha = 2/(N+1)``.
* ``"WMA"`` — linearly-weighted moving average.
* ``"RMA"`` — Wilder's recursive moving average (``alpha = 1/N``).

Regardless of ``ma_type``, the standard-deviation envelope is always
computed as the population stddev of closes over ``std_length`` —
this matches every charting platform's convention for EMA-based
Bollinger Bands.

All three lines are overlay-class — they live on the price axes.
"""

from __future__ import annotations

from typing import ClassVar, Dict, List, Optional, Tuple

import numpy as np

from ..core.bars import Bars
from ..models import Candle
from .base import LineStyle, ParamDef
from .ma_kernels import MA_TYPES, apply_ma

# Per-MA color palette so a chart with multiple BB configs reads at a
# glance. Defaults override only when the user didn't already set a
# style; see :meth:`__init__`.
_DEFAULT_COLOR_BY_MA: Dict[str, str] = {
    "SMA": "#2ca02c",  # green
    "EMA": "#d62728",  # red (matches the previous BB-EMA hue)
    "WMA": "#9467bd",  # purple
    "RMA": "#1f77b4",  # blue
}


class BollingerBands:
    kind_id: ClassVar[str] = "bbands"
    kind_version: ClassVar[int] = 3
    params_schema: ClassVar[Tuple[ParamDef, ...]] = (
        ParamDef("length", "int", default=20, min=2, max=2000, step=1,
                 description="Length"),
        ParamDef("num_std", "float", default=2.0, min=0.1, max=10.0, step=0.1,
                 description="Std devs"),
        ParamDef("std_length", "int", default=20, min=2, max=2000, step=1,
                 description="σ window"),
        ParamDef("ma_type", "choice", default="SMA",
                 choices=MA_TYPES,
                 description="Moving Average"),
    )
    default_style: ClassVar[Dict[str, LineStyle]] = {
        # SMA-default colors. Per-instance overrides applied in
        # __init__ when a non-SMA ma_type is picked.
        "middle": LineStyle(color=_DEFAULT_COLOR_BY_MA["SMA"], width=1.2),
        "upper":  LineStyle(color=_DEFAULT_COLOR_BY_MA["SMA"], width=1.0),
        "lower":  LineStyle(color=_DEFAULT_COLOR_BY_MA["SMA"], width=1.0),
    }

    overlay = True

    def __init__(self, length: int = 20, num_std: float = 2.0,
                 std_length: Optional[int] = None,
                 ma_type: str = "SMA") -> None:
        if length < 2:
            raise ValueError("length must be >= 2")
        if num_std <= 0:
            raise ValueError("num_std must be > 0")
        if std_length is None:
            std_length = length
        if std_length < 2:
            raise ValueError("std_length must be >= 2")
        ma_type_norm = str(ma_type).upper()
        if ma_type_norm not in MA_TYPES:
            raise ValueError(
                f"ma_type must be one of {MA_TYPES}; got {ma_type!r}"
            )
        self.length = int(length)
        self.num_std = float(num_std)
        self.std_length = int(std_length)
        self.ma_type = ma_type_norm
        # Display name encodes ma_type only when it's not the default
        # SMA — preserves "BB(20,2)" in screenshots / docs.
        ma_tag = "" if self.ma_type == "SMA" else f",{self.ma_type}"
        if self.std_length == self.length:
            self.name = f"BB({length},{num_std:g}{ma_tag})"
        else:
            self.name = f"BB({length},{num_std:g}{ma_tag},σ={self.std_length})"

    def compute_arr(self, bars: Bars) -> Dict[str, np.ndarray]:
        closes = bars.close
        n = self.length
        m = self.std_length
        empty = lambda: np.full_like(closes, np.nan)
        middle, upper, lower = empty(), empty(), empty()

        if closes.size == 0:
            return {"middle": middle, "upper": upper, "lower": lower}

        center = apply_ma(self.ma_type, closes, n)
        if closes.size >= n:
            middle[:] = center

        if closes.size >= m:
            csum = np.concatenate(([0.0], np.cumsum(closes)))
            mean_m = (csum[m:] - csum[:-m]) / m
            csum2 = np.concatenate(([0.0], np.cumsum(closes * closes)))
            mean_sq = (csum2[m:] - csum2[:-m]) / m
            var = np.maximum(mean_sq - mean_m * mean_m, 0.0)
            std = np.sqrt(var)
            warmup = max(n, m)
            if closes.size >= warmup:
                std_aligned = std[(warmup - m):]
                mid_aligned = center[warmup - 1:]
                upper[warmup - 1:] = mid_aligned + self.num_std * std_aligned
                lower[warmup - 1:] = mid_aligned - self.num_std * std_aligned

        return {"middle": middle, "upper": upper, "lower": lower}

    def compute(self, candles: List[Candle]) -> Dict[str, np.ndarray]:
        return self.compute_arr(Bars.from_candles(candles))
