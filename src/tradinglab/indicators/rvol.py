"""Relative Volume (RVOL) — unified single-class implementation.

A single :class:`RVOL` factory replaces the legacy ``rvol_simple`` /
``rvol_cum`` / ``rvol_tod`` trio AND the matching ``rvol_z_*`` z-score
trio. Mode is selected via a ``mode`` parameter; z-score output is
toggled via a ``z_score`` flag.

Modes
-----

* ``simple`` (default) — universal, every interval. Numerator is this
  bar's volume; denominator is the aggregator of the previous
  ``length`` bars. ``denominator_includes_current=True`` flips to
  TradingView's convention (default ``False`` — cleaner since the
  numerator and denominator don't share the bar being scored).

* ``cumulative`` — intraday only. Numerator is today's session-
  cumulative volume from the open up to the current bar. Denominator
  is the aggregator of cumulative-volume-at-same-time-of-day over the
  last ``length`` regular sessions. Useful for "is today shaping up
  unusually busy through 10:30?".

* ``time_of_day`` — intraday only. Numerator is this bar's volume.
  Denominator is the aggregator of volume across same-wall-clock bars
  on the last ``length`` sessions. Useful for spotting one-bar volume
  spikes against the typical flow at that exact minute of the trading
  day.

For ``cumulative`` and ``time_of_day``, ``length`` is interpreted as
days (a session count). For ``simple``, ``length`` is interpreted as
bars. This is the same single-knob convention the legacy z-score
classes used; it keeps the parameter schema flat at the cost of a
unit pun.

Z-score
-------

When ``z_score=True``, the output is the rolling z-score of the
underlying RVOL series with window ``length`` bars (sample stddev,
``ddof=1``). For ``cumulative``/``time_of_day`` modes the z-window is
also ``length`` bars — *not* days — by deliberate single-knob design:
the time-of-day-ness or cumulative-ness is already encoded in the
underlying RVOL signal; the z-score just asks "is **this** RVOL
print unusually high vs the recent past?".

Output key remains ``"rvol"`` regardless of ``z_score``. The pane
group switches dynamically: ``"rvol"`` when ``z_score=False``,
``"rvol_z"`` when True (different scales — z-scores live on a
0-centered axis, plain RVOL on a 1.0-centered axis). Reference levels
also switch: ``(1.0, threshold_warn, threshold_extreme)`` for plain
RVOL, ``(0.0, 2.0)`` for z-score (Bellafiore-style "+2σ" threshold).

Time-of-day key
---------------

Both ``cumulative`` and ``time_of_day`` modes key by **HH:MM in
exchange-local wall-clock time** rather than positional ordinal. This
is the ThinkOrSwim / Trade-Ideas convention and is the only correct
choice in the presence of half-day sessions, missing bars, and DST
shifts.

Aggregator
----------

``mean`` is the default. ``median`` is one click away and gives
robustness against earnings-day or news-day outliers in the lookback
window.

Warmup
------

A tunable lookback may not be fully populated for the first few days
of a backtest. We render a graceful partial value once at least
:data:`_MIN_WARMUP_SESSIONS` prior sessions are available; below that
threshold the output is NaN.

Historical rendering
--------------------

``cumulative`` and ``time_of_day`` modes render across **every**
session in the loaded history, not just the most recent one. For each
session ``s`` with at least :data:`_MIN_WARMUP_SESSIONS` prior
sessions, the baseline window is ``[s-length, s)``, and bars in
session ``s`` are scored against that window. This matches
TradingView / ToS conventions and makes the line readable even when
the chart is zoomed out or scrolled back from "today".

Zero-denominator handling
-------------------------

Both ``0/0`` (no historical baseline at this slot AND no current
volume) and ``N>0/0`` (no historical baseline but the stock IS
trading) emit ``0.0``. This conflates "we don't have history yet"
with "this is a quiet stock"; the alternative — emit a sentinel or
``inf`` — would distort the autoscaled y-axis of the shared pane.

Availability
------------

Mode-aware: ``simple`` works on every interval; ``cumulative`` and
``time_of_day`` are intraday only. The factory exposes
``is_available_for(interval, params)`` so the dialog and pane-budget
can filter mode-incompatible configs out before they render an empty
pane.
"""

