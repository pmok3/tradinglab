"""Provider orchestration tests; parsing variants live in test_normalize.py.

The fake SDK is installed in sys.modules even when yfinance is installed,
so constructor arguments, independent property failures and warning suppression
are exercised without network access.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import sys
import types
from unittest.mock import Mock

import pandas as pd
import pytest

from tradinglab.events import yfinance_events
from tradinglab.events.base import DividendRecord, EventBundle

_EPOCH_UTC = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


class _FakeTicker:
    def __init__(self):
        self.earnings_df = None
        self.actions_df = None
        self.earnings_exc = None
        self.actions_exc = None

    @property
    def earnings_dates(self):
        if self.earnings_exc is not None:
            raise self.earnings_exc
        return self.earnings_df

    @property
    def actions(self):
        if self.actions_exc is not None:
            raise self.actions_exc
        return self.actions_df


@pytest.fixture
def fake_yfinance(monkeypatch):
    stub = types.ModuleType("yfinance")
    stub.Ticker = Mock(return_value=_FakeTicker())
    monkeypatch.setitem(sys.modules, "yfinance", stub)
    monkeypatch.setattr(yfinance_events, "_logged_earnings_dates_failure", set())
    return stub


def _earnings_frame():
    return pd.DataFrame({
        "EPS Estimate": [1.50, 2.10],
        "Reported EPS": [1.45, 2.18],
    }, index=pd.to_datetime(["2024-07-25 20:05Z", "2024-01-25 21:05Z"]))


def _actions_frame():
    return pd.DataFrame({
        "Dividends": [0.0, 0.75],
        "Stock Splits": [4.0, 0.0],
    }, index=pd.to_datetime(["2024-06-07 20:00Z", "2024-02-14 21:00Z"]))


def _midnight_ms(year, month, day):
    return int((dt.datetime(year, month, day, tzinfo=dt.timezone.utc)
                - _EPOCH_UTC).total_seconds() * 1000)


def test_normalized_symbol_and_complete_ordered_bundle(fake_yfinance):
    ticker = fake_yfinance.Ticker.return_value
    ticker.earnings_df = _earnings_frame()
    ticker.actions_df = _actions_frame()

    bundle = yfinance_events.fetch_yfinance_events("  aapl  ")

    fake_yfinance.Ticker.assert_called_once_with("AAPL")
    assert isinstance(bundle, EventBundle)
    assert bundle.symbol == "AAPL"
    assert [(r.ts, r.symbol, r.when, r.eps_estimate, r.eps_actual, r.source)
            for r in bundle.earnings] == [
        (_midnight_ms(2024, 1, 25), "AAPL", "AMC", 2.10, 2.18, "yfinance"),
        (_midnight_ms(2024, 7, 25), "AAPL", "AMC", 1.50, 1.45, "yfinance"),
    ]
    assert len(bundle.dividends) == 2
    cash, split = bundle.dividends
    assert cash == DividendRecord(
        ex_ts=_midnight_ms(2024, 2, 14),
        symbol="AAPL", amount=0.75, kind="cash", source="yfinance",
    )
    assert (split.ex_ts, split.symbol, split.kind,
            split.ratio_num, split.ratio_den, split.source) == (
        _midnight_ms(2024, 6, 7), "AAPL", "stock_split", 4, 1, "yfinance",
    )
    assert math.isnan(split.amount)


@pytest.mark.parametrize("empty_frame", [None, pd.DataFrame()])
def test_empty_frames_return_none(fake_yfinance, empty_frame):
    ticker = fake_yfinance.Ticker.return_value
    ticker.earnings_df = ticker.actions_df = empty_frame
    assert yfinance_events.fetch_yfinance_events("AAPL") is None


@pytest.mark.parametrize("missing_axis", ["earnings", "actions"])
@pytest.mark.parametrize("empty_frame", [None, pd.DataFrame()])
def test_partial_empty_response_preserves_other_axis(fake_yfinance, missing_axis, empty_frame):
    ticker = fake_yfinance.Ticker.return_value
    ticker.earnings_df = _earnings_frame()
    ticker.actions_df = _actions_frame()
    setattr(ticker, f"{missing_axis}_df", empty_frame)

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    assert isinstance(bundle, EventBundle)
    assert len(bundle.earnings) == (0 if missing_axis == "earnings" else 2)
    assert len(bundle.dividends) == (0 if missing_axis == "actions" else 2)


def test_missing_yfinance_import_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", None)
    assert yfinance_events.fetch_yfinance_events("AAPL") is None


@pytest.mark.parametrize("symbol", ["", "   ", None])
def test_blank_symbol_does_not_construct_ticker(fake_yfinance, symbol):
    assert yfinance_events.fetch_yfinance_events(symbol) is None
    fake_yfinance.Ticker.assert_not_called()


def test_ticker_constructor_failure_returns_none(fake_yfinance):
    fake_yfinance.Ticker.side_effect = ConnectionError("provider unreachable")
    assert yfinance_events.fetch_yfinance_events("AAPL") is None


@pytest.mark.parametrize("failed_axis", ["earnings", "actions"])
def test_property_failure_preserves_other_axis(fake_yfinance, failed_axis):
    ticker = fake_yfinance.Ticker.return_value
    ticker.earnings_df = _earnings_frame()
    ticker.actions_df = _actions_frame()
    setattr(ticker, f"{failed_axis}_exc", ConnectionError("provider unreachable"))

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    assert isinstance(bundle, EventBundle)
    assert bundle.symbol == "AAPL"
    if failed_axis == "actions":
        assert [(r.ts, r.eps_actual) for r in bundle.earnings] == [
            (_midnight_ms(2024, 1, 25), 2.18),
            (_midnight_ms(2024, 7, 25), 1.45),
        ]
        assert bundle.dividends == []
    else:
        assert bundle.earnings == []
        assert len(bundle.dividends) == 2
        cash, split = bundle.dividends
        assert (cash.ex_ts, cash.kind, cash.amount) == (
            _midnight_ms(2024, 2, 14), "cash", 0.75,
        )
        assert (split.ex_ts, split.kind, split.ratio_num, split.ratio_den) == (
            _midnight_ms(2024, 6, 7), "stock_split", 4, 1,
        )


def test_both_properties_fail_returns_none(fake_yfinance):
    ticker = fake_yfinance.Ticker.return_value
    ticker.earnings_exc = ConnectionError("earnings unavailable")
    ticker.actions_exc = ConnectionError("actions unavailable")
    assert yfinance_events.fetch_yfinance_events("AAPL") is None


@pytest.mark.parametrize("second_exception", [ImportError, RuntimeError])
def test_earnings_warning_once_per_exception_kind(fake_yfinance, caplog, second_exception):
    ticker = fake_yfinance.Ticker.return_value
    with caplog.at_level(logging.WARNING, logger=yfinance_events.__name__):
        for exc in [ImportError("Missing optional dependency 'lxml'"),
                    second_exception("provider error")]:
            ticker.earnings_exc = exc
            assert yfinance_events.fetch_yfinance_events("AAPL") is None

    warnings = [r for r in caplog.records
                if r.name == yfinance_events.__name__ and r.levelno == logging.WARNING]
    kinds = {"ImportError", second_exception.__name__}
    assert len(warnings) == len(kinds)
    assert yfinance_events._logged_earnings_dates_failure == kinds
    assert "earnings_dates" in warnings[0].getMessage()
    assert "lxml" in warnings[0].getMessage()
    if second_exception is RuntimeError:
        assert "RuntimeError" in warnings[1].getMessage()


def test_calls_fetch_again_and_stamp_current_utc_ms(fake_yfinance):
    first_ticker, second_ticker = _FakeTicker(), _FakeTicker()
    first_ticker.earnings_df = _earnings_frame()
    second_ticker.actions_df = _actions_frame()
    fake_yfinance.Ticker.side_effect = [first_ticker, second_ticker]
    before_ms = int((dt.datetime.now(tz=dt.timezone.utc) - _EPOCH_UTC).total_seconds() * 1000)

    first = yfinance_events.fetch_yfinance_events("AAPL")
    second = yfinance_events.fetch_yfinance_events("AAPL")

    after_ms = int((dt.datetime.now(tz=dt.timezone.utc) - _EPOCH_UTC).total_seconds() * 1000)
    assert fake_yfinance.Ticker.call_count == 2
    assert first is not second
    assert len(first.earnings) == 2 and first.dividends == []
    assert second.earnings == [] and len(second.dividends) == 2
    assert isinstance(first.fetched_at, int) and isinstance(second.fetched_at, int)
    assert before_ms <= first.fetched_at <= second.fetched_at <= after_ms


def test_invalid_dates_preserve_both_valid_axes(fake_yfinance):
    ticker = fake_yfinance.Ticker.return_value
    ticker.earnings_df = _earnings_frame()
    ticker.actions_df = _actions_frame()
    ticker.earnings_df.loc[pd.NaT] = [9.0, 9.1]
    ticker.actions_df.loc[pd.NaT] = [9.0, 9.0]

    bundle = yfinance_events.fetch_yfinance_events("AAPL")

    assert isinstance(bundle, EventBundle)
    assert [(r.ts, r.eps_actual) for r in bundle.earnings] == [
        (_midnight_ms(2024, 1, 25), 2.18),
        (_midnight_ms(2024, 7, 25), 1.45),
    ]
    assert [(r.ex_ts, r.kind) for r in bundle.dividends] == [
        (_midnight_ms(2024, 2, 14), "cash"),
        (_midnight_ms(2024, 6, 7), "stock_split"),
    ]


def test_all_invalid_dates_return_none(fake_yfinance):
    ticker = fake_yfinance.Ticker.return_value
    ticker.earnings_df = pd.DataFrame(
        {"EPS Estimate": [1.5]}, index=pd.DatetimeIndex([pd.NaT]),
    )
    ticker.actions_df = pd.DataFrame(
        {"Dividends": [0.75]}, index=pd.DatetimeIndex([pd.NaT]),
    )
    assert yfinance_events.fetch_yfinance_events("AAPL") is None
