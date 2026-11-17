"""Offline tests for the explicit yfinance history-request timeout.

The 10-second argument preserves yfinance 1.3.0's history default. These
tests stub ``sys.modules["yfinance"]`` to check argument forwarding and
existing result/error handling, not real transport timing. Bootstrap
requests and internal retries can take the whole fetch beyond this timeout.
"""
from __future__ import annotations

import sys
from unittest.mock import Mock

import pandas as pd
import pytest

from tradinglab.data import yfinance_source as yf_src


def _one_bar_frame() -> pd.DataFrame:
    """Minimal valid Yahoo-shaped frame: one daily bar, tz-aware index."""
    idx = pd.date_range("2024-01-02", periods=1, freq="D", tz="America/New_York")
    return pd.DataFrame(
        {
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.5],
            "Volume": [1_000],
        },
        index=idx,
    )


@pytest.fixture()
def fake_yfinance(monkeypatch):
    fake = Mock()
    fake.Ticker.return_value.history.return_value = _one_bar_frame()
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    return fake


@pytest.mark.parametrize(
    ("interval", "period", "prepost"),
    [("1d", "2y", False), ("5m", "60d", True)],
)
def test_timeout_kwarg_reaches_history_call(fake_yfinance, interval, period, prepost) -> None:
    """Explicitly preserve the history timeout and existing request options."""
    candles = yf_src.fetch_live_data("AMD", interval)

    fake_yfinance.Ticker.assert_called_once_with("AMD")
    fake_yfinance.Ticker.return_value.history.assert_called_once_with(
        period=period, interval=interval, prepost=prepost, timeout=10,
    )
    assert candles is not None and len(candles) == 1
    bar = candles[0]
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (100, 101, 99, 100.5, 1_000)


def test_timeout_constant_is_read_at_call_time(fake_yfinance, monkeypatch) -> None:
    fake_yfinance.Ticker.return_value.history.return_value = pd.DataFrame()
    monkeypatch.setattr(yf_src, "YFINANCE_TIMEOUT_S", 0.2)

    assert yf_src.fetch_live_data("AMD", "1d") is None
    fake_yfinance.Ticker.return_value.history.assert_called_once_with(
        period="2y", interval="1d", prepost=False, timeout=0.2,
    )


@pytest.mark.parametrize("error_type", [TimeoutError, OSError, ValueError, KeyError])
def test_history_errors_return_none_with_diagnostic(fake_yfinance, capsys, error_type) -> None:
    """Errors reaching the adapter retain the no-raise contract."""
    error = error_type("history failed")
    history = fake_yfinance.Ticker.return_value.history
    history.side_effect = error

    assert yf_src.fetch_live_data("AMD", "1d") is None
    history.assert_called_once()
    assert capsys.readouterr().out == f"Live fetch failed: {error}\n"


def test_empty_history_returns_none(fake_yfinance, capsys) -> None:
    history = fake_yfinance.Ticker.return_value.history
    history.return_value = pd.DataFrame()

    assert yf_src.fetch_live_data("AMD", "1d") is None
    history.assert_called_once()
    assert capsys.readouterr().out == ""


def test_nonempty_history_with_invalid_ohlc_returns_empty_list(fake_yfinance) -> None:
    frame = _one_bar_frame()
    frame["Open"] = float("nan")
    fake_yfinance.Ticker.return_value.history.return_value = frame

    assert yf_src.fetch_live_data("AMD", "1d") == []


def test_missing_yfinance_returns_none(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "yfinance", None)

    assert yf_src.fetch_live_data("AMD", "1d") is None