from __future__ import annotations

import bisect
from collections.abc import Mapping
from typing import Any, ClassVar

import numpy as np

from ..core.bars import Bars
from ..models import Candle
from .base import (
    Availability,
    LineStyle,
    ParamDef,
    intraday_only,
)
from .sessions import (
    is_intraday_np,
    session_filter_mask_np,
    session_groups_np,
    tod_key_np,
)

_MODES: tuple[str, ...] = ("simple", "cumulative", "time_of_day")
_AGGREGATORS: tuple[str, ...] = ("mean", "median")
_SESSION_FILTERS: tuple[str, ...] = (
    "regular_only", "regular_plus_premarket", "extended",
)

#: Modes that require intraday data. Used by :meth:`RVOL.is_available_for`.
_INTRADAY_MODES: tuple[str, ...] = ("cumulative", "time_of_day")

#: Minimum prior sessions before partial-warmup output begins.
_MIN_WARMUP_SESSIONS = 5


# ---------------------------------------------------------------------------
# Validation + small helpers
# ---------------------------------------------------------------------------


def _aggregate(values: np.ndarray, aggregator: str) -> float:
    """Return aggregator of ``values`` ignoring NaNs; 0.0 on empty."""
    if values.size == 0:
        return 0.0
    if aggregator == "median":
        v = float(np.nanmedian(values))
    else:
        v = float(np.nanmean(values))
    if not np.isfinite(v):
        return 0.0
    return v


def _validate_length(length: int) -> int:
    n = int(length)
    if n < 1:
        raise ValueError("length must be >= 1")
    if n > 500:
        raise ValueError("length must be <= 500")
    return n


def _validate_thresholds(warn: float, extreme: float) -> tuple[float, float]:
    w = float(warn)
    e = float(extreme)
    if w <= 0.0 or e <= 0.0:
        raise ValueError("thresholds must be > 0")
    return w, e


# ---------------------------------------------------------------------------
# Per-mode RVOL compute (private; pure functions over Bars)
# ---------------------------------------------------------------------------


def _compute_simple(
    bars: Bars,
    length: int,
    aggregator: str,
    session_filter: str,
    denominator_includes_current: bool,
) -> np.ndarray:
    """Each admitted bar's volume divided by the aggregator of the
    previous ``length`` *admitted* bars' volumes.

    Operates on the dense subsequence of admitted bars (i.e. bars that
    pass ``session_filter``) — NOT on positional bars with the others
    NaN-masked. This is what users intuitively expect: with
    ``session_filter='regular_only'``, the rolling baseline at 9:30am
    of day N is "the previous ``length`` RTH bars" (which is yesterday's
    close stack), not "the previous ``length`` wall-clock bars" — most
    of which are NaN-masked pre-market and would otherwise force the
    indicator to bleed NaN through the first ``length`` minutes of every
    session. Same correctness intent, fully vectorised. Audit
    ``rvol-admitted-rolling``.
    """
    n = len(bars)
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0:
        return out

    admit_mask = session_filter_mask_np(bars, session_filter)
    admit_idx = np.flatnonzero(admit_mask)
    if admit_idx.size == 0:
        return out

    # Dense vol vector over admitted bars only. Cast to float so NaN
    # markers from upstream (gap candles, etc.) survive.
    admit_vol = bars.volume[admit_idx].astype(np.float64, copy=False)
    L = length
    m = admit_idx.size
    if m == 0:
        return out

    # ---- Rolling aggregate over the *previous* L admitted bars -------
    # Convention follows the legacy implementation:
    #   denominator_includes_current=False (default) → window = [k-L, k)
    #   denominator_includes_current=True            → window = [k-L+1, k]
    #
    # The legacy code only emitted a value when the window had *exactly*
    # ``L`` finite (non-NaN) samples. We preserve that gate — bars
    # within the first L-1 admitted positions of the entire history
    # therefore remain NaN, which is the intended warmup behaviour.
    if aggregator == "median":
        denom = _rolling_aggregate_window_median(
            admit_vol, L, denominator_includes_current,
        )
    else:
        denom = _rolling_aggregate_window_mean(
            admit_vol, L, denominator_includes_current,
        )

    # Numerator: this admitted bar's volume. NaN numerators (gap-candle
    # volume) propagate to NaN, matching the legacy short-circuit.
    num = admit_vol
    with np.errstate(invalid="ignore", divide="ignore"):
        rvol = np.where(
            np.isnan(denom) | np.isnan(num),
            np.nan,
            np.where(denom <= 0.0, 0.0, num / denom),
        )

    # Scatter back into the full-length output.
    out[admit_idx] = rvol
    return out


