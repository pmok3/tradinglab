"""Unit tests for ``data.prefetch.planner`` — per-source band planning.

Contract (design §4, Decision 8): "band = one maximal API request." A
``WindowPlanner`` maps ``(symbol, interval, band_index)`` to a ``FetchWindow``
request descriptor, newest-first, exhausting the provider.

* **Period providers (yfinance):** no pagination — band 0 is the interval's max
  trailing period; there is no deeper intraday band (``band>=1 -> None``).
* **Range providers (Alpaca):** band 0 fetches the most recent max page; band k
  steps back via ``end = oldest_ts`` reached so far. Deepening continues until
  the scheduler sees the fetch return no older bars (not signalled by the
  planner).
"""
from __future__ import annotations

import pytest

from tradinglab.data.prefetch.planner import (
    ALPACA_MAX_PAGE,
    FetchWindow,
    PeriodWindowPlanner,
    RangeWindowPlanner,
    planner_for,
)


# ---------------------------------------------------------- PeriodWindowPlanner
def test_period_planner_band0_max_period_per_interval():
    p = PeriodWindowPlanner()
    assert p.band("AMD", "1m", 0).period == "7d"
    assert p.band("AMD", "5m", 0).period == "60d"
    assert p.band("AMD", "15m", 0).period == "60d"
    assert p.band("AMD", "1h", 0).period == "730d"
    assert p.band("AMD", "1d", 0).period == "max"
    assert p.band("AMD", "1wk", 0).period == "max"


def test_period_planner_unknown_interval_defaults_to_max():
    assert PeriodWindowPlanner().band("AMD", "3d", 0).period == "max"


def test_period_planner_band0_kind_and_interval():
    w = PeriodWindowPlanner().band("AMD", "5m", 0)
    assert w.kind == "period" and w.interval == "5m"
    assert w.start is None and w.end is None and w.limit is None


def test_period_planner_has_no_deeper_bands():
    p = PeriodWindowPlanner()
    for iv in ("1m", "5m", "1d"):
        assert p.band("AMD", iv, 1) is None
        assert p.band("AMD", iv, 2) is None


# ----------------------------------------------------------- RangeWindowPlanner
def test_range_planner_band0_recent_max_page():
    w = RangeWindowPlanner().band("AMD", "5m", 0)
    assert w.kind == "range"
    assert w.end is None            # None => latest
    assert w.limit == ALPACA_MAX_PAGE
    assert w.interval == "5m"


def test_range_planner_deeper_band_steps_back_from_oldest():
    p = RangeWindowPlanner()
    w = p.band("AMD", "5m", 1, oldest_ts=1_600_000_000.0)
    assert w.kind == "range"
    assert w.end == 1_600_000_000.0
    assert w.limit == ALPACA_MAX_PAGE


def test_range_planner_deeper_band_without_boundary_is_none():
    # Can't compute band>=1 without knowing where band k-1 ended.
    assert RangeWindowPlanner().band("AMD", "5m", 3, oldest_ts=None) is None


def test_range_planner_custom_page_size():
    p = RangeWindowPlanner(max_page=1000)
    assert p.band("AMD", "1d", 0).limit == 1000


# --------------------------------------------------------------- planner_for
def test_planner_for_range_capable():
    assert isinstance(planner_for(supports_range=True), RangeWindowPlanner)


def test_planner_for_period():
    assert isinstance(planner_for(supports_range=False), PeriodWindowPlanner)


# --------------------------------------------------------------- FetchWindow
def test_fetch_window_is_frozen():
    w = FetchWindow(interval="5m", kind="period", period="60d")
    with pytest.raises((AttributeError, TypeError)):
        w.period = "max"  # type: ignore[misc]


def test_negative_band_index_rejected():
    # Foreground (band -1) is handled by the scheduler with an explicit window,
    # not via the deep-band planner.
    assert PeriodWindowPlanner().band("AMD", "5m", -1) is None
    assert RangeWindowPlanner().band("AMD", "5m", -1) is None
