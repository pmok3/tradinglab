"""Unit tests for the volume time-of-day overlay math + rendering helpers.

Covers the pure-functional surface of
:mod:`tradinglab.gui.volume_tod_overlay`:

* :func:`compute_volume_tod_patches` math across the seven decision-
  branches in plan.md (pre-open suppression, sandbox-rewind, RTH
  cumulative, post-close latch, missing intraday, median tick, default
  off).
* :func:`patches_should_suppress_default_fill` index mapping.
* :func:`darker_shade` from :mod:`tradinglab.rendering`.

The matplotlib-touching :func:`draw_volume_tod_patches` is exercised by
the smoke layer (``check_b68``) where a real ``Axes`` instance is
available.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from tradinglab.gui.volume_tod_overlay import (
    VolumeTodPatch,
    compute_volume_tod_patches,
    patches_should_suppress_default_fill,
)
from tradinglab.models import Candle
from tradinglab.rendering import darker_shade

# ---------------------------------------------------------------------- helpers


def _utc(yr: int, mo: int, day: int, hh: int = 12, mm: int = 0) -> datetime:
    return datetime(yr, mo, day, hh, mm, tzinfo=timezone.utc)


def _make_daily_series(
    n: int, *, start_date: datetime, volume: float = 100_000,
) -> list:
    return [
        Candle(date=start_date + timedelta(days=i),
               open=100.0, high=101.0, low=99.0, close=100.5,
               volume=volume, session="regular")
        for i in range(n)
    ]


def _make_intraday_for_day_idx(
    base_utc: datetime, day_offset: int, *,
    total_volume: float = 100_000,
    bars_per_day: int = 78,
) -> list:
    """Build 5m bars covering RTH (390 minutes) for the given day index.

    base_utc is the day-0 anchor; we step day_offset days forward then
    place the first 5m bar at 09:30 ET = 14:30 UTC (winter EST = UTC-5).
    """
    out: list = []
    day_open_utc = base_utc + timedelta(days=day_offset)
    # Convert to 14:30 UTC == 09:30 ET in winter (EST).
    day_open_utc = day_open_utc.replace(hour=14, minute=30,
                                        second=0, microsecond=0)
    per_bar = total_volume / bars_per_day
    for i in range(bars_per_day):
        out.append(Candle(
            date=day_open_utc + timedelta(minutes=5 * i),
            open=100.0, high=100.5, low=99.5, close=100.1,
            volume=per_bar, session="regular",
        ))
    return out


# -------------------------------------------------------- math: cutoff scenarios


def test_compute_patches_realized_fraction_at_11_et():
    daily = _make_daily_series(1, start_date=_utc(2026, 1, 25))
    intraday = _make_intraday_for_day_idx(_utc(2026, 1, 25), 0)
    cutoff = _utc(2026, 1, 25, 16, 0)  # 11:00 ET = 16:00 UTC winter
    patches = compute_volume_tod_patches(
        daily, intraday,
        now_ms=int(cutoff.timestamp() * 1000),
        slice_start=0, slice_end=1,
    )
    assert len(patches) == 1
    p = patches[0]
    assert p.has_intraday is True
    # 09:30 → 11:00 = 90 mins = 18 of 78 bars (5m granularity).
    expected = 100_000 * 18 / 78
    assert math.isclose(p.filled_height, expected, abs_tol=1.0)
    assert math.isclose(p.outline_height, 100_000, abs_tol=1.0)
    assert math.isclose(p.full_day_volume, 100_000, abs_tol=1.0)


def test_compute_patches_post_close_latch_full():
    daily = _make_daily_series(1, start_date=_utc(2026, 1, 25))
    intraday = _make_intraday_for_day_idx(_utc(2026, 1, 25), 0)
    cutoff = _utc(2026, 1, 25, 22, 0)  # 17:00 ET
    patches = compute_volume_tod_patches(
        daily, intraday,
        now_ms=int(cutoff.timestamp() * 1000),
        slice_start=0, slice_end=1,
    )
    p = patches[0]
    assert p.has_intraday is True
    assert math.isclose(p.filled_height, p.outline_height, abs_tol=1.0)
    assert math.isclose(p.filled_height, 100_000, abs_tol=1.0)


def test_compute_patches_pre_open_wallclock_suppresses():
    daily = _make_daily_series(1, start_date=_utc(2026, 1, 25))
    intraday = _make_intraday_for_day_idx(_utc(2026, 1, 25), 0)
    cutoff = _utc(2026, 1, 25, 11, 0)  # 06:00 ET (pre-open)
    patches = compute_volume_tod_patches(
        daily, intraday,
        now_ms=int(cutoff.timestamp() * 1000),
        slice_start=0, slice_end=1,
        sandbox_active=False,
    )
    p = patches[0]
    assert p.has_intraday is False
    assert p.is_session_pre_open is True
    assert p.filled_height == 0.0
    assert p.outline_height == 0.0


def test_compute_patches_pre_open_sandbox_rewind_full_envelope():
    daily = _make_daily_series(1, start_date=_utc(2026, 1, 25))
    intraday = _make_intraday_for_day_idx(_utc(2026, 1, 25), 0)
    cutoff = _utc(2026, 1, 25, 11, 0)  # 06:00 ET (pre-open)
    patches = compute_volume_tod_patches(
        daily, intraday,
        now_ms=int(cutoff.timestamp() * 1000),
        slice_start=0, slice_end=1,
        sandbox_active=True,
    )
    p = patches[0]
    assert p.has_intraday is True, (
        "Sandbox-rewind pre-open keeps the overlay engaged (decision 12)")
    assert p.is_session_pre_open is True
    assert p.filled_height == 0.0
    assert math.isclose(p.outline_height, 100_000, abs_tol=1.0)


def test_compute_patches_missing_intraday_degrades():
    daily = _make_daily_series(3, start_date=_utc(2026, 1, 25))
    cutoff = _utc(2026, 1, 27, 16, 0)
    patches = compute_volume_tod_patches(
        daily, [],  # no intraday at all
        now_ms=int(cutoff.timestamp() * 1000),
        slice_start=0, slice_end=3,
    )
    assert len(patches) == 3
    for p in patches:
        assert p.has_intraday is False
        assert p.filled_height == 0.0
        assert p.outline_height == 0.0


def test_compute_patches_gap_bars_skipped():
    base = _utc(2026, 1, 25)
    daily = [
        Candle(date=base, open=100.0, high=101.0, low=99.0,
               close=100.5, volume=100_000, session="regular"),
        Candle.gap(base + timedelta(days=1)),
        Candle(date=base + timedelta(days=2), open=100.0, high=101.0,
               low=99.0, close=100.5, volume=100_000, session="regular"),
    ]
    intraday = (_make_intraday_for_day_idx(base, 0)
                + _make_intraday_for_day_idx(base, 2))
    cutoff = _utc(2026, 1, 27, 16, 0)
    patches = compute_volume_tod_patches(
        daily, intraday,
        now_ms=int(cutoff.timestamp() * 1000),
        slice_start=0, slice_end=3,
    )
    # Gap bar is skipped; expect 2 patches (one for day 0, one for day 2).
    assert len(patches) == 2
    assert all(p.has_intraday for p in patches)


# ----------------------------------------------------------- median-tick math


def test_compute_patches_median_tick_after_20_days():
    """20 prior days at 100k each → median = 100k on day 20."""
    daily = _make_daily_series(25, start_date=_utc(2026, 1, 5),
                               volume=100_000)
    intraday = _make_intraday_for_day_idx(_utc(2026, 1, 5), 20)
    # Cutoff on day 20 at 11:00 ET.
    cutoff = _utc(2026, 1, 25, 16, 0)
    patches = compute_volume_tod_patches(
        daily, intraday,
        now_ms=int(cutoff.timestamp() * 1000),
        slice_start=20, slice_end=21,
        median_lookback_days=20,
    )
    assert math.isclose(patches[0].median_height, 100_000, abs_tol=1.0)


def test_compute_patches_median_tick_zero_when_lookback_too_short():
    """Only 5 days of history → median stays 0 (lookback=20 > half=10).

    The math layer enforces a soft floor of ``lookback // 2`` valid
    entries before reporting a median to avoid noise at cold-start.
    """
    daily = _make_daily_series(6, start_date=_utc(2026, 1, 5),
                               volume=100_000)
    intraday = _make_intraday_for_day_idx(_utc(2026, 1, 5), 5)
    cutoff = _utc(2026, 1, 10, 16, 0)
    patches = compute_volume_tod_patches(
        daily, intraday,
        now_ms=int(cutoff.timestamp() * 1000),
        slice_start=5, slice_end=6,
        median_lookback_days=20,
    )
    assert patches[0].median_height == 0.0


def test_compute_patches_median_excludes_current_bar():
    """Median window is strictly < current idx (no look-ahead)."""
    # 20 days at 100k, then day 20 at 9_999_999 (huge outlier).
    daily = (
        _make_daily_series(20, start_date=_utc(2026, 1, 5),
                           volume=100_000)
        + [Candle(date=_utc(2026, 1, 25), open=100.0, high=101.0,
                  low=99.0, close=100.5,
                  volume=9_999_999, session="regular")]
    )
    intraday = _make_intraday_for_day_idx(_utc(2026, 1, 5), 20,
                                          total_volume=9_999_999)
    cutoff = _utc(2026, 1, 25, 16, 0)
    patches = compute_volume_tod_patches(
        daily, intraday,
        now_ms=int(cutoff.timestamp() * 1000),
        slice_start=20, slice_end=21,
        median_lookback_days=20,
    )
    # Median should be 100k (the past 20 days), NOT pulled by today's outlier.
    assert math.isclose(patches[0].median_height, 100_000, abs_tol=1.0)


# ------------------------------------------------------ RTH-only filter


def test_compute_patches_rth_only_filters_extended_hours():
    daily = _make_daily_series(1, start_date=_utc(2026, 1, 25))
    rth = _make_intraday_for_day_idx(_utc(2026, 1, 25), 0,
                                     total_volume=50_000)
    # Pre-market bar at 08:00 ET = 13:00 UTC with high volume — should be
    # filtered out when rth_only=True.
    pre = [Candle(
        date=_utc(2026, 1, 25, 13, 0),
        open=100.0, high=100.5, low=99.5, close=100.1,
        volume=999_999, session="pre",
    )]
    intraday = pre + rth
    cutoff = _utc(2026, 1, 25, 22, 0)  # 17:00 ET post-close
    patches = compute_volume_tod_patches(
        daily, intraday,
        now_ms=int(cutoff.timestamp() * 1000),
        slice_start=0, slice_end=1,
        rth_only=True,
    )
    p = patches[0]
    # Post-close latch → filled = full_day_volume (the daily candle's
    # own volume, NOT the intraday sum — the intraday total is only
    # used to derive the *fraction*).
    assert math.isclose(p.filled_height, 100_000, abs_tol=1.0)


# ------------------------------------------------- suppress-fill index mapping


def test_patches_should_suppress_default_fill_emits_correct_indices():
    patches = [
        VolumeTodPatch(bar_index=1, full_day_volume=100.0,
                       outline_height=100.0, filled_height=40.0,
                       has_intraday=True, is_session_pre_open=False,
                       base_color=(0.5, 0.5, 0.5, 1.0)),
        VolumeTodPatch(bar_index=2, full_day_volume=100.0,
                       outline_height=0.0, filled_height=0.0,
                       has_intraday=False, is_session_pre_open=False,
                       base_color=(0.5, 0.5, 0.5, 1.0)),
        VolumeTodPatch(bar_index=3, full_day_volume=100.0,
                       outline_height=100.0, filled_height=0.0,
                       has_intraday=True, is_session_pre_open=True,
                       base_color=(0.5, 0.5, 0.5, 1.0)),
    ]
    out = patches_should_suppress_default_fill(patches)
    assert out == {1: True, 3: True}, (
        "Index 1 (normal overlay) + index 3 (sandbox-rewind) suppress "
        "the default fill; index 2 (missing intraday) does NOT (degrades "
        "to the default bar)")


# ----------------------------------------------------- darker_shade behaviour


def test_darker_shade_drops_lightness_in_light_mode():
    rgba = (0.4, 0.7, 0.4, 1.0)  # mid-bright green
    out = darker_shade(rgba, dark_mode=False)
    # In light mode the algorithm clamps lightness DOWN by 0.18.
    # Hard to assert exact RGB without re-doing the HLS math; just
    # confirm the result is meaningfully darker by inspecting the
    # max channel.
    assert max(out[:3]) < max(rgba[:3]), (
        "Light-mode darker_shade should produce a strictly darker triple")
    assert out[3] == 1.0


def test_darker_shade_preserves_alpha():
    rgba = (0.3, 0.3, 0.8, 0.42)
    assert darker_shade(rgba, dark_mode=True)[3] == pytest.approx(0.42)
    assert darker_shade(rgba, dark_mode=False)[3] == pytest.approx(0.42)


def test_darker_shade_dark_mode_floor():
    rgba = (0.05, 0.05, 0.05, 1.0)  # already very dark
    out = darker_shade(rgba, dark_mode=True)
    # Dark-mode floor at L=0.10 — should bottom out, not go to pure black.
    assert max(out[:3]) >= 0.05, (
        f"Dark-mode floor at L=0.10 should keep channels above 0.05; got {out}")


# ---------------------------------------------------- empty / edge-case slices


def test_compute_patches_empty_slice():
    daily = _make_daily_series(5, start_date=_utc(2026, 1, 25))
    cutoff = _utc(2026, 1, 26, 16, 0)
    assert compute_volume_tod_patches(
        daily, [], now_ms=int(cutoff.timestamp() * 1000),
        slice_start=2, slice_end=2,
    ) == []


def test_compute_patches_slice_clamped_to_bounds():
    daily = _make_daily_series(3, start_date=_utc(2026, 1, 25))
    cutoff = _utc(2026, 1, 26, 16, 0)
    out = compute_volume_tod_patches(
        daily, [], now_ms=int(cutoff.timestamp() * 1000),
        slice_start=-5, slice_end=99,
    )
    # All 3 bars produced (no gaps); the slice is clamped to [0, 3).
    assert len(out) == 3
