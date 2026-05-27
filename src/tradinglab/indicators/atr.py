"""Average True Range (ATR) — rolling and time-of-day variants.

ATR is a volatility measure: the average over ``length`` bars of the
True Range,

    TR[i] = max(high[i] - low[i],
                |high[i] - close[i-1]|,
                |low[i]  - close[i-1]|)

A single :class:`ATR` indicator class supports two **modes**:

* ``mode="rolling"`` (default) — classic rolling MA of TR with
  user-selectable smoothing (Wilder's RMA, SMA, EMA, WMA). Default
  ``length=14``.

* ``mode="tod"`` (time-of-day) — for each intraday bar, the mean TR
  of the *same wall-clock-time* bar across the last ``length``
  regular sessions. Parallel to ``rvol_tod`` so the two metrics
  share a lookback unit. Default ``length=20``.

  On non-intraday charts (daily / weekly / monthly), ``mode="tod"``
  falls back to a plain rolling 20-bar arithmetic mean of TR — the
  natural extension since each daily bar IS its own time-of-day.

The ``length`` parameter is dual-purpose by user request: 14 in
``rolling`` mode and 20 in ``tod`` mode when omitted. An explicit
``length`` is always honored verbatim.

``ma_type``, ``session_filter``, and ``aggregator`` are mode-scoped:
``ma_type`` applies only to ``mode="rolling"``; ``session_filter``
and ``aggregator`` apply only to ``mode="tod"`` intraday. Inert in
the other mode but stored verbatim on the instance for
round-tripping.

Output schema is ``{"atr": ndarray}``, output range ``[0, +inf)``.
ATR is unit-bearing in price units (no canonical reference levels,
so none are drawn).

Warmup
------

* ``rolling`` — NaN before index ``length`` (``length`` valid TR
  values are needed; TR[0] is NaN).
* ``tod`` intraday — NaN until the bar's session has at least
  ``_MIN_WARMUP_SESSIONS`` prior regular sessions of same-tod
  baseline data.
* ``tod`` daily/weekly/monthly fallback — NaN before index ``length``.
"""

from __future__ import annotations

from typing import ClassVar

import numpy as np

from ..core.bars import Bars
from .base import BaseIndicator, LineStyle, ParamDef
from .ma_kernels import MA_TYPES, apply_ma
from .sessions import (
    is_intraday_np,
    session_filter_mask_np,
    session_groups_np,
    tod_key_np,
)
from .wilder import true_range as _true_range

_DEFAULT_COLOR_BY_MA: dict[str, str] = {
    "RMA": "#ffbb78",
    "SMA": "#8c564b",
    "EMA": "#17becf",
    "WMA": "#e377c2",
}

ATR_MODES: tuple[str, ...] = ("rolling", "tod")
_ATR_TOD_AGGREGATORS: tuple[str, ...] = ("mean", "median")
_ATR_TOD_SESSION_FILTERS: tuple[str, ...] = (
    "regular_only", "regular_plus_premarket", "extended",
)

_LENGTH_DEFAULT_SENTINEL: int = -1
_MIN_WARMUP_SESSIONS = 5
_TOD_DAILY_FALLBACK_LENGTH = 20


def _aggregate(values: np.ndarray, aggregator: str) -> float:
    if values.size == 0:
        return float("nan")
    if aggregator == "median":
        v = float(np.nanmedian(values))
    else:
        v = float(np.nanmean(values))
    if not np.isfinite(v):
        return float("nan")
    return v