def _rolling_aggregate_window_mean(
    values: np.ndarray, L: int, include_current: bool,
) -> np.ndarray:
    """Rolling-mean over the previous ``L`` admitted samples.

    Returns NaN at positions where the window doesn't have ``L`` finite
    samples. Vectorised via cumsum on (value, valid_count) parallel
    arrays so NaN slots in the dense vector are honoured the same way
    ``np.nanmean`` would (require exactly ``L`` finite values).

    Drop-in equivalent of the legacy ``_aggregate(window, 'mean')`` +
    ``valid.size < L`` gate, but O(n) instead of O(n·L) with no Python
    loop. See module docstring "Warmup" + "Zero-denominator handling"
    for the policy this preserves. Audit ``rvol-admitted-rolling``.
    """
    n = values.size
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or L < 1:
        return out
    finite = np.isfinite(values)
    safe = np.where(finite, values, 0.0)
    # Prefix sums w/ a leading 0 so window [lo, hi) = csum[hi]-csum[lo].
    csum = np.concatenate(([0.0], np.cumsum(safe, dtype=np.float64)))
    cnt = np.concatenate(([0], np.cumsum(finite.astype(np.int64))))
    k = np.arange(n)
    if include_current:
        lo = np.maximum(0, k - L + 1)
        hi = k + 1
    else:
        lo = np.maximum(0, k - L)
        hi = k
    win_sum = csum[hi] - csum[lo]
    win_cnt = cnt[hi] - cnt[lo]
    mask = win_cnt == L
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(mask, win_sum / np.maximum(win_cnt, 1), np.nan)
    out[mask] = mean[mask]
    # Below-warmup or insufficient-finite positions remain NaN.
    return out


def _rolling_aggregate_window_median(
    values: np.ndarray, L: int, include_current: bool,
) -> np.ndarray:
    """Rolling-median over the previous ``L`` admitted samples.

    Stride-trick window + ``np.nanmedian`` along axis=1. Same NaN gate
    as ``_rolling_aggregate_window_mean``: requires exactly ``L``
    finite samples in the window, else NaN. O(n·L) but vectorised at
    the C level, so still 10-100x faster than the legacy Python loop
    at n≈5000, L≈20. Audit ``rvol-admitted-rolling``.
    """
    n = values.size
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or L < 1 or n < L:
        return out
    # Build a (n - L + 1, L) sliding view; row k of the view = the
    # window ENDING at index k+L-1 INCLUSIVE.
    view = np.lib.stride_tricks.sliding_window_view(values, L)
    # Per-window finite count gate.
    finite_cnt = np.sum(np.isfinite(view), axis=1)
    gate = finite_cnt == L
    medians = np.full(view.shape[0], np.nan, dtype=np.float64)
    if np.any(gate):
        # Suppress the all-NaN warning that np.nanmedian raises when a
        # full row is NaN — we explicitly mask those out below.
        with np.errstate(invalid="ignore"):
            medians[gate] = np.nanmedian(view[gate], axis=1)
    # Window k in ``view`` is values[k : k+L]; that corresponds to the
    # output position whose lookback ends just before k+L.
    if include_current:
        # window ends at k (inclusive). view row j = values[j:j+L];
        # set out[j+L-1] = medians[j].
        end_positions = np.arange(view.shape[0]) + (L - 1)
    else:
        # window ends just before k (i.e. window = [k-L, k)).
        # view row j = values[j:j+L] → out[j+L] = medians[j].
        end_positions = np.arange(view.shape[0]) + L
    valid = end_positions < n
    out[end_positions[valid]] = medians[valid]
    return out


