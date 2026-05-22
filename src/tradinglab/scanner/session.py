"""Session-boundary helpers for the within-last-N-bars look-back walk.

Provides :func:`find_session_open_index` — given a bar series (any
interval) and an index, return the lowest index that shares the same
calendar (UTC) date as the bar at ``current_index``. The within-last
walk uses this to clamp its lower bound when any FieldRef in the
condition is daily-resetting (VWAP, HOD/LOD, time-of-day RVOL, ...) so
a 9:35 AM "VWAP reclaim within last 5 bars" doesn't peek at yesterday's
close.

Notes
-----
* Calendar dates are computed in UTC. US-equity RTH (9:30 AM – 4:00 PM
  ET) is wholly within a single UTC day for both standard and daylight
  saving time, so this is correct without TZ math. Same convention as
  the existing ``_today_mask`` helper in :mod:`scanner.fields`.

* For daily-and-above intervals, each bar's timestamp lands on its own
  unique UTC date — so this function naturally returns ``current_index``
  (no clamp), matching the spec: "daily-and-above intervals: no clamp".

* Out-of-range / empty inputs degrade gracefully:
    - ``current_index`` < 0 → returns ``current_index`` unchanged
      (caller will not walk into the negative range).
    - ``current_index`` ≥ len(bars) → returns ``current_index`` unchanged.

* This module is small enough to live caches-free; the
  ``IndicatorMemo``-style per-tick caches in :mod:`scanner.engine`
  already absorb the dominant compute. Adding a per-bar cache here
  would be premature.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from .fields import BarsNp


def find_session_open_index(bars: BarsNp, current_index: int) -> int:
    """Return the lowest index sharing the same UTC date as bar ``current_index``.

    For intraday bars: walks back through today's bars and returns the
    index of the session's first bar in the buffer.

    For daily-and-above bars: each bar lives on its own date, so the
    return value equals ``current_index`` (i.e. no clamping happens).

    Out-of-range inputs return ``current_index`` unchanged so the caller
    can pass the result straight into ``max(walk_low, returned_index)``
    without special-casing.
    """
    n = int(bars.timestamps.size)
    if n == 0 or current_index < 0 or current_index >= n:
        return current_index
    today = bars.timestamps[current_index].astype("datetime64[D]")
    # Walk backwards from current_index until the date changes.
    j = current_index
    while j > 0:
        prev_day = bars.timestamps[j - 1].astype("datetime64[D]")
        if prev_day != today:
            return j
        j -= 1
    return 0


__all__ = ["find_session_open_index"]
