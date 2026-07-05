"""Unit tests for the drill-down intraday fetch-window helper.

Pins the fix for the bug where drilling into a recent day (today, or a
day missing from a stale / partially-prefetched 5m cache) wrongly emitted
"5m data only available from … onward" instead of fetching the bars on
demand — even though a manual 5m toggle loads them fine.

``DrilldownMixin._day_within_intraday_fetch_window`` reads the reachable
window from ``constants.provider_lookback_days(source, interval)`` for the
active ``source_var`` (plus ``date.today()``). Called with ``self=None``
the ``source_var`` read raises and is caught → ``src=""`` → the yfinance
(``INTERVAL_PERIODS``) windows, which is what the default-path tests
below exercise. A ``_self(src)`` helper drives the deep-history vendors.
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from tradinglab.gui.drilldown import DrilldownMixin

_f = DrilldownMixin._day_within_intraday_fetch_window


def _self(src: str) -> SimpleNamespace:
    """A throwaway ``self`` whose ``source_var.get()`` returns ``src``."""
    return SimpleNamespace(source_var=SimpleNamespace(get=lambda: src))


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


# --- provider-aware windows (deep-history vendors) -------------------------


def test_deep_history_source_extends_5m_window() -> None:
    # Alpaca 5m reaches ~4 months (120d) back — well beyond yfinance's 60d,
    # but bounded so the up-front fetch stays ~1 page / ≲3s (< the 5s
    # drill-down deadline).
    assert _f(_self("alpaca"), date.today() - timedelta(days=60), "5m") is True
    assert _f(_self("alpaca"), date.today() - timedelta(days=110), "5m") is True
    # Beyond the ~120d + 7-day buffer → out of reach.
    assert _f(_self("alpaca"), date.today() - timedelta(days=200), "5m") is False


def test_deep_history_source_daily_reaches_years_back() -> None:
    # Alpaca daily window is ~15y; any sane drill target is reachable.
    assert _f(_self("alpaca"), date.today() - timedelta(days=2000), "1d") is True


def test_deep_history_source_one_minute_is_bounded() -> None:
    # 1m is bar-dense → bounded at 20d even for a deep-history vendor.
    assert _f(_self("alpaca"), date.today() - timedelta(days=10), "1m") is True
    assert _f(_self("alpaca"), date.today() - timedelta(days=60), "1m") is False


def test_yfinance_source_keeps_sixty_day_5m_window() -> None:
    # An explicit yfinance source is still capped at ~60d for 5m.
    assert _f(_self("yfinance"), date.today() - timedelta(days=30), "5m") is True
    assert _f(_self("yfinance"), date.today() - timedelta(days=120), "5m") is False
