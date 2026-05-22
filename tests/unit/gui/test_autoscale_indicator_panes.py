"""Unit tests for ``ChartApp._autoscale_indicator_panes_for_slot`` —
bug A3 (RRVOL Cumulative pane ylim sticky on overshoot).

The streaming hot paths (``_refresh_view_after_tick``,
``_refresh_view_after_append``) historically only re-autoscaled the
PRICE pane's Y. When a tick added a data point that overshot an
indicator pane's current ylim (RRVOL Cumulative is the canonical
example — its value monotonically grows during the session), the
pane's ylim was not refit, clipping the new bar off-screen until the
user manually pan/zoomed.

Fix: extract the autoscale-pane-y loop from
``_render_indicators_for_slot`` into a standalone helper, and call
that helper from both the tick path's off-screen + on-screen branches
AND the append path. These tests verify the helper itself is
defensively coded (no-op when panes / state / candles missing) and
that it walks every pane in the slot's indicator state.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest import mock

import pytest


def _load_method():
    from tradinglab.app import ChartApp
    return ChartApp._autoscale_indicator_panes_for_slot


class _StubAx:
    def __init__(self, xlim=(0.0, 100.0)):
        self._xlim = xlim
        self.set_ylim_calls: List[tuple] = []

    def get_xlim(self):
        return self._xlim

    def set_ylim(self, lo, hi):
        self.set_ylim_calls.append((lo, hi))


class _StubLine:
    def __init__(self, ydata):
        self._ydata = list(ydata)

    def get_ydata(self):
        return list(self._ydata)


class _StubApp:
    def __init__(self, *, panel_state=None):
        self._panel_state: Dict[str, Any] = panel_state or {}


def test_helper_is_noop_when_slot_missing():
    app = _StubApp(panel_state={})
    _load_method()(app, "primary")  # must not raise


def test_helper_is_noop_when_state_missing():
    app = _StubApp(panel_state={"primary": {"price_ax": _StubAx()}})
    _load_method()(app, "primary")  # must not raise


def test_helper_is_noop_when_no_panes():
    state = SimpleNamespace(panes={}, pane_lines={})
    app = _StubApp(panel_state={
        "primary": {
            "price_ax": _StubAx(),
            "ind_state": state,
            "candles": [object()] * 5,
            "offset": 0,
        }
    })
    _load_method()(app, "primary")  # must not raise


def test_helper_calls_autoscale_for_each_pane():
    # Build a stub state with two panes, each with two lines.
    pane1_ax = _StubAx()
    pane2_ax = _StubAx()
    line1 = _StubLine([1.0, 2.0, 3.0, 4.0, 5.0])
    line2 = _StubLine([0.5, 1.5, 2.5, 3.5, 4.5])
    line3 = _StubLine([100.0, 200.0, 300.0, 400.0, 500.0])
    state = SimpleNamespace(
        panes={"cfg1": pane1_ax, "cfg2": pane2_ax},
        pane_lines={
            "cfg1": {"upper": line1, "lower": line2},
            "cfg2": {"main": line3},
        },
    )
    price_ax = _StubAx(xlim=(0.0, 5.0))
    app = _StubApp(panel_state={
        "primary": {
            "price_ax": price_ax,
            "ind_state": state,
            "candles": [object()] * 5,
            "offset": 0,
        }
    })
    with mock.patch(
        "tradinglab.indicators.render.autoscale_pane_y"
    ) as autoscale_mock:
        _load_method()(app, "primary")
    # Both panes must have had their autoscale invoked.
    assert autoscale_mock.call_count == 2
    # Each call uses the lower_ax for its pane.
    axes_passed = {call.args[0] for call in autoscale_mock.call_args_list}
    assert axes_passed == {pane1_ax, pane2_ax}


def test_helper_clamps_lo_hi_to_candle_range():
    pane_ax = _StubAx()
    line = _StubLine([1.0, 2.0, 3.0])
    state = SimpleNamespace(
        panes={"cfg": pane_ax},
        pane_lines={"cfg": {"main": line}},
    )
    # xlim spans well beyond the 3-bar series — must be clamped to [0, n].
    price_ax = _StubAx(xlim=(-100.0, 1000.0))
    app = _StubApp(panel_state={
        "primary": {
            "price_ax": price_ax,
            "ind_state": state,
            "candles": [object()] * 3,
            "offset": 0,
        }
    })
    with mock.patch(
        "tradinglab.indicators.render.autoscale_pane_y"
    ) as autoscale_mock:
        _load_method()(app, "primary")
    assert autoscale_mock.call_count == 1
    _ax, _lines, lo, hi = autoscale_mock.call_args.args
    assert lo == 0
    assert hi == 3


def test_helper_respects_offset():
    pane_ax = _StubAx()
    line = _StubLine([1.0, 2.0, 3.0, 4.0])
    state = SimpleNamespace(
        panes={"cfg": pane_ax},
        pane_lines={"cfg": {"main": line}},
    )
    # xlim is (10, 14) in canvas coords; offset = 10 → indices (0, 4).
    price_ax = _StubAx(xlim=(10.0, 14.0))
    app = _StubApp(panel_state={
        "primary": {
            "price_ax": price_ax,
            "ind_state": state,
            "candles": [object()] * 4,
            "offset": 10,
        }
    })
    with mock.patch(
        "tradinglab.indicators.render.autoscale_pane_y"
    ) as autoscale_mock:
        _load_method()(app, "primary")
    _ax, _lines, lo, hi = autoscale_mock.call_args.args
    assert lo == 0
    assert hi == 4


def test_helper_swallows_autoscale_exception():
    pane_ax = _StubAx()
    line = _StubLine([1.0, 2.0])
    state = SimpleNamespace(
        panes={"cfg": pane_ax},
        pane_lines={"cfg": {"main": line}},
    )
    price_ax = _StubAx(xlim=(0.0, 2.0))
    app = _StubApp(panel_state={
        "primary": {
            "price_ax": price_ax,
            "ind_state": state,
            "candles": [object()] * 2,
            "offset": 0,
        }
    })
    # A bad autoscale must NOT propagate — the streaming hot path
    # would silently drop ticks if it did.
    with mock.patch(
        "tradinglab.indicators.render.autoscale_pane_y",
        side_effect=RuntimeError("bad pane"),
    ):
        _load_method()(app, "primary")  # must not raise


def test_helper_handles_broken_xlim_gracefully():
    """An axes that raises from get_xlim should not crash the helper."""
    class _BrokenAx:
        def get_xlim(self):
            raise RuntimeError("axes destroyed")
    pane_ax = _StubAx()
    state = SimpleNamespace(
        panes={"cfg": pane_ax},
        pane_lines={"cfg": {"main": _StubLine([1.0])}},
    )
    app = _StubApp(panel_state={
        "primary": {
            "price_ax": _BrokenAx(),
            "ind_state": state,
            "candles": [object()],
            "offset": 0,
        }
    })
    _load_method()(app, "primary")  # must not raise


def test_helper_skips_when_state_has_no_panes_attr():
    """A malformed state object (missing ``panes``) is handled defensively."""
    state = SimpleNamespace()  # no panes / pane_lines attrs
    app = _StubApp(panel_state={
        "primary": {
            "price_ax": _StubAx(xlim=(0.0, 1.0)),
            "ind_state": state,
            "candles": [object()],
            "offset": 0,
        }
    })
    _load_method()(app, "primary")  # must not raise
