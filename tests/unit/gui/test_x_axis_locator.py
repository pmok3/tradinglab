"""`_AdaptiveXLocator` must space intraday ticks uniformly in BAR space.

The x-axis is bar-index space, but the market is shut overnight and the
session opens at 09:30 — not on an hour boundary. Ticking on wall-clock
bucket boundaries therefore covers unequal numbers of bars: on RTH 5m
data an hourly tick set gives gaps of ``{6, 12}`` bars within a day (the
09:30→10:00 stub is half an hour) and ``{30, 48}`` across an overnight
gap. The axis looks visibly ragged.

`check_d6_intraday_uniform_gaps` in the smoke suite asserts this
property, but it could never fail: the global smoke fetcher emits ONE
continuous 12.4-hour day, so the single irregular gap sits at the series
start and falls outside the tested windows (AGENTS.md §7.35 records the
blind spot). These tests supply the multi-session shapes the smoke
fixture cannot express.

Pure locator tests — no ChartApp, no Tk, no matplotlib axes. The locator
only needs ``app._panel_state[slot]["candles"]`` and an object exposing
``get_view_interval()``.
"""
from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

import pytest

from tradinglab.gui.x_axis_locator import _adaptive_x_locator_class

# Zoom fractions mirroring check_d6's sweep.
_FRACS = (0.05, 0.1, 0.2, 0.5, 1.0)
_TARGET = 12


def _sessions(n_days: int, bars_per_day: int, *,
              open_hm: tuple[int, int] = (9, 30),
              step_min: int = 5) -> list:
    """`n_days` sessions of `bars_per_day` bars, with overnight gaps."""
    out = []
    first = _dt.datetime(2026, 4, 20, *open_hm)
    for d in range(n_days):
        day = first + _dt.timedelta(days=d)
        for i in range(bars_per_day):
            out.append(SimpleNamespace(
                date=day + _dt.timedelta(minutes=step_min * i)))
    return out


class _Axis:
    def __init__(self, lim):
        self.lim = lim

    def get_view_interval(self):
        return self.lim


def _locator(candles, interval: str):
    app = SimpleNamespace(_panel_state={"primary": {"candles": candles}},
                          _display_tz=None)
    loc = _adaptive_x_locator_class()("primary", app, interval)
    loc.axis = _Axis((0, len(candles)))
    return loc


def _tick_sets(candles, interval: str):
    """Yield `(frac, ticks)` for each zoom level with enough ticks."""
    loc = _locator(candles, interval)
    n = len(candles)
    for frac in _FRACS:
        win = max(40, int(n * frac))
        loc.axis.lim = (n - win, n)
        ticks = sorted(int(round(v)) for v in loc())
        if len(ticks) >= 3:
            yield frac, ticks


# (id, candles-factory, interval)
_SHAPES = [
    ("rth_5m_5d", lambda: _sessions(5, 78), "5m"),
    ("rth_5m_30d", lambda: _sessions(30, 78), "5m"),
    ("rth_1m_3d", lambda: _sessions(3, 390, step_min=1), "1m"),
    ("rth_15m_20d", lambda: _sessions(20, 26, step_min=15), "15m"),
    ("rth_30m_40d", lambda: _sessions(40, 13, step_min=30), "30m"),
    # Extended hours: 04:00–20:00 is 192 bars at 5m.
    ("ext_5m_5d", lambda: _sessions(5, 192, open_hm=(4, 0)), "5m"),
    # Single continuous run — what the smoke fixture actually provides.
    ("continuous_150", lambda: _sessions(1, 150), "5m"),
    # Half-day: bars-per-day is no longer constant.
    ("halfday_mix", lambda: _sessions(3, 78) + _sessions(1, 39), "5m"),
]
_IDS = [s[0] for s in _SHAPES]


@pytest.mark.parametrize("_name,factory,interval", _SHAPES, ids=_IDS)
def test_intraday_tick_gaps_are_uniform(_name, factory, interval):
    """Every consecutive tick pair is the same number of bars apart.

    This is the property `check_d6` claims and the one a ragged axis
    violates. It must hold across overnight gaps, not just within a day.
    """
    candles = factory()
    for frac, ticks in _tick_sets(candles, interval):
        gaps = {ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)}
        assert len(gaps) == 1, (
            f"{_name} @frac={frac}: tick gaps not uniform: {sorted(gaps)} "
            f"(ticks={ticks})"
        )


@pytest.mark.parametrize("_name,factory,interval", _SHAPES, ids=_IDS)
def test_tick_count_respects_target(_name, factory, interval):
    """Density stays within the locator's budget.

    Uniformity is trivially satisfiable by ticking every bar, so pin the
    budget too — otherwise a stride of 1 would pass the test above and
    paint an unreadable axis.
    """
    candles = factory()
    for frac, ticks in _tick_sets(candles, interval):
        assert len(ticks) <= _TARGET, (
            f"{_name} @frac={frac}: {len(ticks)} ticks exceeds target "
            f"{_TARGET}")


def test_ticks_land_on_session_opens_when_stride_divides_the_day():
    """With equal-length sessions the stride divides bars-per-day, so
    ticks recur at the same time of day and every session's opening bar
    is tickable — that is what lets the formatter print `Apr 21` there.
    """
    candles = _sessions(5, 78)          # 78 bars/session at 5m
    loc = _locator(candles, "5m")
    n = len(candles)
    loc.axis.lim = (0, n)
    ticks = sorted(int(round(v)) for v in loc())
    assert len(ticks) >= 3
    step = ticks[1] - ticks[0]
    assert 78 % step == 0, (
        f"stride {step} does not divide bars-per-day (78); ticks would "
        "drift off the session grid")
    # Anchored on a session open ⇒ every tick is a whole number of
    # strides from bar 0, and session opens are multiples of 78.
    assert all(t % step == 0 for t in ticks), (
        f"ticks not anchored to the session grid: {ticks}")


def test_non_intraday_still_uses_calendar_buckets():
    """Daily data keeps the wall-clock bucket path.

    The bar-stride rule exists because intraday bar spacing and
    wall-clock spacing disagree across a session gap. Daily bars have no
    such gap, and calendar buckets give month/year-aligned ticks that a
    fixed stride cannot — so that path must be left alone.
    """
    daily = [SimpleNamespace(date=_dt.datetime(2024, 1, 1)
                             + _dt.timedelta(days=i))
             for i in range(400)]
    loc = _locator(daily, "1d")
    loc.axis.lim = (0, len(daily))
    ticks = sorted(int(round(v)) for v in loc())
    assert len(ticks) >= 3
    # Calendar-bucket ticks land on month starts, which are NOT evenly
    # spaced in days (28/30/31) — the signature of the bucket path.
    gaps = {ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)}
    assert len(gaps) > 1, (
        "daily ticks look evenly strided; the bar-stride path should not "
        f"apply to non-intraday intervals (gaps={sorted(gaps)})")


def test_empty_and_tiny_series_do_not_raise():
    for candles in ([], _sessions(1, 1), _sessions(1, 3)):
        loc = _locator(candles, "5m")
        loc.axis.lim = (0, max(1, len(candles)))
        loc()  # must not raise
