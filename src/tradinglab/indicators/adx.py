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
from ._palette import QUATERNARY, TAB10_GRAY, TERTIARY_LINE
from .base import BaseIndicator, LineStyle, ParamDef
from .wilder import (
    true_range as _true_range,
)
from .wilder import (
    wilder_smooth_avg as _wilder_smooth_avg,
)
from .wilder import (
    wilder_smooth_sum as _wilder_smooth_sum,
)


class ADX(BaseIndicator):
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
        "plus_di":  LineStyle(color=TERTIARY_LINE, width=1.2),
        "minus_di": LineStyle(color=QUATERNARY,    width=1.2),
        "adx":      LineStyle(color=TAB10_GRAY,    width=1.6),
    }
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("adx", "numeric"),
        ("+di", "numeric"),
        ("-di", "numeric"),
    )

    #: Horizontal guide line at 25 (canonical "trending" threshold).
    reference_levels: ClassVar[tuple[float, ...]] = (25.0,)

    overlay = False  # draw in its own pane

    def __init__(self, length: int = 14) -> None:
        if length < 2:
            raise ValueError("length must be >= 2")
        self.length = int(length)
        self.name = f"ADX({self.length})"

    @property
    def warmup_bars(self) -> int:
        """``4 × length`` — Wilder smoothing chained twice (DI then ADX).

        First-finite ``adx`` lands at ``2×length - 1`` (one Wilder seed
        for the DI inputs, another for the ADX kernel), but the IIR drift
        is the same as RSI/ATR — values keep refining for many bars after
        first emit. ``4×length`` matches RSI/ATR convention.
        """
        return 4 * int(self.length)

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

    # --- incremental protocol (closed-bar appends) ----------------------
    # ADX is a chain of Wilder recurrences: sum-smoothed TR / +DM / -DM →
    # +DI / -DI → DX → average-smoothed ADX. A closed-bar append extends
    # the whole chain O(k) from the committed smoothed sums + ADX value.
    # All recurrences are causal so the cached prefix is bit-identical;
    # appended bars differ from the vectorized kernel by float64 round-off.

    def inc_init(self, bars: Bars) -> dict[str, object]:
        out = self.compute_arr(bars)
        n_bars = len(bars)
        L = self.length
        state: dict[str, object] = {"output": out, "len": n_bars}
        if n_bars > 2 * L:
            highs, lows, closes = bars.high, bars.low, bars.close
            tr = _true_range(highs, lows, closes)
            up = np.empty(n_bars, dtype=np.float64)
            down = np.empty(n_bars, dtype=np.float64)
            up[0] = np.nan
            down[0] = np.nan
            up[1:] = highs[1:] - highs[:-1]
            down[1:] = lows[:-1] - lows[1:]
            plus_dm = np.where((up > down) & (up > 0), up, 0.0)
            minus_dm = np.where((down > up) & (down > 0), down, 0.0)
            plus_dm[0] = np.nan
            minus_dm[0] = np.nan
            str_ = _wilder_smooth_sum(tr, L)
            spdm = _wilder_smooth_sum(plus_dm, L)
            smdm = _wilder_smooth_sum(minus_dm, L)
            s_tr = float(str_[-1])
            s_pdm = float(spdm[-1])
            s_mdm = float(smdm[-1])
            adx_last = float(out["adx"][-1])
            if all(np.isfinite(x) for x in (s_tr, s_pdm, s_mdm, adx_last)):
                state["str"] = s_tr
                state["spdm"] = s_pdm
                state["smdm"] = s_mdm
                state["adx"] = adx_last
                state["last_high"] = float(highs[-1])
                state["last_low"] = float(lows[-1])
                state["last_close"] = float(closes[-1])
                state["seeded"] = True
                return state
        state["seeded"] = False
        return state

    def inc_step(
        self, state: dict[str, object], bars: Bars, *, prev_len: int,
    ) -> dict[str, object]:
        n_bars = len(bars)
        if n_bars <= prev_len:
            raise ValueError(
                f"ADX.inc_step requires growth: prev_len={prev_len}, new_len={n_bars}"
            )
        if not state.get("seeded"):
            raise ValueError("ADX.inc_step: unseeded state")
        L = self.length
        q = (L - 1.0) / L
        str_ = float(state["str"])  # type: ignore[arg-type]
        spdm = float(state["spdm"])  # type: ignore[arg-type]
        smdm = float(state["smdm"])  # type: ignore[arg-type]
        adx = float(state["adx"])  # type: ignore[arg-type]
        last_high = float(state["last_high"])  # type: ignore[arg-type]
        last_low = float(state["last_low"])  # type: ignore[arg-type]
        last_close = float(state["last_close"])  # type: ignore[arg-type]
        highs, lows, closes = bars.high, bars.low, bars.close
        old = state["output"]  # type: ignore[index]
        pdi_out = np.empty(n_bars, dtype=np.float64)
        mdi_out = np.empty(n_bars, dtype=np.float64)
        adx_out = np.empty(n_bars, dtype=np.float64)
        pdi_out[:prev_len] = old["plus_di"]
        mdi_out[:prev_len] = old["minus_di"]
        adx_out[:prev_len] = old["adx"]
        for j in range(prev_len, n_bars):
            h = float(highs[j])
            lo = float(lows[j])
            c = float(closes[j])
            up = h - last_high
            down = last_low - lo
            plus_dm = up if (up > down and up > 0.0) else 0.0
            minus_dm = down if (down > up and down > 0.0) else 0.0
            hl = h - lo
            hpc = abs(h - last_close)
            lpc = abs(lo - last_close)
            tr = hpc if hpc > hl else hl
            tr = lpc if lpc > tr else tr
            str_ = str_ * q + tr
            spdm = spdm * q + plus_dm
            smdm = smdm * q + minus_dm
            if not np.isfinite(str_):
                plus_di = np.nan
                minus_di = np.nan
            elif str_ == 0.0:
                plus_di = 0.0
                minus_di = 0.0
            else:
                plus_di = 100.0 * spdm / str_
                minus_di = 100.0 * smdm / str_
            di_sum = plus_di + minus_di
            if not np.isfinite(di_sum):
                dx = np.nan
            elif di_sum == 0.0:
                dx = 0.0
            else:
                dx = 100.0 * abs(plus_di - minus_di) / di_sum
            dx_eff = dx if np.isfinite(dx) else 0.0
            adx = adx * q + dx_eff / L
            pdi_out[j] = plus_di
            mdi_out[j] = minus_di
            adx_out[j] = adx
            last_high = h
            last_low = lo
            last_close = c
        return {
            "output": {"plus_di": pdi_out, "minus_di": mdi_out, "adx": adx_out},
            "len": n_bars,
            "str": str_,
            "spdm": spdm,
            "smdm": smdm,
            "adx": adx,
            "last_high": last_high,
            "last_low": last_low,
            "last_close": last_close,
            "seeded": True,
        }

