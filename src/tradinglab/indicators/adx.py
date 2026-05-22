"""Average Directional Index (ADX) — Wilder (1978).

A trend-strength indicator computed from the *directional movement*
between consecutive bars. Three plotted lines:

    +DI  = directional index up
    -DI  = directional index down
    ADX  = smoothed, sign-stripped magnitude of (+DI - -DI)

For each bar ``i`` (with prior bar ``i-1``):

    TR   = max(high - low,
               |high - prev_close|,
               |low  - prev_close|)
    up   = high - prev_high
    down = prev_low - low
    +DM  = up   if up > down  and up > 0   else 0
    -DM  = down if down > up  and down > 0 else 0

Wilder smoothing (a.k.a. RMA) over ``length`` bars is then applied
to ``TR``, ``+DM``, and ``-DM``. The smoothed series seed at the
sum of the first ``length`` post-warmup values; subsequent values
use the recurrence ``S_i = S_{i-1} - S_{i-1}/N + x_i`` (per Wilder),
which is equivalent to an EMA with ``alpha = 1/N``.

    +DI = 100 * smoothed(+DM) / smoothed(TR)
    -DI = 100 * smoothed(-DM) / smoothed(TR)
    DX  = 100 * |+DI - -DI| / (+DI + -DI)
    ADX = Wilder-smoothed DX over ``length``

Output range is ``[0, 100]`` for all three lines. The reference
axhline at ``25`` (the canonical trend-strength threshold) is drawn
by the render layer via the ``reference_levels`` mechanism.

Default: ``length = 14`` (Wilder's classic).

Warmup: ``+DI`` / ``-DI`` are first finite at index ``length``;
``ADX`` is first finite at index ``2 * length - 1`` (Wilder smoothing
of an already-warmed series adds another ``length-1`` of warmup).
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from ..core.bars import Bars
from ..models import Candle
from .base import LineStyle, ParamDef
from .wilder import (
    true_range as _true_range,
)
from .wilder import (
    wilder_smooth_avg as _wilder_smooth_avg,
)
from .wilder import (
    wilder_smooth_sum as _wilder_smooth_sum,
)


class ADX:
    """Average Directional Index (Wilder).

    ``compute`` returns ``{"plus_di": ndarray, "minus_di": ndarray,
    "adx": ndarray}``. All arrays are the same length as ``candles``.
    """

    kind_id: ClassVar[str] = "adx"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("length", "int", default=14, min=2, max=2000, step=1,
                 description="Length"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        # +DI green (bull direction), -DI red (bear direction), ADX
        # blue (the de-signed trend-strength magnitude).
        "plus_di":  LineStyle(color="#2ca02c", width=1.2),
        "minus_di": LineStyle(color="#d62728", width=1.2),
        "adx":      LineStyle(color="#7f7f7f", width=1.6),
    }

    #: Horizontal guide line at 25 (canonical "trending" threshold).
    reference_levels: ClassVar[tuple[float, ...]] = (25.0,)

    overlay = False  # draw in its own pane

    def __init__(self, length: int = 14) -> None:
        if length < 2:
            raise ValueError("length must be >= 2")
        self.length = int(length)
        self.name = f"ADX({self.length})"

    # --- public --------------------------------------------------------

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        n = len(bars)
        plus_di_out  = np.full(n, np.nan, dtype=np.float64)
        minus_di_out = np.full(n, np.nan, dtype=np.float64)
        adx_out      = np.full(n, np.nan, dtype=np.float64)
        if n == 0:
            return {"plus_di": plus_di_out, "minus_di": minus_di_out,
                    "adx": adx_out}

        highs, lows, closes = bars.high, bars.low, bars.close

        L = self.length
        # Need at least 2*L bars to produce any finite ADX value
        # (L bars to warm DI smoothing + L-1 more to warm DX smoothing
        # + 1 to emit the first ADX). We still emit DI for short-but-
        # ≥L+1-bar inputs, leaving ADX as NaN.
        if n < 2:
            return {"plus_di": plus_di_out, "minus_di": minus_di_out,
                    "adx": adx_out}

        # Per-bar TR, +DM, -DM. Index 0 is NaN (no prior bar).
        tr = _true_range(highs, lows, closes)
        up = np.empty(n, dtype=np.float64)
        down = np.empty(n, dtype=np.float64)
        up[0] = np.nan
        down[0] = np.nan
        up[1:] = highs[1:] - highs[:-1]
        down[1:] = lows[:-1] - lows[1:]
        plus_dm = np.where((up > down) & (up > 0), up, 0.0)
        minus_dm = np.where((down > up) & (down > 0), down, 0.0)
        plus_dm[0] = np.nan
        minus_dm[0] = np.nan

        # Wilder smoothing seeds at the *sum* of the first L valid
        # values. Valid values start at index 1, so the seed lands at
        # index L (i.e. L valid values: indices 1..L). Subsequent
        # values use the Wilder recurrence S_i = S_{i-1} - S_{i-1}/L + x_i
        # which is equivalent to an RMA (alpha = 1/L). Dividing the
        # seeded sum by L yields the same series as the RMA form;
        # we keep the *sum* form here so the +DI / -DI ratios divide
        # cleanly (sum / sum = average / average).
        if n <= L:
            return {"plus_di": plus_di_out, "minus_di": minus_di_out,
                    "adx": adx_out}

        smoothed_tr = _wilder_smooth_sum(tr, L)
        smoothed_plus_dm  = _wilder_smooth_sum(plus_dm,  L)
        smoothed_minus_dm = _wilder_smooth_sum(minus_dm, L)

        # +DI / -DI: percentages. Guard divide-by-zero when the entire
        # smoothed TR window is flat (no movement); emit 0 rather than
        # NaN so the lines stay continuous through a perfectly flat
        # patch.
        with np.errstate(divide="ignore", invalid="ignore"):
            plus_di = np.where(
                np.isfinite(smoothed_tr) & (smoothed_tr != 0.0),
                100.0 * smoothed_plus_dm  / smoothed_tr,
                np.nan,
            )
            minus_di = np.where(
                np.isfinite(smoothed_tr) & (smoothed_tr != 0.0),
                100.0 * smoothed_minus_dm / smoothed_tr,
                np.nan,
            )
            flat_tr = np.isfinite(smoothed_tr) & (smoothed_tr == 0.0)
            plus_di  = np.where(flat_tr, 0.0, plus_di)
            minus_di = np.where(flat_tr, 0.0, minus_di)

            # DX = 100 * |+DI - -DI| / (+DI + -DI). When both are 0
            # (totally flat market), DX is 0.
            di_sum = plus_di + minus_di
            dx = np.where(
                np.isfinite(di_sum) & (di_sum != 0.0),
                100.0 * np.abs(plus_di - minus_di) / di_sum,
                np.nan,
            )
            both_flat = np.isfinite(di_sum) & (di_sum == 0.0)
            dx = np.where(both_flat, 0.0, dx)

        # ADX = Wilder smoothing of DX (averaged form: divide by L
        # so ADX is in [0, 100]).
        adx = _wilder_smooth_avg(dx, L)

        plus_di_out[:]  = plus_di
        minus_di_out[:] = minus_di
        adx_out[:]      = adx
        return {"plus_di": plus_di_out, "minus_di": minus_di_out,
                "adx": adx_out}

    def compute(self, candles: list[Candle]) -> dict[str, np.ndarray]:
        return self.compute_arr(Bars.from_candles(candles))
