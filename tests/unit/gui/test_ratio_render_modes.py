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


# ------------------------------------------------- dynamic leftmost-visible
class _FakeAxes:  # hashable (used as an _ax_candle_map key)
    def __init__(self, xlim):
        self._xlim = xlim

    def get_xlim(self):
        return self._xlim


def test_rebased_to_anchor_at_leftmost_visible():
    cs = _series([2.0, 3.0, 1.0, 4.0])
    # Anchor at index 1 (close 3.0) -> that bar reads 100; factor 100/3.
    out = ChartApp._rebased_to_anchor(cs, 1)
    assert out is not None and out is not cs
    f = 100.0 / 3.0
    assert abs(out[1].close - 100.0) < 1e-9
    assert abs(out[0].close - 2.0 * f) < 1e-9
    assert abs(out[2].close - 1.0 * f) < 1e-9
    assert abs(out[3].close - 4.0 * f) < 1e-9
    # OHLC all scaled by the same factor.
    assert abs(out[0].high - 2.1 * f) < 1e-9
    assert abs(out[2].low - 0.9 * f) < 1e-9


def test_rebased_to_anchor_noop_when_left_edge_already_100():
    cs = _series([100.0, 150.0, 50.0])
    # Leftmost bar already reads 100 -> no re-bake (factor within 1e-6 of 1).
    assert ChartApp._rebased_to_anchor(cs, 0) is None


def test_rebased_to_anchor_clamps_index_and_guards():
    cs = _series([2.0, 4.0])
    out = ChartApp._rebased_to_anchor(cs, 99)  # out-of-range -> clamp to last
    assert out is not None
    assert abs(out[1].close - 100.0) < 1e-9
    assert abs(out[0].close - 2.0 * 25.0) < 1e-9
    assert ChartApp._rebased_to_anchor([], 0) is None
    assert ChartApp._rebased_to_anchor(_series([0.0, 3.0]), 0) is None


def test_apply_dynamic_ratio_rebase_anchors_leftmost_visible():
    cs = _series([2.0, 3.0, 1.0, 4.0, 5.0])
    # xlim left edge ceil(1.6)=2 -> leftmost visible bar index 2 (close 1.0).
    ax_p = _FakeAxes((1.6, 3.4))
    drawn, fitted = [], []
    ps = {
        "candles": list(cs), "price_ax": ax_p, "vol_ax": None, "ind_axes": [],
        "render_start": 0, "render_end": len(cs), "offset": 0,
    }
    self_ = SimpleNamespace(
        _pan_state=None,
        _ratio_rebase_var=SimpleNamespace(get=lambda: True),
        _active_symbol_for_slot=lambda _slot: "AMD/NVDA",
        _panel_state={"primary": ps},
        _ax_candle_map={ax_p: (ps["candles"], "price", 0)},
        _series_cache={id(ps["candles"]): object()},  # stale entry -> evicted
        _draw_slice=lambda slot, a, b: drawn.append((slot, a, b)),
        _autoscale_slot_y=lambda slot, lo, hi: fitted.append((slot, lo, hi)),
    )
    old_id = id(ps["candles"])
    ChartApp._apply_dynamic_ratio_rebase(self_)
    out = ps["candles"]
    assert abs(out[2].close - 100.0) < 1e-9          # leftmost visible == 100
    assert abs(out[0].close - 200.0) < 1e-9          # whole series scaled x100
    assert self_._ax_candle_map[ax_p][0] is out       # map re-pointed
    assert old_id not in self_._series_cache          # stale entry evicted
    assert drawn == [("primary", 0, 5)]
    assert fitted == [("primary", 2, 4)]


def test_apply_dynamic_ratio_rebase_skips_during_pan_drag():
    cs = _series([2.0, 3.0, 1.0])
    ax_p = _FakeAxes((0.6, 2.4))
    ps = {
        "candles": list(cs), "price_ax": ax_p, "vol_ax": None, "ind_axes": [],
        "render_start": 0, "render_end": len(cs), "offset": 0,
    }
    self_ = SimpleNamespace(
        _pan_state={"active": True},  # mid pan-drag -> no re-anchor
        _ratio_rebase_var=SimpleNamespace(get=lambda: True),
        _active_symbol_for_slot=lambda _slot: "AMD/NVDA",
        _panel_state={"primary": ps},
        _ax_candle_map={ax_p: (ps["candles"], "price", 0)},
        _draw_slice=lambda *a: None,
        _autoscale_slot_y=lambda *a: None,
    )
    ChartApp._apply_dynamic_ratio_rebase(self_)
    assert ps["candles"] is cs or ps["candles"] == cs  # untouched


# --------------------------------------------- live pan y-axis label scale
def test_ratio_rebase_y_scale_leftmost_visible():
    cs = _series([2.0, 3.0, 1.0, 4.0])
    ax_p = _FakeAxes((1.6, 3.4))  # left edge ceil(1.6) = 2 -> close 1.0
    ps = {"candles": cs, "offset": 0}
    self_ = SimpleNamespace(_ax_candle_map={ax_p: (cs, "price", 0)})
    s = ChartApp._ratio_rebase_y_scale(self_, ps, ax_p)
    assert abs(s - 100.0) < 1e-9  # 100 / 1.0
    # A tick at the leftmost bar's value reads exactly 100 after scaling.
    assert abs(1.0 * s - 100.0) < 1e-9
    # A tick at bar 3 (close 4.0) reads 400 (4x the left edge).
    assert abs(4.0 * s - 400.0) < 1e-9


def test_ratio_rebase_y_scale_noop_when_left_edge_is_100():
    cs = _series([100.0, 150.0, 50.0])
    ax_p = _FakeAxes((-0.4, 2.4))  # left edge 0 -> close 100.0
    ps = {"candles": cs, "offset": 0}
    self_ = SimpleNamespace(_ax_candle_map={ax_p: (cs, "price", 0)})
    assert abs(ChartApp._ratio_rebase_y_scale(self_, ps, ax_p) - 1.0) < 1e-9


def test_ratio_rebase_y_scale_guards_empty_and_nonpositive():
    ax_p = _FakeAxes((0.0, 1.0))
    self_ = SimpleNamespace(_ax_candle_map={})
    assert ChartApp._ratio_rebase_y_scale(self_, {"candles": []}, ax_p) == 1.0
    bad = _series([0.0, 3.0])  # leftmost close 0 -> safe passthrough
    ps = {"candles": bad, "offset": 0}
    self2 = SimpleNamespace(_ax_candle_map={ax_p: (bad, "price", 0)})
    assert ChartApp._ratio_rebase_y_scale(self2, ps, ax_p) == 1.0


# ---------------------------------------------------------------- hide volume
def test_hides_volume_for_ratio():
    # Ratios always hide the volume pane (always candlesticks, no volume).
    assert ChartApp._slot_hides_volume(_stub("AMD/NVDA"), "primary") is True
    assert ChartApp._slot_hides_volume(_stub("RSP/SPY"), "primary") is True


def test_shows_volume_for_non_ratio():
    assert ChartApp._slot_hides_volume(_stub("AMD"), "primary") is False
    assert ChartApp._slot_hides_volume(_stub("SPY"), "primary") is False