class ATR(BaseIndicator):
    """Average True Range with user-selectable mode + smoothing.

    ``compute`` returns ``{"atr": ndarray}``.
    """

    kind_id: ClassVar[str] = "atr"
    kind_version: ClassVar[int] = 2
    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("length", "int", default=_LENGTH_DEFAULT_SENTINEL,
                 min=2, max=2000, step=1,
                 description="Length"),
        ParamDef("ma_type", "choice", default="RMA",
                 choices=MA_TYPES,
                 description="MA kernel"),
        ParamDef("mode", "choice", default="rolling",
                 choices=ATR_MODES,
                 description="Mode"),
        ParamDef("session_filter", "choice", default="regular_only",
                 choices=_ATR_TOD_SESSION_FILTERS,
                 description="Session"),
        ParamDef("aggregator", "choice", default="mean",
                 choices=_ATR_TOD_AGGREGATORS,
                 description="Aggregator"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        "atr": LineStyle(color=_DEFAULT_COLOR_BY_MA["RMA"], width=1.4),
    }
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("atr", "numeric"),
    )
    reference_levels: ClassVar[tuple[float, ...]] = ()
    overlay = False

    def __init__(
        self,
        length: int = _LENGTH_DEFAULT_SENTINEL,
        ma_type: str = "RMA",
        mode: str = "rolling",
        session_filter: str = "regular_only",
        aggregator: str = "mean",
    ) -> None:
        mode_norm = str(mode).lower()
        if mode_norm not in ATR_MODES:
            raise ValueError(f"mode must be one of {ATR_MODES!r}; got {mode!r}")
        if int(length) == _LENGTH_DEFAULT_SENTINEL:
            length = 20 if mode_norm == "tod" else 14
        if int(length) < 2:
            raise ValueError("length must be >= 2")
        ma_type_norm = str(ma_type).upper()
        if ma_type_norm not in MA_TYPES:
            raise ValueError(
                f"ma_type must be one of {MA_TYPES}; got {ma_type!r}"
            )
        if str(session_filter) not in _ATR_TOD_SESSION_FILTERS:
            raise ValueError(
                f"session_filter must be one of {_ATR_TOD_SESSION_FILTERS!r}; "
                f"got {session_filter!r}"
            )
        if str(aggregator) not in _ATR_TOD_AGGREGATORS:
            raise ValueError(
                f"aggregator must be one of {_ATR_TOD_AGGREGATORS!r}; "
                f"got {aggregator!r}"
            )
        self.length = int(length)
        self.ma_type = ma_type_norm
        self.mode = mode_norm
        self.session_filter = str(session_filter)
        self.aggregator = str(aggregator)
        if self.mode == "tod":
            self.name = f"ATR ToD({self.length})"
        elif self.ma_type == "RMA":
            self.name = f"ATR({self.length})"
        else:
            self.name = f"ATR-{self.ma_type}({self.length})"

    @property
    def warmup_bars(self) -> int:
        """4×length for Wilder (RMA) smoothing; ``length`` otherwise.

        ATR's first-finite index is ``length`` for every kernel, but RMA
        (Wilder's recursive form) keeps drifting toward truth — same IIR
        story as RSI. SMA / EMA / WMA settle by ``length`` bars, so the
        empirical index is the right number for them.
        """
        if self.ma_type == "RMA":
            return 4 * int(self.length)
        return int(self.length)

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        n = len(bars)
        out = np.full(n, np.nan, dtype=np.float64)
        if n < 2:
            return {"atr": out}
        highs, lows, closes = bars.high, bars.low, bars.close
        tr = _true_range(highs, lows, closes)

        if self.mode == "rolling":
            out[:] = apply_ma(self.ma_type, tr, self.length)
            return {"atr": out}

        # mode == "tod" — session bucketing path.
        return self._compute_tod_arr(bars, tr)


    def _compute_tod_arr(self, bars: Bars, tr: np.ndarray) -> dict[str, np.ndarray]:
        n = len(bars)
        out = np.full(n, np.nan, dtype=np.float64)

        if not is_intraday_np(bars):
            L = _TOD_DAILY_FALLBACK_LENGTH
            if n > L:
                # Rolling arithmetic mean of TR via cumulative sum.
                # TR[0] is NaN (no prior close); replace with 0 in the
                # cumsum then mask out windows where index 0 falls in
                # range. Output starts valid at index L.
                tr_clean = np.where(np.isfinite(tr), tr, 0.0)
                csum = np.concatenate(([0.0], np.cumsum(tr_clean)))
                # window for output index i covers tr[i-L+1 : i+1].
                # i starts at L (skipping i=L-1 to avoid the NaN at TR[0]).
                window_sums = csum[L + 1:n + 1] - csum[1:n - L + 1]
                out[L:n] = window_sums / L
            return {"atr": out}

        admit_mask = session_filter_mask_np(bars, self.session_filter)
        groups = session_groups_np(bars, regular_only=True)
        if len(groups) < _MIN_WARMUP_SESSIONS + 1:
            return {"atr": out}

        tk = tod_key_np(bars)

        # Build a (n_sessions x n_unique_tod_keys) dense matrix of TR
        # so each current bar's baseline can be sliced as a numpy
        # column window instead of an L-dict comprehension. NaN
        # entries mark "this session never saw a bar at that ToD".
        unique_keys, inv_idx = np.unique(tk, return_inverse=True)
        n_sessions = len(groups)
        n_keys = unique_keys.size
        tod_matrix = np.full((n_sessions, n_keys), np.nan, dtype=np.float64)
        for s, grp in enumerate(groups):
            grp_arr = np.asarray(grp, dtype=np.int64)
            keep = admit_mask[grp_arr] & np.isfinite(tr[grp_arr])
            sel = grp_arr[keep]
            if sel.size == 0:
                continue
            cols = inv_idx[sel]
            # Mirror the prior dict.setdefault behaviour: first-write
            # wins per (session, tod_key) bucket. np.unique returns
            # sorted first occurrences in `idx` so we use that to
            # de-duplicate while preserving "earliest bar in session".
            _, first_idx = np.unique(cols, return_index=True)
            tod_matrix[s, cols[first_idx]] = tr[sel[first_idx]]

        L = self.length
        for s in range(_MIN_WARMUP_SESSIONS, n_sessions):
            cur_grp = groups[s]
            lo = max(0, s - L)
            window = tod_matrix[lo:s]  # shape (<=L, n_keys)
            valid_mask = np.isfinite(window)
            counts = valid_mask.sum(axis=0)
            ready_cols = counts >= _MIN_WARMUP_SESSIONS
            if not ready_cols.any():
                continue
            if self.aggregator == "median":
                # nanmedian per column; ignore not-ready columns.
                agg_per_col = np.full(n_keys, np.nan, dtype=np.float64)
                if ready_cols.any():
                    agg_per_col[ready_cols] = np.nanmedian(
                        window[:, ready_cols], axis=0,
                    )
            else:
                col_sums = np.where(valid_mask, window, 0.0).sum(axis=0)
                with np.errstate(invalid="ignore", divide="ignore"):
                    agg_per_col = np.where(
                        ready_cols, col_sums / counts, np.nan,
                    )
            for idx in cur_grp:
                if not admit_mask[idx]:
                    continue
                col = inv_idx[idx]
                if ready_cols[col]:
                    v = float(agg_per_col[col])
                    if np.isfinite(v):
                        out[idx] = v
        return {"atr": out}
