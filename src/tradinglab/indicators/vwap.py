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

from typing import ClassVar

import numpy as np

from ..core.bars import Bars
from ._palette import QUINARY
from .base import Availability, BaseIndicator, LineStyle, ParamDef, intraday_only
from .sessions import is_intraday_np, session_groups_np

_PRICE_SOURCES: tuple[str, ...] = ("typical", "close", "ohlc4")


class VWAP(BaseIndicator):
    """Session-anchored Volume-Weighted Average Price.

    ``compute`` returns ``{"vwap": ndarray}``. Indices where VWAP is
    undefined (warmup, pre/post bars, gaps, daily+ intervals) are NaN.
    """

    kind_id: ClassVar[str] = "vwap"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef(
            "price_source", "choice", default="typical",
            choices=_PRICE_SOURCES,
            description="Price source",
        ),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        "vwap": LineStyle(color=QUINARY, width=1.6),
    }
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("vwap", "numeric"),
    )
    resets_daily: ClassVar[bool] = True

    overlay = True

    def __init__(self, price_source: str = "typical") -> None:
        if price_source not in _PRICE_SOURCES:
            raise ValueError(
                f"price_source must be one of {_PRICE_SOURCES!r}; "
                f"got {price_source!r}"
            )
        self.price_source = price_source
        self.name = "VWAP"

    @staticmethod
    def is_available_for(interval: str) -> Availability:
        """VWAP is session-anchored — only meaningful on intraday bars.

        On daily / weekly / monthly intervals each bar IS its own
        session, so VWAP degenerates and ``compute_arr`` returns
        all-NaN. Declaring it unavailable lets the chart menu grey it
        out and lets the Strategy Tester block a Run that would
        silently produce zero trades (the NaN VWAP never satisfies a
        ``close > vwap`` style condition). Audit ``intraday-interval-guard``.
        """
        return intraday_only(interval)

    # --- public --------------------------------------------------------

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
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

    # --- incremental protocol (closed-bar appends) ----------------------
    # VWAP is a per-session cumulative Σ(price·vol)/Σ(vol). A closed-bar
    # append extends it O(k): accumulate within the current session,
    # RESET at each new calendar (UTC) day, and skip non-regular bars
    # (they receive NaN and contribute nothing) — exactly mirroring the
    # per-group cumsum in ``compute_arr``. Non-intraday inputs leave
    # ``seeded=False`` (compute_arr is all-NaN there) → full recompute.

    def _day_int_at(self, bars: Bars, j: int) -> int:
        return int(bars.timestamps[j].astype("datetime64[D]").astype("int64"))

    def inc_init(self, bars: Bars) -> dict[str, object]:
        out = self.compute_arr(bars)
        n_bars = len(bars)
        state: dict[str, object] = {"output": out, "len": n_bars}
        if n_bars >= 2 and is_intraday_np(bars):
            groups = session_groups_np(bars, regular_only=True)
            if groups and groups[-1].size > 0:
                last = groups[-1]
                price = _price_arr(bars, self.price_source)
                vol = np.where(np.isfinite(bars.volume), bars.volume, 0.0)
                p = price[last]
                v = vol[last]
                valid = np.isfinite(p)
                cum_pv = float(np.where(valid, p * v, 0.0).sum())
                cum_v = float(np.where(valid, v, 0.0).sum())
                state["cum_pv"] = cum_pv
                state["cum_v"] = cum_v
                state["cur_day"] = self._day_int_at(bars, int(last[-1]))
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
                f"VWAP.inc_step requires growth: prev_len={prev_len}, new_len={n_bars}"
            )
        if not state.get("seeded"):
            raise ValueError("VWAP.inc_step: unseeded state (non-intraday or empty)")
        cum_pv = float(state["cum_pv"])  # type: ignore[arg-type]
        cum_v = float(state["cum_v"])  # type: ignore[arg-type]
        cur_day = int(state["cur_day"])  # type: ignore[arg-type]
        price = _price_arr(bars, self.price_source)
        sess = bars.session
        vols = bars.volume
        old = state["output"]["vwap"]  # type: ignore[index]
        new_out = np.empty(n_bars, dtype=np.float64)
        new_out[:prev_len] = old
        for j in range(prev_len, n_bars):
            if sess[j] != "regular":
                new_out[j] = np.nan
                continue
            day_j = self._day_int_at(bars, j)
            if day_j != cur_day:
                cum_pv = 0.0
                cum_v = 0.0
                cur_day = day_j
            p = float(price[j])
            v = float(vols[j])
            if not np.isfinite(v):
                v = 0.0
            if np.isfinite(p):
                cum_pv += p * v
                cum_v += v
            new_out[j] = cum_pv / cum_v if cum_v > 0.0 else np.nan
        return {
            "output": {"vwap": new_out},
            "len": n_bars,
            "cum_pv": cum_pv,
            "cum_v": cum_v,
            "cur_day": cur_day,
            "seeded": True,
        }



# --- helpers -----------------------------------------------------------


def _price_arr(bars: Bars, source: str) -> np.ndarray:
    if source == "close":
        return bars.close
    if source == "ohlc4":
        return (bars.open + bars.high + bars.low + bars.close) / 4.0
    return (bars.high + bars.low + bars.close) / 3.0