def _compute_cumulative(
    bars: Bars,
    length: int,
    aggregator: str,
    session_filter: str,
) -> np.ndarray:
    """Today's session-cumulative volume / aggregated cumulative
    volume at same wall-clock time over the prior ``length`` sessions.

    Intraday only. Returns all-NaN on non-intraday data.
    """
    n = len(bars)
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or not is_intraday_np(bars):
        return out

    admit_mask = session_filter_mask_np(bars, session_filter)
    groups = session_groups_np(bars, regular_only=True)
    if len(groups) < _MIN_WARMUP_SESSIONS + 1:
        return out

    vol = bars.volume
    tk = tod_key_np(bars)  # int32, h*60+m

    # Per-session cumulative-volume keyed by tod.
    session_cum: list[dict[int, float]] = []
    for grp in groups:
        cum = 0.0
        keyed: dict[int, float] = {}
        for idx in grp:
            if not admit_mask[idx]:
                continue
            v = float(vol[idx])
            if not np.isfinite(v):
                v = 0.0
            cum += v
            keyed[int(tk[idx])] = cum
        session_cum.append(keyed)

    # Pre-sort each session's tod-keys + cumulative-values into parallel
    # arrays for O(log b) prefix lookup via bisect.
    sorted_keyed: list[tuple[list[int], list[float]]] = []
    for keyed in session_cum:
        if not keyed:
            sorted_keyed.append(([], []))
            continue
        items = sorted(keyed.items())
        sorted_keyed.append(
            ([k for k, _ in items], [v for _, v in items]),
        )

    for s in range(_MIN_WARMUP_SESSIONS, len(groups)):
        cur_grp = groups[s]
        cur_keyed = session_cum[s]
        prior_window = sorted_keyed[max(0, s - length):s]
        for idx in cur_grp:
            if not admit_mask[idx]:
                continue
            k = int(tk[idx])
            today_cum = cur_keyed.get(k)
            if today_cum is None:
                continue
            baseline_vals: list[float] = []
            for keys_list, vals_list in prior_window:
                if not keys_list:
                    continue
                pos = bisect.bisect_right(keys_list, k) - 1
                if pos >= 0:
                    baseline_vals.append(vals_list[pos])
            if len(baseline_vals) < _MIN_WARMUP_SESSIONS:
                continue
            denom = _aggregate(
                np.asarray(baseline_vals, dtype=np.float64),
                aggregator,
            )
            if denom <= 0.0:
                out[idx] = 0.0
            else:
                out[idx] = float(today_cum) / denom
    return out


def _compute_time_of_day(
    bars: Bars,
    length: int,
    aggregator: str,
    session_filter: str,
) -> np.ndarray:
    """This bar's volume vs the aggregator of same-wall-clock-bar
    volumes across the last ``length`` regular sessions.

    Intraday only. Returns all-NaN on non-intraday data.
    """
    n = len(bars)
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or not is_intraday_np(bars):
        return out

    admit_mask = session_filter_mask_np(bars, session_filter)
    groups = session_groups_np(bars, regular_only=True)
    if len(groups) < _MIN_WARMUP_SESSIONS + 1:
        return out

    vol = bars.volume
    tk = tod_key_np(bars)

    # Per-session per-tod-key volume map.
    per_session: list[dict[int, float]] = []
    for grp in groups:
        keyed: dict[int, float] = {}
        for idx in grp:
            if not admit_mask[idx]:
                continue
            v = float(vol[idx])
            if not np.isfinite(v):
                v = 0.0
            k = int(tk[idx])
            # Sum repeats (DST / duplicate timestamps).
            keyed[k] = keyed.get(k, 0.0) + v
        per_session.append(keyed)

    for s in range(_MIN_WARMUP_SESSIONS, len(groups)):
        cur_grp = groups[s]
        prior_window = per_session[max(0, s - length):s]
        for idx in cur_grp:
            if not admit_mask[idx]:
                continue
            k = int(tk[idx])
            baseline_vals = [d[k] for d in prior_window if k in d]
            if len(baseline_vals) < _MIN_WARMUP_SESSIONS:
                continue
            v_now = float(vol[idx])
            if not np.isfinite(v_now):
                v_now = 0.0
            denom = _aggregate(
                np.asarray(baseline_vals, dtype=np.float64),
                aggregator,
            )
            if denom <= 0.0:
                out[idx] = 0.0
            else:
                out[idx] = v_now / denom
    return out


