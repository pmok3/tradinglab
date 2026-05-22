"""Pair-filter and timestamp-align two candle series for compare mode.

Pure data; no Tk/mpl. Used by the GUI chart in compare mode *and* by the
replay/backtest layer when simulating a compare pair.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..constants import is_intraday
from ..models import Candle


def apply_pair_filter(
    primary_raw: Optional[List[Candle]],
    compare_raw: Optional[List[Candle]],
    interval: str,
    extended_hours: bool,
) -> Tuple[List[Candle], List[Candle]]:
    """Return ``(primary, compare)`` filtered for the current settings.

    Coordinates extended-hours across the pair: the toggle only *takes
    effect* when the interval is intraday **and** both sides actually
    have pre/post bars. If the user has Extended Hours on but one side
    is RTH-only, we fall back to RTH-only on **both** so that right-edge
    alignment doesn't silently mismatch an extended bar on one side
    with an RTH bar on the other.

    **Identity preservation**: when the filter is a no-op (no extended
    bars to drop), the **original list** is returned — streaming relies
    on object identity for in-place tick updates to be observable via
    aligned views.
    """
    want_extended = is_intraday(interval) and extended_hours

    if want_extended and compare_raw:
        primary_has_ext = bool(primary_raw) and any(
            c.is_extended for c in primary_raw
        )
        compare_has_ext = any(c.is_extended for c in compare_raw)
        if not (primary_has_ext and compare_has_ext):
            want_extended = False

    def _filter(cs: Optional[List[Candle]]) -> List[Candle]:
        if not cs:
            return []
        if want_extended:
            return cs
        if not any(c.is_extended for c in cs):
            return cs
        return [c for c in cs if c.session == "regular"]

    return _filter(primary_raw), _filter(compare_raw)


def _normalize_pairing_key(d):
    """Strip tzinfo for use as a dict / sort key.

    The two sides of a compare pair can come from different sources
    (live yfinance vs. disk-cached pickle vs. fake test data) and may
    disagree on tz-awareness even when they describe the same wall
    clock — disk-cache pickles preserve the tz the provider returned
    (typically ``America/New_York`` for US equities), while in-memory
    fake/streaming data is often naive. Mixing tz-aware and tz-naive
    datetimes inside ``set(...) | set(...)`` or ``sorted(...)`` raises
    ``TypeError: can't compare offset-naive and offset-aware datetimes``.

    Both sides represent the same exchange wall time, so we normalize
    keys by stripping tzinfo. Returned candles retain their original
    ``.date`` (with tz, if any); only the dict keys are normalized.
    """
    tz = getattr(d, "tzinfo", None)
    if tz is None:
        return d
    try:
        return d.replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        return d


def align_pair(
    primary: List[Candle],
    compare: List[Candle],
) -> Tuple[List[Candle], List[Candle]]:
    """Timestamp-align two candle series.

    Produces two equal-length lists whose i-th entries share the same
    ``date``. Missing slots on one side get a :py:meth:`Candle.gap`
    placeholder. Real bars in the output are the **same objects** as in
    the inputs — streaming tick updates remain visible through the
    aligned view.

    Tz-mixed inputs (one naive, one aware) are tolerated: keys are
    normalized via :func:`_normalize_pairing_key` so wall-clock
    alignment works regardless of provenance.
    """
    if not primary or not compare:
        return list(primary or []), list(compare or [])

    lo_day = max(primary[0].date.date(), compare[0].date.date())
    hi_day = min(primary[-1].date.date(), compare[-1].date.date())
    if lo_day > hi_day:
        return list(primary), list(compare)

    _k = _normalize_pairing_key
    by_p = {_k(c.date): c for c in primary if lo_day <= c.date.date() <= hi_day}
    by_c = {_k(c.date): c for c in compare if lo_day <= c.date.date() <= hi_day}
    merged = sorted(set(by_p) | set(by_c))

    out_p: List[Candle] = []
    out_c: List[Candle] = []
    for d in merged:
        out_p.append(by_p.get(d) or Candle.gap(d))
        out_c.append(by_c.get(d) or Candle.gap(d))
    return out_p, out_c


def apply_pair_filter_and_align(
    primary_raw: Optional[List[Candle]],
    compare_raw: Optional[List[Candle]],
    interval: str,
    extended_hours: bool,
) -> Tuple[List[Candle], List[Candle]]:
    """Pair-filter, then timestamp-align in compare mode."""
    primary, compare = apply_pair_filter(
        primary_raw, compare_raw, interval, extended_hours,
    )
    if compare_raw is not None and primary and compare:
        primary, compare = align_pair(primary, compare)
    return primary, compare
