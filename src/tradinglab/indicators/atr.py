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

from typing import ClassVar, Dict, List, Tuple

import numpy as np

from ..core.bars import Bars
from ..models import Candle
from .base import LineStyle, ParamDef
from .ma_kernels import MA_TYPES, apply_ma
from .sessions import (
    is_intraday_np,
    session_filter_mask_np,
    session_groups_np,
    tod_key_np,
)
from .wilder import true_range as _true_range

_DEFAULT_COLOR_BY_MA: Dict[str, str] = {
    "RMA": "#ffbb78",
    "SMA": "#8c564b",
    "EMA": "#17becf",
    "WMA": "#e377c2",
}

ATR_MODES: Tuple[str, ...] = ("rolling", "tod")
_ATR_TOD_AGGREGATORS: Tuple[str, ...] = ("mean", "median")
_ATR_TOD_SESSION_FILTERS: Tuple[str, ...] = (
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


class ATR:
    """Average True Range with user-selectable mode + smoothing.

    ``compute`` returns ``{"atr": ndarray}``.
    """

    kind_id: ClassVar[str] = "atr"
    kind_version: ClassVar[int] = 2
    params_schema: ClassVar[Tuple[ParamDef, ...]] = (
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
    default_style: ClassVar[Dict[str, LineStyle]] = {
        "atr": LineStyle(color=_DEFAULT_COLOR_BY_MA["RMA"], width=1.4),
    }
    reference_levels: ClassVar[Tuple[float, ...]] = ()
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

    def compute_arr(self, bars: Bars) -> Dict[str, np.ndarray]:
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

    def compute(self, candles: List[Candle]) -> Dict[str, np.ndarray]:
        return self.compute_arr(Bars.from_candles(candles))

    def _compute_tod_arr(self, bars: Bars, tr: np.ndarray) -> Dict[str, np.ndarray]:
        n = len(bars)
        out = np.full(n, np.nan, dtype=np.float64)

        if not is_intraday_np(bars):
            L = _TOD_DAILY_FALLBACK_LENGTH
            for i in range(L, n):
                window = tr[i - L + 1:i + 1]
                out[i] = _aggregate(window, "mean")
            return {"atr": out}

        admit_mask = session_filter_mask_np(bars, self.session_filter)
        groups = session_groups_np(bars, regular_only=True)
        if len(groups) < _MIN_WARMUP_SESSIONS + 1:
            return {"atr": out}

        tk = tod_key_np(bars)

        per_session: List[Dict[int, float]] = []
        for grp in groups:
            keyed: Dict[int, float] = {}
            for idx in grp:
                if not admit_mask[idx]:
                    continue
                v = float(tr[idx])
                if not np.isfinite(v):
                    continue
                keyed.setdefault(int(tk[idx]), v)
            per_session.append(keyed)

        for s in range(_MIN_WARMUP_SESSIONS, len(groups)):
            cur_grp = groups[s]
            prior_window = per_session[max(0, s - self.length):s]
            for idx in cur_grp:
                if not admit_mask[idx]:
                    continue
                k = int(tk[idx])
                baseline_vals = [d[k] for d in prior_window if k in d]
                if len(baseline_vals) < _MIN_WARMUP_SESSIONS:
                    continue
                out[idx] = _aggregate(
                    np.asarray(baseline_vals, dtype=np.float64),
                    self.aggregator,
                )
        return {"atr": out}
