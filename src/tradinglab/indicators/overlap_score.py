"""Overlap Score Inverted — weighted range overlap indicator.

Measures how much of the current candle's price range is in "new
territory" relative to recent candles, with exponential weighting
so recent bars matter more.

For each bar *i* with lookback *N*:

1. **Current range**: ``high[i] - low[i]`` (floored to 0.01).

2. **Overlap with prior bar k** (k=1 is immediately prior):
   ``overlap = max(0, min(high[i], high[i-k]) - max(low[i], low[i-k]))``

3. **Overlap fraction**: ``overlap / current_range``

4. **Exponential weights**: ``alpha = 1 - 5/(N+1)``, aggressive decay.
   Bar k gets raw weight ``alpha^(k-1)``, normalized so weights sum
   to 1. The most recent bar carries ~39% of the weight (lookback=10);
   bars older than half the lookback are nearly zero-weighted.

5. **Weighted average overlap**:
   ``overlap_score = sum(normalized_weight[k] * overlap_fraction[k])``

6. **Invert** so high = new territory:
   ``OSI = (1 - overlap_score) * 100``

Output range is ``[0, 100]``:

* **0** — current bar is entirely within recent bars' ranges
  (full consolidation / inside-bar territory).
* **100** — current bar has zero overlap with any recent bar
  (fully new territory / breakout / gap).
* **60** — 60% of the current bar's range is in new territory,
  40% is recycled from recent bars.

Complements ATR: ATR measures range *size*; this measures range
*location novelty*. Together they classify the market into four
regimes (see the spec.md for the 2×2 matrix).
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from ..core.bars import Bars
from .base import BaseIndicator, LineStyle, ParamDef

# Minimum range to avoid division by zero on doji bars.
_MIN_RANGE = 0.01


class OverlapScoreInverted(BaseIndicator):
    """Weighted range overlap indicator (inverted: high = new territory).

    ``compute_arr`` returns ``{"osi": ndarray}`` in ``[0, 100]``.
    The first ``lookback`` bars are NaN (insufficient history).
    """

    kind_id: ClassVar[str] = "overlap_score_inv"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("lookback", "int", default=10, min=2, max=200, step=1,
                 description="Lookback"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        "osi": LineStyle(color="#ab47bc", width=1.4),  # purple
    }

    reference_levels: ClassVar[tuple[float, ...]] = (20.0, 80.0)

    overlay = False  # lower pane

    def __init__(self, lookback: int = 10) -> None:
        if lookback < 2:
            raise ValueError("lookback must be >= 2")
        self.lookback = int(lookback)
        self.name = f"Overlap({self.lookback})"

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        n = len(bars)
        out = np.full(n, np.nan, dtype=np.float64)
        L = self.lookback
        if n <= L:
            return {"osi": out}

        # Promote to float64 once; bars.high/low are typically already
        # float64, but we want a guaranteed contiguous numeric array so
        # sliding_window_view returns a clean stride view.
        highs = np.asarray(bars.high, dtype=np.float64)
        lows = np.asarray(bars.low, dtype=np.float64)

        # Precompute exponential weights with aggressive decay.
        # alpha = 1 - 5/(L+1) gives a half-life of ~L/7 bars,
        # making the 1-2 most recent bars dominate heavily.
        alpha = max(0.01, 1.0 - 5.0 / (L + 1.0))
        raw_w = np.power(alpha, np.arange(L, dtype=np.float64))
        norm_w = raw_w / raw_w.sum()

        # Vectorized rolling overlap with the L most-recent prior bars.
        # sliding_window_view(highs[:-1], L)[j] == highs[j : j+L], which
        # for output bar i = j + L holds the L priors highs[i-L .. i-1].
        # Column index L-1 is the most-recent prior (i-1); the original
        # Python loop used k=0 for that bar, so the weight vector is
        # applied reversed.
        hi_win = sliding_window_view(highs[:-1], L)  # shape (n-L, L), no copy
        lo_win = sliding_window_view(lows[:-1], L)

        cur_hi = highs[L:, None]
        cur_lo = lows[L:, None]
        cur_rng = np.maximum(cur_hi - cur_lo, _MIN_RANGE)

        overlap = np.maximum(
            0.0,
            np.minimum(cur_hi, hi_win) - np.maximum(cur_lo, lo_win),
        )
        weighted = (overlap / cur_rng) @ norm_w[::-1]

        out[L:] = (1.0 - weighted) * 100.0
        return {"osi": out}

