"""Unit tests for ``viewport.remap_window_by_time``.

This pure helper backs the ticker-switch view-preservation feature: when
the user is panned to a particular calendar window on AAPL and switches
to MSFT, the new chart should show the same calendar window in MSFT's
bar-index space. See ``ChartApp._render`` for the integration site.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tradinglab.core.viewport import remap_window_by_time


def _series(start: datetime, n: int, step_min: int = 5) -> list[datetime]:
    return [start + timedelta(minutes=i * step_min) for i in range(n)]


# ---------------------------------------------------------------------------
# Identity / same-symbol cases
# ---------------------------------------------------------------------------


def test_identical_series_preserves_window():
    """Same dates → mapped window equals the rounded source window."""
    dates = _series(datetime(2026, 5, 1, 9, 30), 100)
    result = remap_window_by_time(dates, (40.2, 60.7), dates)
    assert result == (40, 62)  # round(40.2)=40, round(60.7)=61, hi+1=62


def test_round_trip_close_to_int_bounds():
    dates = _series(datetime(2026, 5, 1, 9, 30), 50)
    # xlim of (10.0, 20.0) → lo_i=10, hi_i=20 → returns (10, 21).
    assert remap_window_by_time(dates, (10.0, 20.0), dates) == (10, 21)


# ---------------------------------------------------------------------------
# Cross-symbol mapping (different bar counts but overlapping calendar)
# ---------------------------------------------------------------------------


def test_new_series_starts_later_clamps_lo_to_zero():
    """If new symbol's first bar is AFTER the source window's start,
    snap lo to 0 so the user sees the available beginning."""
    base = datetime(2026, 5, 1, 9, 30)
    prev_dates = _series(base, 100)  # 100 bars from 9:30
    new_dates = _series(base + timedelta(hours=2), 100)  # starts 2h later
    # Source xlim covers 9:30-10:30 (indices 0-12).
    result = remap_window_by_time(prev_dates, (0.0, 12.0), new_dates)
    # All source dates are BEFORE new_dates[0], so rmap_lo stays at the
    # default snap (0) and rmap_hi also stays 0 — degenerate → None.
    assert result is None


def test_new_series_starts_later_partial_overlap():
    base = datetime(2026, 5, 1, 9, 30)
    prev_dates = _series(base, 100)
    # New series starts 30m after — overlap on the right side.
    new_dates = _series(base + timedelta(minutes=30), 100)
    # Source window: indices 0..20 → 9:30..11:10.
    result = remap_window_by_time(prev_dates, (0.0, 20.0), new_dates)
    assert result is not None
    lo, hi = result
    # lo snaps to 0 (source 9:30 < new[0]=10:00).
    # hi: greatest new index with new[i] ≤ 11:10 → 11:10 - 10:00 = 70min
    # / 5min = index 14, so hi = 15 (half-open).
    assert lo == 0
    assert hi == 15


def test_new_series_extends_past_source_end():
    base = datetime(2026, 5, 1, 9, 30)
    prev_dates = _series(base, 50)  # 50 bars
    new_dates = _series(base, 200)  # 200 bars — extends further forward
    # Source window: indices 10..40 (covers same time range).
    result = remap_window_by_time(prev_dates, (10.0, 40.0), new_dates)
    # Should map to the SAME indices in new_dates since the prefix
    # is identical.
    assert result == (10, 41)


def test_misaligned_grids_pick_nearest_le():
    """New series has a different bar grid (e.g. shifted by 2 minutes).
    Mapping should pick the greatest new index whose date ≤ source ts."""
    base = datetime(2026, 5, 1, 9, 30)
    prev_dates = _series(base, 50, step_min=5)
    # Same start, but 5m bars offset by +2 min (e.g., 9:32, 9:37, ...).
    new_dates = _series(base + timedelta(minutes=2), 50, step_min=5)
    # Source window: indices 10..20 → 10:20..11:10 source time.
    # In new_dates, 10:20 lies between new[9]=10:17 and new[10]=10:22,
    # so rmap_lo = 9 (greatest with date ≤ 10:20).
    # 11:10 lies between new[19]=11:07 and new[20]=11:12, so rmap_hi=19.
    result = remap_window_by_time(prev_dates, (10.0, 20.0), new_dates)
    assert result == (9, 20)


# ---------------------------------------------------------------------------
# Degenerate inputs
# ---------------------------------------------------------------------------


def test_empty_prev_returns_none():
    assert remap_window_by_time([], (0.0, 10.0), _series(datetime.now(), 5)) is None


def test_empty_new_returns_none():
    dates = _series(datetime.now(), 5)
    assert remap_window_by_time(dates, (0.0, 4.0), []) is None


def test_default_xlim_returns_none():
    dates = _series(datetime.now(), 5)
    # (0, 1) is the matplotlib axes default before any data is drawn —
    # remap should refuse to act on it.
    assert remap_window_by_time(dates, (0.0, 1.0), dates) is None


def test_inverted_xlim_returns_none():
    dates = _series(datetime.now(), 50)
    assert remap_window_by_time(dates, (30.0, 10.0), dates) is None


def test_zero_overlap_after_remap_returns_none():
    """Source window and new series share NO calendar overlap."""
    base = datetime(2026, 5, 1, 9, 30)
    prev_dates = _series(base, 50)
    # New series is one full year later.
    new_dates = _series(base + timedelta(days=365), 50)
    # Source xlim is in the prev range.
    result = remap_window_by_time(prev_dates, (10.0, 30.0), new_dates)
    # Source dates are all BEFORE new_dates[0] — rmap_lo and rmap_hi
    # both default to 0 → degenerate → None.
    assert result is None


def test_left_clamp_within_subwindow():
    """Negative left xlim clamps to 0 while staying a proper sub-window."""
    dates = _series(datetime(2026, 5, 1, 9, 30), 30)
    # hi_i=20 < 29 → still a sub-window (only the left edge is clamped).
    result = remap_window_by_time(dates, (-5.0, 20.0), dates)
    assert result == (0, 21)


def test_right_clamp_within_subwindow():
    """Right xlim past the end clamps to n-1 while staying a sub-window."""
    dates = _series(datetime(2026, 5, 1, 9, 30), 30)
    # lo_i=10 > 0 → still a sub-window (only the right edge is clamped).
    result = remap_window_by_time(dates, (10.0, 100.0), dates)
    assert result == (10, 30)


# ---------------------------------------------------------------------------
# Full-source-coverage intent guard (IPO / very-short-history)
# ---------------------------------------------------------------------------


def test_two_bar_ipo_source_does_not_crush_long_destination():
    """The motivating bug: a 2-bar IPO source must NOT carry its ~1-day
    window onto a long-history destination (which would show ~2 bars).

    A freshly-loaded 2-bar chart gets xlim (-0.5, 1.5) from default
    windowing; that spans the ENTIRE 2-bar source → no zoom to preserve →
    None → caller uses its default right-edge window.
    """
    today = datetime(2026, 6, 12)
    spcx = [today - timedelta(days=1), today]  # pre-IPO bar + first session
    amd = [today - timedelta(days=i) for i in range(360, -1, -1)]  # ~1y daily
    assert remap_window_by_time(spcx, (-0.5, 1.5), amd) is None


def test_full_coverage_identical_series_returns_none():
    """Viewing a symbol's ENTIRE history (even a long one) is not a zoom."""
    dates = _series(datetime(2026, 5, 1, 9, 30), 100)
    # xlim spanning all 100 bars (lo_i=0, hi_i=99) → full coverage → None.
    assert remap_window_by_time(dates, (-0.5, 99.5), dates) is None


