"""Volume-Weighted Average Price (VWAP) — session-anchored, intraday only.

Per the design discussion (b45):

* **Anchor**: regular-session open. Cumulative ``Σ(price·vol)/Σ(vol)``
  resets at the start of each new calendar trading day.
* **Bars considered**: ``session == "regular"`` only. Pre- and
  post-market bars do not contribute, and they receive no VWAP value
  (NaN), even when the user has extended-hours rendering enabled. Gap
  fillers are skipped entirely.
* **Price input**: configurable; default *typical price* ``(H+L+C)/3``,
  which is the industry-standard input.
* **Interval scope**: intraday only. On daily / weekly intervals each
  bar IS its own session, so VWAP would degenerate to the per-bar
  typical price; we instead return all-NaN so the line auto-hides.
  Detection is based on the median spacing between consecutive
  non-gap candles: ``>= 23h`` ⇒ daily-or-higher.

The compute is pure and deterministic — sandbox replay drives it
exactly the same way live data does (one bar at a time, accumulating
within the current session, resetting at each new day).
"""

from __future__ import annotations

from typing import ClassVar, Dict, List, Tuple

import numpy as np

from ..core.bars import Bars
from ..models import Candle
from .base import LineStyle, ParamDef
from .sessions import is_intraday_np, session_groups_np

_PRICE_SOURCES: Tuple[str, ...] = ("typical", "close", "ohlc4")


class VWAP:
    """Session-anchored Volume-Weighted Average Price.

    ``compute`` returns ``{"vwap": ndarray}``. Indices where VWAP is
    undefined (warmup, pre/post bars, gaps, daily+ intervals) are NaN.
    """

    kind_id: ClassVar[str] = "vwap"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[Tuple[ParamDef, ...]] = (
        ParamDef(
            "price_source", "choice", default="typical",
            choices=_PRICE_SOURCES,
            description="Price source",
        ),
    )
    default_style: ClassVar[Dict[str, LineStyle]] = {
        "vwap": LineStyle(color="#9467bd", width=1.6),
    }

    overlay = True

    def __init__(self, price_source: str = "typical") -> None:
        if price_source not in _PRICE_SOURCES:
            raise ValueError(
                f"price_source must be one of {_PRICE_SOURCES!r}; "
                f"got {price_source!r}"
            )
        self.price_source = price_source
        self.name = "VWAP"

    # --- public --------------------------------------------------------

    def compute_arr(self, bars: Bars) -> Dict[str, np.ndarray]:
        n = len(bars)
        out = np.full(n, np.nan, dtype=np.float64)
        if n == 0:
            return {"vwap": out}

        if not is_intraday_np(bars):
            return {"vwap": out}

        price = _price_arr(bars, self.price_source)
        vol = bars.volume.copy()
        # Treat non-finite volume as zero contribution.
        vol = np.where(np.isfinite(vol), vol, 0.0)

        # session_groups_np with regular_only=True returns only regular
        # bars grouped by day — exactly the bars VWAP cumulates over.
        groups = session_groups_np(bars, regular_only=True)
        for grp in groups:
            if grp.size == 0:
                continue
            p = price[grp]
            v = vol[grp]
            # Skip non-finite price entries by zero-weighting them.
            valid = np.isfinite(p)
            pv = np.where(valid, p * v, 0.0)
            vv = np.where(valid, v, 0.0)
            cum_pv = np.cumsum(pv)
            cum_v = np.cumsum(vv)
            with np.errstate(divide="ignore", invalid="ignore"):
                vw = np.where(cum_v > 0.0, cum_pv / cum_v, np.nan)
            out[grp] = vw
        return {"vwap": out}

    def compute(self, candles: List[Candle]) -> Dict[str, np.ndarray]:
        return self.compute_arr(Bars.from_candles(candles))


# --- helpers -----------------------------------------------------------


def _price_arr(bars: Bars, source: str) -> np.ndarray:
    if source == "close":
        return bars.close
    if source == "ohlc4":
        return (bars.open + bars.high + bars.low + bars.close) / 4.0
    return (bars.high + bars.low + bars.close) / 3.0
