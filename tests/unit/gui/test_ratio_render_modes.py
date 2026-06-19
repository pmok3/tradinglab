"""Unit tests for ratio-chart render-mode helpers (line/candle, hide-volume,
rebase-to-100) — exercised without constructing a full ChartApp by calling the
unbound methods against a minimal stub ``self``.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from tradinglab.app import ChartApp
from tradinglab.models import Candle


def _stub(symbol, *, rebase=False):
    s = SimpleNamespace()
    s._active_symbol_for_slot = lambda _slot: symbol
    s._ratio_rebase_var = SimpleNamespace(get=lambda: rebase)
    return s


def _series(closes):
    out = []
    t = dt.datetime(2026, 6, 15, 9, 30)
    for c in closes:
        out.append(Candle(date=t, open=c, high=c + 0.1, low=c - 0.1,
                          close=c, volume=10, session="regular"))
        t += dt.timedelta(days=1)
    return out


# ------------------------------------------------------------------- rebase
def test_rebase_to_100_on_ratio():
    cs = _series([2.0, 3.0, 1.0])
    out = ChartApp._maybe_rebase_candles(
        _stub("AMD/NVDA", rebase=True), "primary", cs)
    assert out is not cs  # new list
    assert abs(out[0].close - 100.0) < 1e-9
    assert abs(out[1].close - 150.0) < 1e-9
    assert abs(out[2].close - 50.0) < 1e-9
    # OHLC scaled by the same factor
    assert abs(out[0].high - (2.1 * 50.0)) < 1e-9


def test_rebase_passthrough_when_toggle_off():
    cs = _series([2.0, 3.0])
    assert ChartApp._maybe_rebase_candles(
        _stub("AMD/NVDA", rebase=False), "primary", cs) is cs


def test_rebase_passthrough_for_non_ratio():
    cs = _series([2.0, 3.0])
    assert ChartApp._maybe_rebase_candles(
        _stub("AMD", rebase=True), "primary", cs) is cs


def test_rebase_empty_and_nonpositive_anchor_safe():
    assert ChartApp._maybe_rebase_candles(_stub("AMD/NVDA", rebase=True), "primary", []) == []
    cs = _series([0.0, 3.0])  # anchor close 0 -> passthrough (no div-by-zero)
    assert ChartApp._maybe_rebase_candles(
        _stub("AMD/NVDA", rebase=True), "primary", cs) is cs


# ---------------------------------------------------------------- hide volume
def test_hides_volume_for_ratio():
    # Ratios always hide the volume pane (always candlesticks, no volume).
    assert ChartApp._slot_hides_volume(_stub("AMD/NVDA"), "primary") is True
    assert ChartApp._slot_hides_volume(_stub("RSP/SPY"), "primary") is True


def test_shows_volume_for_non_ratio():
    assert ChartApp._slot_hides_volume(_stub("AMD"), "primary") is False
    assert ChartApp._slot_hides_volume(_stub("SPY"), "primary") is False