def _rolling_zscore(rvol: np.ndarray, length: int) -> np.ndarray:
    """Return the rolling z-score of ``rvol`` over ``length`` bars.

    Sample stddev (``ddof=1``). NaN-safe: bars where the underlying
    series is NaN are excluded from each window's mean/stddev. A bar's
    z is NaN unless:

    1. It has at least 2 finite RVOL values in its lookback window, AND
    2. The window's stddev is strictly positive, AND
    3. The bar's own RVOL value is finite.

    The window is the last ``length`` consecutive bars (inclusive of
    the current bar).
    """
    n = rvol.shape[0]
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0 or length < 2:
        return out

    L = int(length)
    for i in range(n):
        if not np.isfinite(rvol[i]):
            continue
        lo = max(0, i - L + 1)
        window = rvol[lo : i + 1]
        finite = window[np.isfinite(window)]
        if finite.size < 2:
            continue
        mean = float(finite.mean())
        std = float(finite.std(ddof=1))
        if not np.isfinite(std) or std <= 0.0:
            continue
        out[i] = (float(rvol[i]) - mean) / std
    return out


def _dispatch_compute(
    bars: Bars,
    *,
    mode: str,
    length: int,
    aggregator: str,
    session_filter: str,
    denominator_includes_current: bool,
    z_score: bool,
) -> np.ndarray:
    """Run the requested mode's RVOL compute, optionally take rolling z."""
    if mode == "simple":
        rvol = _compute_simple(
            bars, length, aggregator, session_filter,
            denominator_includes_current,
        )
    elif mode == "cumulative":
        rvol = _compute_cumulative(bars, length, aggregator, session_filter)
    elif mode == "time_of_day":
        rvol = _compute_time_of_day(bars, length, aggregator, session_filter)
    else:  # pragma: no cover — guarded in __init__
        raise ValueError(f"unknown mode {mode!r}")
    if not z_score:
        return rvol
    return _rolling_zscore(rvol, length)


# ---------------------------------------------------------------------------
# Public unified RVOL factory
# ---------------------------------------------------------------------------


