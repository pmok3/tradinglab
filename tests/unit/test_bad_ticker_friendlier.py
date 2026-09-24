"""Exercise bad-ticker-friendlier through the real load/rejection path.

Messages identify the mistyped symbol and suggest spelling/source changes,
without exposing internal provider names. Both primary and compare rejection
must restore the confirmed selection; primary failure preserves the old chart.
"""

from __future__ import annotations

import pytest

from tests.unit.test_load_data_prefetch_indicator_refresh import (
    _candles,
    _install_load_data_harness,
)
from tradinglab import disk_cache
from tradinglab.app import ChartApp
from tradinglab.data.base import DATA_SOURCES


@pytest.mark.parametrize("vendor", [
    "yfinance", "synthetic", "synthetic-stream", "alpaca", "polygon", "schwab",
])
@pytest.mark.parametrize("side", ["primary", "compare"])
def test_bad_ticker_message_and_rejection_behavior(monkeypatch, vendor, side):
    app = ChartApp.__new__(ChartApp)
    old_primary = _candles(50)
    _install_load_data_harness(app, primary=old_primary, compare=_candles(200))
    app.source_var.set(vendor)
    app.ticker_var.set("TSLAA" if side == "primary" else "AMD")
    app.compare_ticker_var.set("TSLAA" if side == "compare" else "SPY")
    messages = []
    app._status.error = messages.append
    monkeypatch.setitem(
        DATA_SOURCES, vendor,
        lambda ticker, _interval: [] if ticker == "TSLAA" else _candles(100),
    )
    monkeypatch.setattr(disk_cache, "load", lambda *_: None)
    monkeypatch.setattr(disk_cache, "save", lambda *_: None)

    app._load_data()

    assert len(messages) == 1
    message = messages[0]
    assert "'TSLAA'" in message and "not found" in message
    assert "spelling" in message.lower() and "data source" in message.lower()
    assert vendor not in message
    assert app.ticker_var.get() == "AMD"
    assert app.compare_ticker_var.get() == "SPY"
    if side == "primary":
        assert app._primary is old_primary
    else:
        assert app._primary[0].close == 100
        assert app._compare == []
