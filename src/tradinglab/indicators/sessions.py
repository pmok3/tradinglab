"""Session-grouping helpers shared across session-aware indicators.

Indicators that depend on knowing where one regular-trading-session
ends and the next begins — VWAP, Anchored VWAP, the Relative-Volume
family — all need the same primitives:

* group bars into per-day buckets,
* tell whether a series is intraday or daily-or-higher,
* key bars by wall-clock time-of-day for cross-session comparison.

Centralising them here keeps the rules consistent and gives any
future "session-anchored" indicator one place to depend on.

All helpers are pure, accept ordered candle lists, and never mutate
their inputs.

Timezone convention
-------------------
Candle ``date`` fields are assumed to already be in exchange-local
wall-clock time (US/Eastern for the equities the app supports). This
matches every existing data source the app fetches from. Timezone-aware
``datetime`` instances are accepted; their tzinfo is stripped before
comparison so naive and aware datasets group identically.

NumPy-native variants
---------------------
The ``*_np`` variants below operate directly on a :class:`Bars` view
(column-major NumPy arrays, populated via ``Bars.from_candles``). They
return values byte-for-byte equivalent to the candle versions but skip
per-bar Python attribute access.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..core.bars import Bars
from ..models import Candle

# --- Time-of-day keying -----------------------------------------------------

#: Wall-clock key for cross-session bucketing. ``(hour, minute)``.
TodKey = tuple[int, int]


def tod_key(c: Candle) -> TodKey | None:
    """Return ``(hour, minute)`` for ``c`` or ``None`` if unparsable.

    Uses the candle's stored datetime as-is (assumed exchange-local).
    Robust to tz-aware datetimes — the offset is irrelevant once we
    only look at ``hour`` / ``minute`` of the local wall clock.
    """
    try:
        return (int(c.date.hour), int(c.date.minute))
    except Exception:  # noqa: BLE001
        return None


def tod_key_np(bars: Bars) -> np.ndarray:
    """Vectorised ``(hour, minute)`` packed as ``hour * 60 + minute``.

    Returns a ``(n,)`` int32 array where each value is in ``[0, 1440)``.
    Compatible with ``tod_key(c)``: the (h, m) tuple equals
    ``(v // 60, v % 60)``. Compact integer keys make
    ``np.add.at`` / ``np.bincount`` bucketing trivial.
    """
    ts = bars.timestamps
    if ts.size == 0:
        return np.empty(0, dtype=np.int32)
    ns = ts.astype("int64")
    return ((ns // 60_000_000_000) % 1440).astype(np.int32, copy=False)


# --- Session grouping -------------------------------------------------------


def session_groups(
    candles: Sequence[Candle], *, regular_only: bool = True,
) -> list[list[int]]:
    """Group candle indices by calendar trading day.

    Returns a list of per-session lists of indices. Sessions are
    detected by ``c.date.date()`` (so any timestamps that share a
    wall-clock date go in the same bucket). The result preserves
    the order of ``candles``.

    ``regular_only=True`` (default) skips bars whose ``session``
    attribute is not ``"regular"`` and gap fillers entirely. The
    skipped bars do NOT split a session in two — only the date
    boundary does — they are simply omitted from the bucket.
    """
    out: list[list[int]] = []
    cur_day = None
    cur_bucket: list[int] | None = None
    for i, c in enumerate(candles):
        if getattr(c, "is_gap", False):
            continue
        if regular_only and getattr(c, "session", "regular") != "regular":
            continue
        try:
            day = c.date.date()
        except Exception:  # noqa: BLE001
            continue
        if day != cur_day:
            cur_day = day
            cur_bucket = []
            out.append(cur_bucket)
        cur_bucket.append(i)  # type: ignore[union-attr]
    return out


def session_groups_np(
    bars: Bars, *, regular_only: bool = True,
) -> list[np.ndarray]:
    """Vectorised :func:`session_groups`.

    Returns a list of int64 ndarrays of admitted indices, one per
    calendar day, preserving input order. Equivalent (modulo
    ndarray-vs-list) to ``session_groups(bars.candles, ...)``.
    """
    n = len(bars)
    if n == 0:
        return []
    sess = bars.session
    if regular_only:
        admit = (sess == "regular")
    else:
        admit = (sess != "gap")
    if not admit.any():
        return []
    days = bars.timestamps.astype("datetime64[D]").astype("int64")
    idx_all = np.arange(n, dtype=np.int64)
    keep_idx = idx_all[admit]
    keep_days = days[admit]
    if keep_idx.size == 0:
        return []
    boundaries = np.flatnonzero(np.diff(keep_days)) + 1
    return np.split(keep_idx, boundaries)


# --- Interval detection -----------------------------------------------------


def is_intraday(candles: Sequence[Candle]) -> bool:
    """True iff the median spacing between non-gap candles is < 23 hours.

    Mirrors :func:`vwap._is_daily_or_higher`; by deferring to a single
    helper, both VWAP and the RVOL family stay in lock-step on the
    intraday/daily boundary.
    """
    deltas: list[float] = []
    prev_dt = None
    for c in candles:
        if getattr(c, "is_gap", False):
            continue
        if prev_dt is not None:
            try:
                dt = (c.date - prev_dt).total_seconds()
            except Exception:  # noqa: BLE001
                dt = 0.0
            if dt > 0:
                deltas.append(dt)
            if len(deltas) >= 30:
                break
        prev_dt = c.date
    if not deltas:
        return False
    deltas.sort()
    median = deltas[len(deltas) // 2]
    return median < 23 * 3600


def is_intraday_np(bars: Bars) -> bool:
    """Vectorised :func:`is_intraday` over a Bars view.

    Same heuristic: median of the first up-to-30 positive inter-bar
    deltas (excluding gap bars) is < 23h.
    """
    n = len(bars)
    if n < 2:
        return False
    keep = bars.session != "gap"
    ts = bars.timestamps[keep]
    if ts.size < 2:
        return False
    diffs = np.diff(ts.astype("int64"))
    pos = diffs[diffs > 0]
    if pos.size == 0:
        return False
    sample = pos[:30]
    median_ns = float(np.median(sample))
    return median_ns < 23 * 3600 * 1_000_000_000


# --- Premarket / extended-hours filter --------------------------------------


def session_filter_predicate(filter_mode: str):
    """Return a callable ``(candle) -> bool`` admitting bars that
    pass ``filter_mode``.

    - ``"regular_only"``: only ``session == "regular"``.
    - ``"regular_plus_premarket"``: regular OR ``"pre"``.
    - ``"extended"``: regular OR pre OR post.

    Gap fillers (``is_gap=True``) are always rejected.
    Unknown filter values fall back to ``regular_only`` (safe default).
    """
    if filter_mode == "extended":
        admitted = ("regular", "pre", "post", "extended")
    elif filter_mode == "regular_plus_premarket":
        admitted = ("regular", "pre")
    else:
        admitted = ("regular",)

    def _pred(c: Candle) -> bool:
        if getattr(c, "is_gap", False):
            return False
        return getattr(c, "session", "regular") in admitted
    return _pred


def session_filter_mask_np(bars: Bars, filter_mode: str) -> np.ndarray:
    """Vectorised :func:`session_filter_predicate` over a Bars view.

    Returns a boolean ``(n,)`` mask: ``True`` for bars that pass the
    filter (gap fillers always reject).
    """
    sess = bars.session
    if sess.size == 0:
        return np.zeros(0, dtype=bool)
    if filter_mode == "extended":
        return (
            (sess == "regular") | (sess == "pre")
            | (sess == "post") | (sess == "extended")
        )
    if filter_mode == "regular_plus_premarket":
        return (sess == "regular") | (sess == "pre")
    return (sess == "regular")