class RVOL:
    """Unified Relative Volume indicator covering all three modes
    (simple / cumulative / time_of_day) plus optional rolling z-score.

    See module docstring for semantics. Backward-compatible with
    persisted configs from the legacy ``rvol_simple`` / ``rvol_cum`` /
    ``rvol_tod`` / ``rvol_z_*`` indicators via
    :data:`tradinglab.indicators.base._KIND_ID_MIGRATIONS`.
    """

    kind_id: ClassVar[str] = "rvol"
    kind_version: ClassVar[int] = 1
    #: Default pane group; the actual pane group depends on ``z_score``
    #: at runtime — see :meth:`pane_group_for`.
    pane_group: ClassVar[str] = "rvol"
    overlay: ClassVar[bool] = False

    #: Whitelist of params that actually affect compute output. Used by
    #: :func:`tradinglab.scanner.fields._build_indicator_specs` to
    #: prune purely-cosmetic params (``threshold_warn`` /
    #: ``threshold_extreme`` paint axhlines on the chart but are never
    #: referenced inside :meth:`compute_arr`) from the entries / exits /
    #: scanner block-editor forms. The full schema stays visible in the
    #: chart-side Manage Indicators dialog.
    TRIGGER_RELEVANT_PARAMS: ClassVar[tuple[str, ...]] = (
        "mode", "length", "aggregator", "session_filter",
        "denominator_includes_current", "z_score",
    )

    params_schema: ClassVar[tuple[ParamDef, ...]] = (
        ParamDef("mode", "choice", default="simple",
                 choices=_MODES, description="Mode"),
        ParamDef("length", "int", default=20, min=1, max=500, step=1,
                 description="Length"),
        ParamDef("aggregator", "choice", default="mean",
                 choices=_AGGREGATORS, description="Aggregator"),
        ParamDef("session_filter", "choice", default="regular_only",
                 choices=_SESSION_FILTERS, description="Session filter"),
        ParamDef("denominator_includes_current", "bool", default=False,
                 description="Include current in denom"),
        ParamDef("z_score", "bool", default=False,
                 description="Z-score"),
        ParamDef("threshold_warn", "float", default=2.0, min=0.1, max=100.0,
                 step=0.1, description="Warn level"),
        ParamDef("threshold_extreme", "float", default=5.0, min=0.1,
                 max=100.0, step=0.1, description="Extreme level"),
    )
    default_style: ClassVar[dict[str, LineStyle]] = {
        "rvol": LineStyle(color="#aec7e8", width=1.4),
    }
    reference_levels: ClassVar[tuple[float, ...]] = ()

    @classmethod
    def is_available_for(
        cls,
        interval: str,
        params: Mapping[str, Any] | None = None,
    ) -> Availability:
        """Mode-aware availability check.

        ``simple`` mode works on every interval. ``cumulative`` and
        ``time_of_day`` require an intraday interval — matching the
        legacy ``rvol_cum`` / ``rvol_tod`` ``is_available_for`` so
        the migration is behavior-preserving.
        """
        mode = str((params or {}).get("mode", "simple"))
        if mode in _INTRADAY_MODES:
            return intraday_only(interval)
        return Availability(True, "")

    @classmethod
    def pane_group_for(cls, params: Mapping[str, Any] | None) -> str:
        """Return the pane group for an indicator with these params.

        Z-scores live on a 0-centered axis; plain RVOL lives on a
        1.0-centered axis. Mixing the two on a single pane mangles the
        autoscale, so they get separate pane groups.
        """
        return "rvol_z" if bool((params or {}).get("z_score", False)) else "rvol"

    def __init__(
        self,
        mode: str = "simple",
        length: int = 20,
        aggregator: str = "mean",
        session_filter: str = "regular_only",
        denominator_includes_current: bool = False,
        z_score: bool = False,
        threshold_warn: float = 2.0,
        threshold_extreme: float = 5.0,
    ) -> None:
        if mode not in _MODES:
            raise ValueError(f"mode must be one of {_MODES!r}")
        if aggregator not in _AGGREGATORS:
            raise ValueError(f"aggregator must be one of {_AGGREGATORS!r}")
        if session_filter not in _SESSION_FILTERS:
            raise ValueError(f"session_filter must be one of {_SESSION_FILTERS!r}")
        self.mode = mode
        self.length = _validate_length(length)
        # Z-score needs at least 2 samples for sample stddev (ddof=1).
        if bool(z_score) and self.length < 2:
            raise ValueError("z_score requires length >= 2")
        self.aggregator = aggregator
        self.session_filter = session_filter
        self.denominator_includes_current = bool(denominator_includes_current)
        self.z_score = bool(z_score)
        self.threshold_warn, self.threshold_extreme = _validate_thresholds(
            threshold_warn, threshold_extreme,
        )
        # Per-instance reference levels: z-scores get the Bellafiore
        # 0/+2σ pair; plain RVOL gets the 1.0 baseline + warn/extreme.
        if self.z_score:
            self.reference_levels: tuple[float, ...] = (0.0, 2.0)
        else:
            self.reference_levels = (
                1.0, float(self.threshold_warn), float(self.threshold_extreme),
            )
        # Display name reflects mode + z-score for log/debug readability.
        suffix = " Z" if self.z_score else ""
        mode_short = {"simple": "", "cumulative": " Cum",
                      "time_of_day": " ToD"}[self.mode]
        self.name = f"RVOL{mode_short}{suffix}({self.length})"

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        return {
            "rvol": _dispatch_compute(
                bars,
                mode=self.mode,
                length=self.length,
                aggregator=self.aggregator,
                session_filter=self.session_filter,
                denominator_includes_current=self.denominator_includes_current,
                z_score=self.z_score,
            ),
        }

    def compute(self, candles: list[Candle]) -> dict[str, np.ndarray]:
        return self.compute_arr(Bars.from_candles(candles))
