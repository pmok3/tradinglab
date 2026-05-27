"""Pin the vectorized ET conversion contract used by the evaluator hot path.

``_compute_et_arrays(timestamps)`` replaces the per-bar
``datetime.fromtimestamp(ts, _ET)`` + ``_is_regular_session`` calls in
the main mechanical-backtest loop with a single numpy pass that samples
the zoneinfo table once per UTC day and broadcasts. These tests assert
the numpy output matches the slow per-bar reference computation
bit-for-bit across DST transitions, RTH boundaries, weekends, and the
empty-input edge case.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from tradinglab.strategy_tester.evaluator import (
    _ET,
    _bar_ts_to_et,
    _compute_et_arrays,
    _is_regular_session,
)

pytestmark = pytest.mark.skipif(_ET is None, reason="zoneinfo / tzdata unavailable")


def _reference(ts_arr: np.ndarray) -> tuple[list[int], list[bool]]:
    """Slow per-bar reference: what the pre-vectorization code did."""
    dates: list[int] = []
    rth: list[bool] = []
    for ts in ts_arr.tolist():
        et = _bar_ts_to_et(int(ts))
        # Days-since-1970 in ET = (date - epoch).days.
        dates.append((et.date() - datetime(1970, 1, 1).date()).days)
        rth.append(_is_regular_session(et))
    return dates, rth


def _utc_ts(year: int, month: int, day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp())


def test_empty_input_returns_empty_arrays() -> None:
    ts = np.empty(0, dtype=np.int64)
    et_date_ints, rth_mask, et_offsets = _compute_et_arrays(ts)
    assert et_date_ints.shape == (0,)
    assert rth_mask.shape == (0,)
    assert et_offsets.shape == (0,)
    assert et_date_ints.dtype == np.int64
    assert rth_mask.dtype == bool
    assert et_offsets.dtype == np.int64


def test_dst_spring_forward_transition() -> None:
    """2024-03-10 02:00 EST → 03:00 EDT. Offset flips from -5h to -4h.

    Probe 12:00 UTC on March 9, 10, 11 — each falls on the correct side
    of the offset switch (March 9: still EST/-5h, March 10 noon UTC is
    07:00 EST (offset already EDT by 03:00 ET so 12:00 UTC = 08:00 EDT),
    March 11: full EDT/-4h).
    """
    ts = np.array([
        _utc_ts(2024, 3, 8, 14, 30),   # Fri 09:30 ET — RTH open, EST
        _utc_ts(2024, 3, 9, 14, 30),   # Sat — weekend
        _utc_ts(2024, 3, 10, 14, 30),  # Sun — weekend
        _utc_ts(2024, 3, 11, 13, 30),  # Mon 09:30 EDT — RTH open, post-DST
        _utc_ts(2024, 3, 11, 20, 0),   # Mon 16:00 EDT — RTH close
        _utc_ts(2024, 3, 11, 20, 1),   # Mon 16:01 EDT — postmarket
    ], dtype=np.int64)
    et_date_ints, rth_mask, et_offsets = _compute_et_arrays(ts)
    ref_dates, ref_rth = _reference(ts)
    assert et_date_ints.tolist() == ref_dates
    assert rth_mask.tolist() == ref_rth
    # Specific offset checks: March 8 = EST = -18000s, March 11 = EDT = -14400s.
    assert int(et_offsets[0]) == -5 * 3600
    assert int(et_offsets[3]) == -4 * 3600


def test_dst_fall_back_transition() -> None:
    """2024-11-03 02:00 EDT → 01:00 EST. Offset flips from -4h to -5h."""
    ts = np.array([
        _utc_ts(2024, 11, 1, 13, 30),  # Fri 09:30 EDT
        _utc_ts(2024, 11, 1, 20, 0),   # Fri 16:00 EDT
        _utc_ts(2024, 11, 4, 14, 30),  # Mon 09:30 EST (post-DST)
        _utc_ts(2024, 11, 4, 21, 0),   # Mon 16:00 EST
        _utc_ts(2024, 11, 4, 21, 1),   # Mon 16:01 EST — postmarket
    ], dtype=np.int64)
    et_date_ints, rth_mask, et_offsets = _compute_et_arrays(ts)
    ref_dates, ref_rth = _reference(ts)
    assert et_date_ints.tolist() == ref_dates
    assert rth_mask.tolist() == ref_rth
    assert int(et_offsets[0]) == -4 * 3600
    assert int(et_offsets[2]) == -5 * 3600


def test_rth_boundaries_inclusive_both_ends() -> None:
    """09:30:00 ET first inclusive, 16:00:00 ET last inclusive — exactly
    matches ``_is_regular_session``."""
    # Monday 2024-06-03 EDT (offset -4h, so ET HH:MM = UTC HH:MM - 4h).
    ts = np.array([
        _utc_ts(2024, 6, 3, 13, 29),   # 09:29 ET — pre-open
        _utc_ts(2024, 6, 3, 13, 30),   # 09:30 ET — open (inclusive)
        _utc_ts(2024, 6, 3, 13, 30) + 1,  # 09:30:01 ET
        _utc_ts(2024, 6, 3, 19, 59),   # 15:59 ET
        _utc_ts(2024, 6, 3, 20, 0),    # 16:00 ET — close (inclusive)
        _utc_ts(2024, 6, 3, 20, 0) + 1,  # 16:00:01 ET — past close
    ], dtype=np.int64)
    _, rth_mask, _ = _compute_et_arrays(ts)
    assert rth_mask.tolist() == [False, True, True, True, True, False]


def test_weekends_all_false() -> None:
    # 2024-06-01 = Saturday, 2024-06-02 = Sunday. Bars at noon ET each day.
    ts = np.array([
        _utc_ts(2024, 6, 1, 16, 0),   # Sat 12:00 ET
        _utc_ts(2024, 6, 1, 18, 0),   # Sat 14:00 ET
        _utc_ts(2024, 6, 2, 16, 0),   # Sun 12:00 ET
        _utc_ts(2024, 6, 3, 16, 0),   # Mon 12:00 ET — RTH (control)
    ], dtype=np.int64)
    _, rth_mask, _ = _compute_et_arrays(ts)
    assert rth_mask.tolist() == [False, False, False, True]


def test_year_long_5min_against_reference() -> None:
    """End-to-end: a dense year of 5-min UTC bars (spanning both DST
    transitions) matches the reference function bit-for-bit."""
    start = _utc_ts(2024, 1, 1, 0, 0)
    end = _utc_ts(2025, 1, 1, 0, 0)
    # 5-minute = 300s. ~105k bars across the year. Sample every 17 bars
    # (~6k bars) to keep the test fast while still covering both DST
    # transitions densely.
    ts = np.arange(start, end, 300 * 17, dtype=np.int64)
    et_date_ints, rth_mask, _ = _compute_et_arrays(ts)
    ref_dates, ref_rth = _reference(ts)
    assert et_date_ints.tolist() == ref_dates
    assert rth_mask.tolist() == ref_rth


def test_et_date_int_increments_at_et_midnight_not_utc() -> None:
    """The whole point of using ET-days (not UTC-days) for session-roll
    detection: 23:00 ET (= 03:00/04:00 UTC next day) is still the same
    ET trading day as 09:30 ET that morning."""
    # Monday 2024-06-03, EDT (-4h).
    morning = _utc_ts(2024, 6, 3, 13, 30)   # 09:30 ET Mon
    evening = _utc_ts(2024, 6, 4, 2, 30)    # 22:30 ET Mon (= 02:30 UTC Tue)
    next_morning = _utc_ts(2024, 6, 4, 13, 30)  # 09:30 ET Tue
    ts = np.array([morning, evening, next_morning], dtype=np.int64)
    et_date_ints, _, _ = _compute_et_arrays(ts)
    assert et_date_ints[0] == et_date_ints[1]  # same ET trading day
    assert et_date_ints[2] == et_date_ints[0] + 1  # next ET day
