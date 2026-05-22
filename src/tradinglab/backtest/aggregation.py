"""Aggregate intraday candles to a higher timeframe.

Used by the sandbox controller to render a chosen display timeframe
(e.g. 15m, 30m, 1h) on top of the single primary tick interval the
user actually fetched (e.g. 5m). Buckets are anchored on each
session's *first primary-bar timestamp* so RTH 1h aggregation
produces a leading bar at 09:30 → 10:30 (rather than the UTC-clock
09:00 → 10:00 a fixed-modulo bucketing would emit).

The last emitted bucket is intentionally treated as in-progress: as
each new primary bar arrives the caller can re-aggregate the full
visible primary list and the trailing higher-TF bar grows in place
(volume sums, high = running max, low = running min, close = latest
primary close).

Public surface:

* :data:`INTERVAL_MINUTES` — minute count per supported interval string.
* :func:`interval_minutes` — lookup with explicit ``ValueError``.
* :func:`divides_evenly` — cheap precondition check the caller can use
  to validate selectable timeframe combos before calling :func:`aggregate`.
* :func:`aggregate` — primary candle list → higher-TF candle list.
"""

from __future__ import annotations

import datetime as _dt
from typing import List

from ..models import Candle

INTERVAL_MINUTES = {
    "1m": 1,
    "2m": 2,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "60m": 60,
}


def interval_minutes(itv: str) -> int:
    """Return minute count for ``itv``. Raises :class:`ValueError` if unknown."""
    if itv not in INTERVAL_MINUTES:
        raise ValueError(f"unsupported interval {itv!r}")
    return INTERVAL_MINUTES[itv]


def divides_evenly(primary: str, target: str) -> bool:
    """True iff ``target`` is an integer multiple of ``primary`` minutes.

    A ``False`` return means primary→target aggregation is not
    well-defined (e.g. ``2m`` cannot reconstruct true ``5m`` bars).
    Unknown intervals also return ``False``.
    """
    pm = INTERVAL_MINUTES.get(primary)
    tm = INTERVAL_MINUTES.get(target)
    if pm is None or tm is None or pm <= 0 or tm <= 0:
        return False
    return tm % pm == 0


def aggregate(
    primary_candles: List[Candle],
    primary_interval: str,
    target_interval: str,
) -> List[Candle]:
    """Aggregate ``primary_candles`` (at ``primary_interval``) up to ``target_interval``.

    Returns a fresh list of :class:`Candle`. Buckets are anchored on
    each calendar date's *first* primary bar timestamp — so RTH 1h
    buckets begin at session open (09:30 ET in the data's tz), not at
    a UTC-modulo boundary. Each new calendar date resets the anchor.

    The trailing emitted bar is implicitly in-progress whenever the
    last primary bar's bucket is not yet full; callers re-aggregate
    after every new primary bar to keep the in-progress higher-TF bar
    fresh.

    Raises:
        ValueError: ``target_interval`` does not divide ``primary_interval``
            evenly, or either interval string is unsupported.
    """
    if not primary_candles:
        return []
    pm = interval_minutes(primary_interval)
    tm = interval_minutes(target_interval)
    if tm == pm:
        # Identity aggregation — return a shallow copy so the caller can
        # mutate without affecting the source list.
        return list(primary_candles)
    if tm % pm != 0:
        raise ValueError(
            f"cannot aggregate {primary_interval}->{target_interval}: "
            f"{tm} not divisible by {pm}"
        )

    out: List[Candle] = []
    bucket_start: _dt.datetime | None = None
    bucket_open = bucket_high = bucket_low = bucket_close = 0.0
    bucket_vol = 0
    bucket_session = "regular"
    session_anchor: _dt.datetime | None = None
    last_date: _dt.date | None = None

    def _flush() -> None:
        if bucket_start is None:
            return
        out.append(Candle(
            date=bucket_start,
            open=bucket_open,
            high=bucket_high,
            low=bucket_low,
            close=bucket_close,
            volume=bucket_vol,
            session=bucket_session,
        ))

    for c in primary_candles:
        d = c.date
        date_only = d.date()
        if date_only != last_date:
            # New session — reset the anchor so 1h buckets line up
            # with session-open rather than UTC-clock midnight.
            session_anchor = d
            last_date = date_only
        # Bucket index relative to today's session anchor.
        delta_min = int((d - session_anchor).total_seconds() // 60)
        if delta_min < 0:
            delta_min = 0
        bucket_idx = delta_min // tm
        candidate_start = session_anchor + _dt.timedelta(
            minutes=bucket_idx * tm)

        if bucket_start != candidate_start:
            _flush()
            bucket_start = candidate_start
            bucket_open = c.open
            bucket_high = c.high
            bucket_low = c.low
            bucket_close = c.close
            bucket_vol = int(c.volume)
            bucket_session = c.session
        else:
            if c.high > bucket_high:
                bucket_high = c.high
            if c.low < bucket_low:
                bucket_low = c.low
            bucket_close = c.close
            bucket_vol += int(c.volume)
            # If any constituent is regular-session, the higher-TF bar
            # is regular-session too — pre/post stays only when the
            # whole bucket is pre/post.
            if bucket_session != "regular" and c.session == "regular":
                bucket_session = "regular"

    _flush()
    return out
