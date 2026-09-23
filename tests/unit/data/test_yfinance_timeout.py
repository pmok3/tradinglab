"""Offline tests for the explicit yfinance history-request timeout.

The 10-second argument preserves yfinance 1.3.0's history default. These
tests stub ``sys.modules["yfinance"]`` to check argument forwarding and
existing result/error handling, not real transport timing. Bootstrap
requests and internal retries can take the whole fetch beyond this timeout.
"""
from __future__ import annotations

import sys
from types import ModuleType

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
    """Install a fake ``yfinance`` module.

    Returns ``(behaviors, calls)`` where ``behaviors`` maps a ticker to a
    ``(ticker, kwargs) -> DataFrame`` callable invoked by the fake
    ``Ticker.history()``, and ``calls`` records every ``history()``
    invocation as ``(ticker, kwargs)``.
    """
    behaviors: dict = {}
    calls: list = []

    class _FakeTicker:
        def __init__(self, ticker: str) -> None:
            self._ticker = ticker

        def history(self, **kwargs):
            calls.append((self._ticker, kwargs))
            return behaviors[self._ticker](self._ticker, kwargs)

    mod = ModuleType("yfinance")
    mod.Ticker = _FakeTicker
    monkeypatch.setitem(sys.modules, "yfinance", mod)
    return behaviors, calls


@pytest.mark.parametrize(
    ("interval", "period", "prepost"),
    [("1d", "2y", False), ("5m", "60d", True)],
)
def test_timeout_kwarg_reaches_history_call(fake_yfinance, interval, period, prepost) -> None:
    """Explicitly preserve the history timeout and existing request options."""
    behaviors, calls = fake_yfinance
    behaviors["AMD"] = lambda ticker, kwargs: _one_bar_frame()

    candles = yf_src.fetch_live_data("AMD", interval)

    assert calls == [("AMD", {
        "period": period, "interval": interval, "prepost": prepost, "timeout": 10,
    })]
    assert yf_src.YFINANCE_TIMEOUT_S == 10
    assert candles is not None and len(candles) == 1
    bar = candles[0]
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (100, 101, 99, 100.5, 1_000)


def test_timeout_constant_is_read_at_call_time(fake_yfinance, monkeypatch) -> None:
    behaviors, calls = fake_yfinance
    behaviors["AMD"] = lambda ticker, kwargs: pd.DataFrame()
    monkeypatch.setattr(yf_src, "YFINANCE_TIMEOUT_S", 0.2)

    assert yf_src.fetch_live_data("AMD", "1d") is None
    assert calls == [("AMD", {
        "period": "2y", "interval": "1d", "prepost": False, "timeout": 0.2,
    })]


@pytest.mark.parametrize("error_type", [TimeoutError, OSError, ValueError, KeyError])
def test_history_errors_return_none_with_diagnostic(fake_yfinance, capsys, error_type) -> None:
    """Errors reaching the adapter retain the no-raise contract."""
    behaviors, calls = fake_yfinance
    error = error_type("history failed")

    def _fail(ticker: str, kwargs: dict):
        raise error

    behaviors["AMD"] = _fail

    assert yf_src.fetch_live_data("AMD", "1d") is None
    assert len(calls) == 1  # The adapter adds no retry of its own.
    assert capsys.readouterr().out == f"Live fetch failed: {error}\n"


def test_empty_history_returns_none(fake_yfinance, capsys) -> None:
    behaviors, calls = fake_yfinance
    behaviors["AMD"] = lambda ticker, kwargs: pd.DataFrame()

    assert yf_src.fetch_live_data("AMD", "1d") is None
    assert len(calls) == 1
    assert capsys.readouterr().out == ""


def test_nonempty_history_with_invalid_ohlc_returns_empty_list(fake_yfinance) -> None:
    behaviors, _calls = fake_yfinance
    frame = _one_bar_frame()
    frame["Open"] = float("nan")
    behaviors["AMD"] = lambda ticker, kwargs: frame

    assert yf_src.fetch_live_data("AMD", "1d") == []


def test_missing_yfinance_returns_none(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "yfinance", None)

    assert yf_src.fetch_live_data("AMD", "1d") is None
