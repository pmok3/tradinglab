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

from typing import ClassVar

import numpy as np

from ..core.bars import Bars
from ._palette import PRIMARY_LINE, QUATERNARY, QUINARY, TERTIARY_LINE
from .base import BaseIndicator, LineStyle, ParamDef
from .ma_kernels import MA_TYPES, apply_ma

# Per-MA color palette so a chart with multiple BB configs reads at a
# glance. Defaults override only when the user didn't already set a
# style; see :meth:`__init__`.
_DEFAULT_COLOR_BY_MA: dict[str, str] = {
    "SMA": TERTIARY_LINE,   # green
    "EMA": QUATERNARY,      # red (matches the previous BB-EMA hue)
    "WMA": QUINARY,         # purple
    "RMA": PRIMARY_LINE,    # blue
}


class BollingerBands(BaseIndicator):
    kind_id: ClassVar[str] = "bbands"
    kind_version: ClassVar[int] = 3
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
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
    default_style: ClassVar[dict[str, LineStyle]] = {
        # SMA-default colors. Per-instance overrides applied in
        # __init__ when a non-SMA ma_type is picked.
        "middle": LineStyle(color=_DEFAULT_COLOR_BY_MA["SMA"], width=1.2),
        "upper":  LineStyle(color=_DEFAULT_COLOR_BY_MA["SMA"], width=1.0),
        "lower":  LineStyle(color=_DEFAULT_COLOR_BY_MA["SMA"], width=1.0),
    }
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("middle", "numeric"),
        ("upper", "numeric"),
        ("lower", "numeric"),
    )

    overlay = True

    @classmethod
    def effective_output_keys(cls, params: dict) -> tuple[str, ...]:
        """Return outputs in top-down visual order (upper → middle → lower).

        ``default_style.keys()`` declares middle first (the canonical
        "centerline" anchor for code) but on the chart the bands are
        upper / middle / lower stacked top-down — that's the order the
        user reads them in the legend. Audit ``legend-condensation``.
        """
        return ("upper", "middle", "lower")

    def __init__(self, length: int = 20, num_std: float = 2.0,
                 std_length: int | None = None,
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

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        closes = bars.close
        n = self.length
        m = self.std_length
        def empty():
            return np.full_like(closes, np.nan)
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

    # --- incremental protocol (closed-bar appends) ----------------------
    # Gated to the default ma_type="SMA": the middle is a rolling SMA and
    # the band is a rolling population std, both maintainable with O(1)
    # running sums per appended bar (sum for the mean, sum + sum-of-squares
    # for the variance). EMA/WMA/RMA leave seeded=False → full recompute.
    # The window sums are causal so the cached prefix is exact; appended
    # bars differ from compute_arr's cumsum form by float64 round-off only.

    def _inc_supported(self) -> bool:
        return self.ma_type == "SMA"

    def inc_init(self, bars: Bars) -> dict[str, object]:
        out = self.compute_arr(bars)
        closes = bars.close
        n_bars = int(closes.size)
        nn = self.length
        mm = self.std_length
        warmup = max(nn, mm)
        state: dict[str, object] = {"output": out, "len": n_bars}
        if self._inc_supported() and n_bars > warmup:
            win_n = closes[n_bars - nn:n_bars]
            win_m = closes[n_bars - mm:n_bars]
            state["sum_n"] = float(win_n.sum())
            state["sum_m"] = float(win_m.sum())
            state["sumsq_m"] = float((win_m * win_m).sum())
            state["seeded"] = True
        else:
            state["seeded"] = False
        return state

    def inc_step(
        self, state: dict[str, object], bars: Bars, *, prev_len: int,
    ) -> dict[str, object]:
        closes = bars.close
        n_bars = int(closes.size)
        if n_bars <= prev_len:
            raise ValueError(
                f"Bollinger.inc_step requires growth: prev_len={prev_len}, new_len={n_bars}"
            )
        if not (self._inc_supported() and state.get("seeded")):
            raise ValueError("Bollinger.inc_step: unsupported config or unseeded state")
        nn = self.length
        mm = self.std_length
        k = self.num_std
        sum_n = float(state["sum_n"])  # type: ignore[arg-type]
        sum_m = float(state["sum_m"])  # type: ignore[arg-type]
        sumsq_m = float(state["sumsq_m"])  # type: ignore[arg-type]
        old = state["output"]  # type: ignore[index]
        mid_out = np.empty(n_bars, dtype=np.float64)
        up_out = np.empty(n_bars, dtype=np.float64)
        lo_out = np.empty(n_bars, dtype=np.float64)
        mid_out[:prev_len] = old["middle"]
        up_out[:prev_len] = old["upper"]
        lo_out[:prev_len] = old["lower"]
        for j in range(prev_len, n_bars):
            c = float(closes[j])
            out_n = float(closes[j - nn])
            out_m = float(closes[j - mm])
            sum_n += c - out_n
            sum_m += c - out_m
            sumsq_m += c * c - out_m * out_m
            mean_n = sum_n / nn
            mean_m = sum_m / mm
            var = sumsq_m / mm - mean_m * mean_m
            if var < 0.0:
                var = 0.0
            std = np.sqrt(var)
            mid_out[j] = mean_n
            up_out[j] = mean_n + k * std
            lo_out[j] = mean_n - k * std
        return {
            "output": {"middle": mid_out, "upper": up_out, "lower": lo_out},
            "len": n_bars,
            "sum_n": sum_n,
            "sum_m": sum_m,
            "sumsq_m": sumsq_m,
            "seeded": True,
        }