def test_full_coverage_out_of_range_returns_none():
    """An xlim past BOTH edges clamps to full coverage → None (was the old
    ``test_xlim_clamped_when_out_of_range`` input; the contract changed)."""
    dates = _series(datetime(2026, 5, 1, 9, 30), 30)
    assert remap_window_by_time(dates, (-5.0, 100.0), dates) is None


def test_deliberate_narrow_subwindow_is_preserved():
    """A deliberate narrow zoom (proper sub-window) is still carried over —
    the intent guard must NOT over-correct it the way a result-floor would."""
    dates = _series(datetime(2026, 5, 1, 9, 30), 100)
    # 3-bar zoom in the middle: lo_i=50 > 0 and hi_i=52 < 99 → preserved.
    result = remap_window_by_time(dates, (50.0, 52.0), dates)
    assert result == (50, 53)


def test_left_edge_only_pan_is_preserved():
    """Viewing the START of a series (lo at bar 0 but hi well short of the
    end) is a deliberate pan, not full coverage → preserved."""
    dates = _series(datetime(2026, 5, 1, 9, 30), 100)
    result = remap_window_by_time(dates, (-0.5, 20.0), dates)
    assert result == (0, 21)


def test_right_edge_only_pan_is_preserved():
    """Viewing the END of a series (hi at the last bar but lo well past 0)
    is a deliberate pan, not full coverage → preserved."""
    dates = _series(datetime(2026, 5, 1, 9, 30), 100)
    result = remap_window_by_time(dates, (80.0, 99.5), dates)
    assert result == (80, 100)
