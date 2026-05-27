"""Stochastic Momentum Index (SMI) — Blau (1993).

A double-smoothed refinement of the classic stochastic oscillator. For
each bar in a window of length ``N``:

    HH    = max(high[i-N+1 .. i])
    LL    = min(low[i-N+1 .. i])
    mid   = (HH + LL) / 2
    dist  = close[i] - mid
    range = HH - LL

The momentum signal is then double-smoothed:

    sd1   = EMA(dist,  smooth1)
    sd2   = EMA(sd1,   smooth2)
    sr1   = EMA(range, smooth1)
    sr2   = EMA(sr1,   smooth2)

    SMI   = 100 * sd2 / (sr2 / 2)
    signal = EMA(SMI, signal_length)

Output is bounded in roughly ``[-100, +100]``. Crossovers between the
SMI line and its signal line are the primary trading signal.

Defaults (Blau classic): N=14, smooth1=3, smooth2=3, signal=3.

Reference levels at ±40 and 0 are drawn by the render layer (see
``reference_levels`` class attribute).
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from ..core.bars import Bars
from ._palette import SECONDARY_LINE, TAB10_CYAN
from .base import BaseIndicator, LineStyle, ParamDef


class SMI(BaseIndicator):
    """Stochastic Momentum Index (Blau).

    ``compute`` returns ``{"smi": ndarray, "signal": ndarray}``.
    Both arrays are the same length as ``candles``; the first
    ``length-1`` entries are ``NaN`` (HH/LL window not yet full).
    """

    kind_id: ClassVar[str] = "smi"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("length", "int", default=14, min=2, max=2000, step=1,
                 description="%K period"),
        ParamDef("smooth1", "int", default=3, min=1, max=200, step=1,
                 description="Smooth 1"),
        ParamDef("smooth2", "int", default=3, min=1, max=200, step=1,
                 description="Smooth 2"),
        ParamDef("signal_length", "int", default=3, min=1, max=200, step=1,
                 description="Signal"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        # Teal SMI line; orange signal line for clear contrast on dark
        # and light themes alike.
        "smi":    LineStyle(color=TAB10_CYAN,      width=1.4),
        "signal": LineStyle(color=SECONDARY_LINE,  width=1.2),
    }
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("smi", "numeric"),
        ("signal", "numeric"),
    )

    #: Horizontal guide lines drawn on the indicator pane by the
    #: render layer (axhline at each level). Empty tuple ⇒ no guides.
    reference_levels: ClassVar[tuple[float, ...]] = (-40.0, 0.0, 40.0)

    overlay = False  # draw in its own pane

    def __init__(
        self,
        length: int = 14,
        smooth1: int = 3,
        smooth2: int = 3,
        signal_length: int = 3,
    ) -> None:
        if length < 2:
            raise ValueError("length must be >= 2")
        if smooth1 < 1:
            raise ValueError("smooth1 must be >= 1")
        if smooth2 < 1:
            raise ValueError("smooth2 must be >= 1")
        if signal_length < 1:
            raise ValueError("signal_length must be >= 1")
        self.length = int(length)
        self.smooth1 = int(smooth1)
        self.smooth2 = int(smooth2)
        self.signal_length = int(signal_length)
        self.name = (
            f"SMI({self.length},{self.smooth1},"
            f"{self.smooth2},{self.signal_length})"
        )

    # --- public --------------------------------------------------------

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        n = len(bars)
        smi_out = np.full(n, np.nan, dtype=np.float64)
        sig_out = np.full(n, np.nan, dtype=np.float64)
        if n == 0:
            return {"smi": smi_out, "signal": sig_out}

        highs, lows, closes = bars.high, bars.low, bars.close

        L = self.length
        if n < L:
            return {"smi": smi_out, "signal": sig_out}

        # Rolling HH / LL via sliding-window views — still O(N*L),
        # but the per-window max/min runs inside NumPy rather than a
        # Python loop. The series we render is at most a few thousand
        # bars; trivial overhead. (For larger windows this could move
        # to a deque; not needed at our sizes.)
        hh = np.full(n, np.nan, dtype=np.float64)
        ll = np.full(n, np.nan, dtype=np.float64)
        windows_hi = sliding_window_view(highs, L)
        windows_lo = sliding_window_view(lows, L)
        hh[L - 1:] = windows_hi.max(axis=1)
        ll[L - 1:] = windows_lo.min(axis=1)

        mid = (hh + ll) / 2.0
        dist = closes - mid
        rng = hh - ll  # noqa: F841 — used below as ``rng``

        # Double EMA on dist and range. Seed each EMA pass at the
        # first valid (non-NaN) sample using that sample's value
        # (standard EMA seeding, same convention as EMA class).
        sd1 = _ema_with_nan(dist, self.smooth1)
        sd2 = _ema_with_nan(sd1,  self.smooth2)
        sr1 = _ema_with_nan(rng,  self.smooth1)
        sr2 = _ema_with_nan(sr1,  self.smooth2)

        # SMI = 100 * sd2 / (sr2 / 2). Guard against divide-by-zero
        # when range collapses (flat market): emit 0 rather than NaN
        # so the line stays continuous through the flat patch.
        with np.errstate(divide="ignore", invalid="ignore"):
            denom = sr2 / 2.0
            smi = np.where(
                np.isfinite(sd2) & np.isfinite(denom) & (denom != 0.0),
                100.0 * sd2 / denom,
                np.nan,
            )
            # Flat-market patch: when sd2 is finite and denom == 0,
            # the numerator is also 0 (no excursion above/below mid),
            # so the meaningful SMI value is 0.
            flat = np.isfinite(sd2) & (denom == 0.0)
            smi = np.where(flat, 0.0, smi)

        signal = _ema_with_nan(smi, self.signal_length)

        return {"smi": smi, "signal": signal}



# --- helpers -----------------------------------------------------------


def _ema_with_nan(arr: np.ndarray, length: int) -> np.ndarray:
    """Recursive EMA that gracefully skips leading NaN samples.

    Output mirrors input length. Indices before the first finite
    input remain NaN; the EMA seeds at the first finite sample with
    that sample's value, then applies ``alpha = 2/(length+1)``.
    """
    out = np.full_like(arr, np.nan)
    if arr.size == 0:
        return out
    alpha = 2.0 / (length + 1.0)
    seeded = False
    prev = 0.0
    for i in range(arr.size):
        v = arr[i]
        if not np.isfinite(v):
            continue
        if not seeded:
            prev = float(v)
            seeded = True
            out[i] = prev
            continue
        prev = alpha * float(v) + (1.0 - alpha) * prev
        out[i] = prev
    return out
