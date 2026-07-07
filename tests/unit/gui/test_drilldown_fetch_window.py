"""Unit tests for the drill-down intraday fetch-window helper.

Pins the fix for the bug where drilling into a recent day (today, or a
day missing from a stale / partially-prefetched 5m cache) wrongly emitted
"5m data only available from … onward" instead of fetching the bars on
demand — even though a manual 5m toggle loads them fine.

``DrilldownMixin._day_within_intraday_fetch_window`` has two regimes:

* **Range-capable providers** (Alpaca) fetch any historical day on demand,
  so reachability is gated on the learned coverage ``data_start`` watermark
  (unknown → always reachable), not a trailing window.
* **Trailing-window providers** (yfinance) read the reachable window from
  ``constants.provider_lookback_days(source, interval)`` (plus
  ``date.today()``). Called with ``self=None`` the ``source_var`` read
  raises and is caught → ``src=""`` → the yfinance (``INTERVAL_PERIODS``)
  windows, which is what the default-path tests below exercise. A
  ``_self(src)`` helper drives the named vendors.
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from tradinglab.gui.drilldown import DrilldownMixin, _day_to_ts

_f = DrilldownMixin._day_within_intraday_fetch_window


@pytest.fixture(autouse=True)
def _ensure_alpaca_range_capable():
    """Guarantee the range-capable regime is exercised regardless of creds.

    ``data/__init__`` registers ``alpaca`` with ``supports_range=True``
    ONLY when ``AlpacaCredentials.is_configured()`` — true on a dev box
    that has Alpaca keys, but FALSE in CI (fresh checkout, no creds). The
    range-capable tests below hardcode ``"alpaca"`` as their exemplar
    range-capable provider, so without this fixture they silently fall
    into the trailing-window regime on CI and mis-assert (a day beyond the
    ~60-day yfinance window reads unreachable). Force ``alpaca`` into
    ``_RANGE_CAPABLE`` for the test and restore the prior state afterwards
    so a real credentialed registration is never clobbered.
    """
    from tradinglab.data import base
    had = "alpaca" in base._RANGE_CAPABLE
    base._RANGE_CAPABLE.add("alpaca")
    try:
        yield
    finally:
        if not had:
            base._RANGE_CAPABLE.discard("alpaca")


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


# --- provider-aware windows -------------------------------------------------
#
# Range-capable providers (Alpaca — ``source_supports_range``) fetch any
# historical day on demand via a targeted page-span window, so the reachable
# set is no longer a trailing window. It is "any day at or after the learned
# provider data-start". With no coverage sidecar yet (data_start unknown),
# EVERY day is reachable; once a fetch has learned the real data-start,
# days older than it (minus a 7-day buffer) become unreachable.


def test_range_capable_source_reaches_any_day_without_coverage() -> None:
    # Alpaca (range-capable) with no learned data-start → any day is
    # fetchable on demand, including ones far beyond yfinance's 60d cap.
    for n in (60, 110, 200, 2000):
        assert _f(_self("alpaca"), date.today() - timedelta(days=n), "5m") is True, n


def test_deep_history_source_daily_reaches_years_back() -> None:
    # Alpaca daily: any sane drill target is reachable.
    assert _f(_self("alpaca"), date.today() - timedelta(days=2000), "1d") is True


def test_range_capable_one_minute_also_on_demand() -> None:
    # 1m is bar-dense but still on-demand for a range-capable vendor — no
    # trailing-window cap applies (the targeted fetch pulls just the day's
    # page rather than the whole trailing history).
    for n in (10, 60, 400):
        assert _f(_self("alpaca"), date.today() - timedelta(days=n), "1m") is True, n


def test_range_capable_source_gates_on_learned_data_start(monkeypatch) -> None:
    # Once coverage has learned the provider's data-start, a day well after
    # it is reachable but a day well before it (past the 7-day buffer) is not.
    from tradinglab.data import coverage as _cov
    from tradinglab.gui import drilldown as _dd

    ds_ts = _day_to_ts(date.today() - timedelta(days=100))
    rec = _cov.CoverageRecord(data_start_ts=ds_ts)
    monkeypatch.setattr(_dd.coverage, "load", lambda *a, **k: rec)
    slf = SimpleNamespace(
        source_var=SimpleNamespace(get=lambda: "alpaca"),
        ticker_var=SimpleNamespace(get=lambda: "AAPL"),
    )
    # 50 days ago → after data_start → reachable.
    assert _f(slf, date.today() - timedelta(days=50), "5m") is True
    # 200 days ago → before data_start − 7d buffer → unreachable.
    assert _f(slf, date.today() - timedelta(days=200), "5m") is False


def test_yfinance_source_keeps_sixty_day_5m_window() -> None:
    # An explicit yfinance source is still capped at ~60d for 5m.
    assert _f(_self("yfinance"), date.today() - timedelta(days=30), "5m") is True
    assert _f(_self("yfinance"), date.today() - timedelta(days=120), "5m") is False
