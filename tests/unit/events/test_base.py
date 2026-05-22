"""Unit tests for :mod:`tradinglab.events.base` record types.

Locks in:
* :class:`EarningsRecord` defaults (NaN actuals + estimates, empty when)
* :class:`EarningsRecord.is_future` predicate (NaN actual → True)
* :class:`EarningsRecord.surprise_pct` math (signed pct, NaN-on-zero)
* :class:`DividendRecord.is_cash_event` / ``is_split`` discriminators
* :class:`EventBundle` post-init sort invariant on both axes
* :data:`EVENT_SOURCES` registry shape + :func:`register_event_source`
  idempotent overwrite

These are tiny but valuable — they're the data contract every other
module assumes.
"""
from __future__ import annotations

import math

import pytest

from tradinglab.events.base import (
    EVENT_SOURCES,
    DividendRecord,
    EarningsRecord,
    EventBundle,
    register_event_source,
)


# ---------------------------------------------------------------------------
# EarningsRecord
# ---------------------------------------------------------------------------

def test_earnings_record_defaults_are_nan():
    r = EarningsRecord(ts=1000, symbol="AAPL", when="AMC")
    assert math.isnan(r.eps_estimate)
    assert math.isnan(r.eps_actual)
    assert math.isnan(r.revenue_estimate)
    assert math.isnan(r.revenue_actual)
    assert r.source == ""


def test_earnings_is_future_when_actual_is_nan():
    r = EarningsRecord(ts=1, symbol="AAPL", when="AMC", eps_estimate=1.0)
    assert r.is_future is True


def test_earnings_is_not_future_when_actual_finite():
    r = EarningsRecord(ts=1, symbol="AAPL", when="AMC",
                       eps_estimate=1.0, eps_actual=1.05)
    assert r.is_future is False


def test_surprise_pct_positive_beat():
    r = EarningsRecord(ts=1, symbol="X", when="AMC",
                       eps_estimate=1.00, eps_actual=1.10)
    assert r.surprise_pct == pytest.approx(10.0)


def test_surprise_pct_negative_miss():
    r = EarningsRecord(ts=1, symbol="X", when="AMC",
                       eps_estimate=2.00, eps_actual=1.50)
    assert r.surprise_pct == pytest.approx(-25.0)


def test_surprise_pct_signed_on_negative_estimate():
    # An estimate of -0.50 and an actual of -0.40 is a *beat* (less
    # negative), so the percentage is +20%.
    r = EarningsRecord(ts=1, symbol="X", when="AMC",
                       eps_estimate=-0.50, eps_actual=-0.40)
    assert r.surprise_pct == pytest.approx(20.0)


def test_surprise_pct_nan_when_estimate_zero():
    r = EarningsRecord(ts=1, symbol="X", when="AMC",
                       eps_estimate=0.0, eps_actual=0.05)
    assert math.isnan(r.surprise_pct)


def test_surprise_pct_nan_when_either_side_nan():
    r = EarningsRecord(ts=1, symbol="X", when="AMC",
                       eps_estimate=math.nan, eps_actual=1.0)
    assert math.isnan(r.surprise_pct)
    r2 = EarningsRecord(ts=1, symbol="X", when="AMC",
                        eps_estimate=1.0)  # NaN actual default
    assert math.isnan(r2.surprise_pct)


# ---------------------------------------------------------------------------
# DividendRecord
# ---------------------------------------------------------------------------

def test_dividend_cash_is_cash_event():
    d = DividendRecord(ex_ts=1, symbol="X", amount=0.25, kind="cash")
    assert d.is_cash_event is True
    assert d.is_split is False


def test_dividend_special_is_cash_event():
    d = DividendRecord(ex_ts=1, symbol="X", amount=1.50, kind="special")
    assert d.is_cash_event is True
    assert d.is_split is False


def test_dividend_split_classification():
    d = DividendRecord(ex_ts=1, symbol="X", amount=math.nan,
                       kind="stock_split", ratio_num=2, ratio_den=1)
    assert d.is_split is True
    assert d.is_cash_event is False


def test_dividend_spinoff_is_cash_event_per_q10():
    # User decision Q10: spin-offs collapse to a cash credit at ex-date.
    d = DividendRecord(ex_ts=1, symbol="X", amount=5.00, kind="spinoff")
    assert d.is_cash_event is True
    assert d.is_split is False


# ---------------------------------------------------------------------------
# EventBundle.__post_init__ sort invariant
# ---------------------------------------------------------------------------

def test_event_bundle_sorts_earnings_by_ts():
    b = EventBundle(
        symbol="X",
        earnings=[
            EarningsRecord(ts=300, symbol="X", when="AMC"),
            EarningsRecord(ts=100, symbol="X", when="BMO"),
            EarningsRecord(ts=200, symbol="X", when="AMC"),
        ],
    )
    assert [r.ts for r in b.earnings] == [100, 200, 300]


def test_event_bundle_sorts_dividends_by_ex_ts():
    b = EventBundle(
        symbol="X",
        dividends=[
            DividendRecord(ex_ts=500, symbol="X", amount=0.10),
            DividendRecord(ex_ts=100, symbol="X", amount=0.10),
            DividendRecord(ex_ts=250, symbol="X", amount=0.10),
        ],
    )
    assert [d.ex_ts for d in b.dividends] == [100, 250, 500]


def test_event_bundle_empty_lists_ok():
    b = EventBundle(symbol="X")
    assert b.earnings == []
    assert b.dividends == []
    assert b.fetched_at == 0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_event_sources_contains_yfinance_and_synthetic():
    # These are registered at package import time.
    assert "synthetic" in EVENT_SOURCES
    assert "yfinance" in EVENT_SOURCES


def test_register_event_source_is_idempotent():
    sentinel = lambda t: None  # noqa: E731
    original = EVENT_SOURCES.get("__test_stub__")
    try:
        register_event_source("__test_stub__", sentinel)
        assert EVENT_SOURCES["__test_stub__"] is sentinel
        # Overwrite with a new value — should not raise.
        sentinel2 = lambda t: None  # noqa: E731
        register_event_source("__test_stub__", sentinel2)
        assert EVENT_SOURCES["__test_stub__"] is sentinel2
    finally:
        # Don't pollute other tests.
        if original is None:
            EVENT_SOURCES.pop("__test_stub__", None)
        else:
            EVENT_SOURCES["__test_stub__"] = original
