"""Relative Relative Volume (RRVOL) — unified single-class implementation.

A single :class:`RRVOL` factory replaces the legacy ``rrvol_simple`` /
``rrvol_cum`` / ``rrvol_tod`` trio AND adds z-score support that did
not previously exist for RRVOL.

RRVOL answers "is this stock trading unusually heavily *relative to
the broad market right now*?" by dividing the stock's RVOL (at the
selected mode) by SPY's RVOL of the same flavour. A ratio above 1.0
means the stock is busy beyond what the tape's general activity
already explains; a ratio below 1.0 means the stock looks busy only
because *everything* is busy (Fed day, OPEX, etc.).

Reference symbol
----------------

The denominator symbol is **hardcoded SPY**. This is a deliberate
product decision: traders read RRVOL as "vs. the market", and the
market is SPY for US equities. A future extension could expose a
``compare_symbol`` parameter, but that complicates indicator-cache
keys and the data-fetch lifecycle without a clear use-case. Defer.

Data plumbing
-------------

SPY bars are fetched asynchronously via
:mod:`tradinglab.core.reference_data`. The render path supplies
``interval`` + ``source`` via :mod:`tradinglab.core.render_context`;
RRVOL reads that context and looks up cached SPY :class:`Bars`. On a
cache miss the registry schedules a background fetch and the
indicator emits all-NaN for this render — when SPY arrives, the
on-arrival callback wired by ``ChartApp`` clears the indicator cache
and triggers a re-render.

Primary == SPY
--------------

When the primary symbol IS SPY, the underlying ratio is identically
1.0. We detect this via the ``primary_symbol`` field of
:func:`current_context`, NOT by trying to compare bars or candles —
separate fetches of "SPY" can yield slightly different histories.
With ``z_score=False`` the indicator emits a flat 1.0 line wherever
the primary RVOL is finite. With ``z_score=True`` the constant
series collapses to NaN under the rolling-z (zero stddev) — that's
the correct mathematical answer.

Alignment
---------

For each primary bar at timestamp ``t``, we look up the SPY bar with
the same ``np.datetime64[ns]`` value. Within this app every fetcher
normalises to exchange-local naive timestamps (see
``data/normalize.py``), so equality is exact. Primary bars without a
matching SPY bar (rare: pre-IPO, holiday-shifted half-days where SPY
didn't trade, etc.) emit NaN.

Z-score
-------

When ``z_score=True``, the rolling z is taken over the **rrvol ratio
series** itself, not over either leg. Z-scoring the legs separately
and then dividing the z-scores is mathematically meaningless; the
intent of "RRVOL z-score" is "is *this* stock's relative-vs-SPY busy-
ness unusual vs its own recent RRVOL history?".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

import numpy as np

from ..core.bars import Bars
from ..core.reference_data import get_reference_bars
from ..core.render_context import current_context
from ..models import Candle
from .base import (
    Availability,
    LineStyle,
    ParamDef,
    intraday_only,
)
from .rvol import (
    _AGGREGATORS,
    _INTRADAY_MODES,
    _MODES,
    _SESSION_FILTERS,
    _dispatch_compute,
    _rolling_zscore,
    _validate_length,
    _validate_thresholds,
)

_REFERENCE_SYMBOL = "SPY"


def _compute_rrvol_arr(
    primary_bars: Bars,
    *,
    mode: str,
    length: int,
    aggregator: str,
    session_filter: str,
    denominator_includes_current: bool,
    z_score: bool,
) -> np.ndarray:
    """Compute the RRVOL series: primary RVOL / SPY RVOL, optionally z-scored."""
    n = len(primary_bars)
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0:
        return out

    ctx = current_context()
    interval = ctx.get("interval")
    source = ctx.get("source")
    primary_symbol = ctx.get("primary_symbol")

    # Need both interval + source to even attempt a reference lookup.
    # Without context (e.g. a test calling compute_arr directly) we
    # cannot align to SPY, so emit all-NaN gracefully.
    if not interval or not source:
        return out

    # Compute primary RVOL (without z-score yet — z is taken on the ratio).
    primary_rvol = _dispatch_compute(
        primary_bars,
        mode=mode, length=length, aggregator=aggregator,
        session_filter=session_filter,
        denominator_includes_current=denominator_includes_current,
        z_score=False,
    )

    if (primary_symbol or "").upper() == _REFERENCE_SYMBOL:
        # numerator == denominator; ratio is identically 1.0 wherever
        # primary RVOL itself is finite.
        finite = np.isfinite(primary_rvol)
        out[finite] = 1.0
        if z_score:
            # Constant 1.0 ⇒ zero stddev ⇒ z is NaN by design. Nothing
            # to do; just bypass the rolling-z to avoid generating
            # spurious values when finite is all-False (n=0 case).
            return _rolling_zscore(out, length)
        return out

    spy_bars = get_reference_bars(source, _REFERENCE_SYMBOL, interval)
    if spy_bars is None or len(spy_bars) == 0:
        # SPY not yet warmed; the on-arrival callback will trigger a
        # re-render once data lands.
        return out

    spy_rvol = _dispatch_compute(
        spy_bars,
        mode=mode, length=length, aggregator=aggregator,
        session_filter=session_filter,
        denominator_includes_current=denominator_includes_current,
        z_score=False,
    )

    # Align timestamps via a vectorized exact-match search.
    # ``Bars.timestamps`` is ``datetime64[ns]`` exchange-local naive
    # (per fetcher normalize).
    spy_ts = spy_bars.timestamps
    if spy_ts.shape[0] != spy_rvol.shape[0]:
        return out
    spy_ts_i = spy_ts.astype("int64", copy=False)
    pri_ts_i = primary_bars.timestamps.astype("int64", copy=False)

    sort_idx = np.argsort(spy_ts_i, kind="stable")
    sorted_spy_ts = spy_ts_i[sort_idx]
    match_pos = np.searchsorted(sorted_spy_ts, pri_ts_i, side="right") - 1
    safe_pos = np.clip(match_pos, 0, sorted_spy_ts.size - 1)
    valid = (match_pos >= 0) & (sorted_spy_ts[safe_pos] == pri_ts_i)

    denom = np.full(n, np.nan, dtype=np.float64)
    matched_idx = sort_idx[safe_pos]
    denom[valid] = spy_rvol[matched_idx[valid]]

    finite = np.isfinite(primary_rvol) & np.isfinite(denom)
    out[finite & (denom <= 0.0)] = 0.0
    positive = finite & (denom > 0.0)
    out[positive] = primary_rvol[positive] / denom[positive]

    if z_score:
        return _rolling_zscore(out, length)
    return out


class RRVOL:
    """Unified Relative-Relative Volume indicator (vs. SPY).

    Mirrors :class:`tradinglab.indicators.rvol.RVOL`'s parameter
    schema; computes RVOL on primary + SPY independently, divides
    element-wise, and (when ``z_score=True``) takes the rolling z of
    the resulting ratio series. Backward-compatible with persisted
    configs from the legacy ``rrvol_simple`` / ``rrvol_cum`` /
    ``rrvol_tod`` indicators via
    :data:`tradinglab.indicators.base._KIND_ID_MIGRATIONS`.
    """

    kind_id: ClassVar[str] = "rrvol"
    kind_version: ClassVar[int] = 1
    pane_group: ClassVar[str] = "rvol"
    overlay: ClassVar[bool] = False

    #: Whitelist of params that actually affect compute output. Mirrors
    #: :attr:`tradinglab.indicators.rvol.RVOL.TRIGGER_RELEVANT_PARAMS`;
    #: see that class for rationale. ``threshold_warn`` and
    #: ``threshold_extreme`` are cosmetic-only reference-line knobs.
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
        "rvol": LineStyle(color="#c5b0d5", width=1.4),
    }
    reference_levels: ClassVar[tuple[float, ...]] = ()

    @classmethod
    def is_available_for(
        cls,
        interval: str,
        params: Mapping[str, Any] | None = None,
    ) -> Availability:
        """Mode-aware availability — same gating as :class:`RVOL`."""
        mode = str((params or {}).get("mode", "simple"))
        if mode in _INTRADAY_MODES:
            return intraday_only(interval)
        return Availability(True, "")

    @classmethod
    def pane_group_for(cls, params: Mapping[str, Any] | None) -> str:
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
        if bool(z_score) and self.length < 2:
            raise ValueError("z_score requires length >= 2")
        self.aggregator = aggregator
        self.session_filter = session_filter
        self.denominator_includes_current = bool(denominator_includes_current)
        self.z_score = bool(z_score)
        self.threshold_warn, self.threshold_extreme = _validate_thresholds(
            threshold_warn, threshold_extreme,
        )
        if self.z_score:
            self.reference_levels: tuple[float, ...] = (0.0, 2.0)
        else:
            self.reference_levels = (
                1.0, float(self.threshold_warn), float(self.threshold_extreme),
            )
        suffix = " Z" if self.z_score else ""
        mode_short = {"simple": "", "cumulative": " Cum",
                      "time_of_day": " ToD"}[self.mode]
        self.name = f"RRVOL{mode_short}{suffix}({self.length})"

    def compute_arr(self, bars: Bars) -> dict[str, np.ndarray]:
        return {
            "rvol": _compute_rrvol_arr(
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
