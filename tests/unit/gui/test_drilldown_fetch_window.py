"""Unit tests for the drill-down intraday fetch-window helper.

Pins the fix for the bug where drilling into a recent day (today, or a
day missing from a stale / partially-prefetched 5m cache) wrongly emitted
"5m data only available from … onward" instead of fetching the bars on
demand — even though a manual 5m toggle loads them fine.

``DrilldownMixin._day_within_intraday_fetch_window`` is a pure function
of ``day`` + the module-level ``INTERVAL_PERIODS`` table and
``date.today()`` (it reads no instance state), so it can be called with a
throwaway ``self``.
"""
from __future__ import annotations

from datetime import date, timedelta

from tradinglab.gui.drilldown import DrilldownMixin

_f = DrilldownMixin._day_within_intraday_fetch_window


def test_today_is_fetchable() -> None:
    assert _f(None, date.today(), "5m") is True


def test_recent_days_are_fetchable() -> None:
    # The reported failing days (today, a few days ago) are all within
    # yfinance's ~60-day 5m window.
    for n in (1, 2, 3, 5, 14, 30, 55):
        assert _f(None, date.today() - timedelta(days=n), "5m") is True, n


def test_within_buffered_window_is_fetchable() -> None:
    # 60-day window + 7-day generosity buffer = ~67 days.
    assert _f(None, date.today() - timedelta(days=60), "5m") is True
    assert _f(None, date.today() - timedelta(days=66), "5m") is True


def test_clearly_old_days_are_not_fetchable() -> None:
    for n in (80, 120, 400):
        assert _f(None, date.today() - timedelta(days=n), "5m") is False, n


def test_one_minute_window_is_seven_days() -> None:
    # 1m → "7d"; a day older than ~14 days is unreachable.
    assert _f(None, date.today() - timedelta(days=2), "1m") is True
    assert _f(None, date.today() - timedelta(days=30), "1m") is False


def test_hourly_window_is_two_years() -> None:
    # 1h → "730d"; a year-old day is still reachable.
    assert _f(None, date.today() - timedelta(days=365), "1h") is True


def test_daily_periods_are_unbounded() -> None:
    # 1d → "2y" (a year-spec): generous enough for any sane drill target;
    # 1mo → "max" → always fetchable.
    assert _f(None, date.today() - timedelta(days=500), "1d") is True
    assert _f(None, date.today() - timedelta(days=9999), "1mo") is True


def test_unknown_interval_defaults_to_sixty_days() -> None:
    assert _f(None, date.today() - timedelta(days=3), "bogus") is True
    assert _f(None, date.today() - timedelta(days=400), "bogus") is False


def test_non_date_returns_false() -> None:
    assert _f(None, "2026-06-08", "5m") is False
    assert _f(None, None, "5m") is False
