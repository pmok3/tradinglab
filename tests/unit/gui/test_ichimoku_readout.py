from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

from tradinglab.gui.interaction import InteractionMixin
from tradinglab.gui.readout_legend import build_overlay_legend_rows
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager
from tradinglab.indicators.render import PanelIndicatorState
from tradinglab.models import Candle


def _manager() -> IndicatorManager:
    manager = IndicatorManager()
    manager.add(IndicatorConfig(
        kind_id="ichimoku",
        display_name="Ichimoku",
        params={
            "conversion_period": 9,
            "base_period": 26,
            "span_b_period": 52,
            "displacement": 26,
        },
        scopes=frozenset({"main"}),
    ))
    return manager


def test_compact_row_contains_tenkan_kijun_and_cloud_state() -> None:
    rows = build_overlay_legend_rows(
        _manager(), "main", "1d", symbol="AAPL",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.label == "Ichi(9,26,52,26)"
    assert [segment.output_key for segment in row.outputs] == [
        "tenkan", "kijun",
    ]
    assert row.state is not None
    assert row.state.label == "Cloud"
    assert row.state.first_output == "senkou_span_a_raw"
    assert row.unavailable_reason == ""


def test_quotient_ratio_row_explains_why_it_is_unavailable() -> None:
    row = build_overlay_legend_rows(
        _manager(), "main", "1d", symbol="AMD/NVDA",
    )[0]
    assert "approximate" in row.unavailable_reason


def test_cloud_state_follows_independent_fill_visibility() -> None:
    manager = _manager()
    cfg = manager.list()[0]
    cfg.fill_visibility = {"cloud": False}
    row = build_overlay_legend_rows(
        manager, "main", "1d", symbol="AAPL",
    )[0]
    assert row.state is None
    assert [segment.output_key for segment in row.outputs] == [
        "tenkan", "kijun",
    ]


def test_shift_aware_line_lookup_samples_plotted_x_not_array_index() -> None:
    fig, ax = plt.subplots()
    line, = ax.plot([2.5, 3.5, 4.5], [10.0, 11.0, 12.0])
    line._sc_x_data = np.array([2.5, 3.5, 4.5])
    line._sc_y_data = np.array([10.0, 11.0, 12.0])
    line._sc_panel_offset = 0.5
    assert InteractionMixin._line_value_at(line, 2) == 10.0
    assert InteractionMixin._line_value_at(line, 1) is None
    plt.close(fig)


class _Text:
    def __init__(self) -> None:
        self.value = ""
        self._text = self

    def set_text(self, value: str) -> None:
        self.value = value

    def set_color(self, _value: str) -> None:
        return


class _Box:
    def __init__(self, line, *, state=None) -> None:
        self._main_text = _Text()
        self._pct_text = _Text()
        self._ind_rows = [{
            "config_id": 7,
            "outputs": [{
                "output_key": "tenkan",
                "value_textarea": _Text(),
                "line": line,
                "notset": False,
            }],
            "state": state,
        }]
        self.visible = False

    def set_visible(self, value: bool) -> None:
        self.visible = value


def test_future_gutter_readout_shows_projection_without_invented_ohlcv() -> None:
    fig, ax = plt.subplots()
    stale_line, = ax.plot([11.0], [1.0])
    stale_line._sc_x_data = np.array([11.0])
    stale_line._sc_y_data = np.array([1.0])
    stale_line._sc_panel_offset = 0.0
    current_line, = ax.plot([11.0], [123.45])
    current_line._sc_x_data = np.array([11.0])
    current_line._sc_y_data = np.array([123.45])
    current_line._sc_panel_offset = 0.0
    candles = [
        Candle(
            date=dt.datetime(2026, 1, 2) + dt.timedelta(days=i),
            open=100 + i,
            high=101 + i,
            low=99 + i,
            close=100.5 + i,
            volume=1_000,
            session="regular",
        )
        for i in range(10)
    ]
    box = _Box(stale_line)
    app = object.__new__(InteractionMixin)
    app._readout_artists = {ax: box}
    app._ax_candle_map = {ax: (candles, "price", 0)}
    app._panel_state = {
        "primary": {
            "price_ax": ax,
            "render_start": 0,
            "render_end": len(candles),
            "ind_state": PanelIndicatorState(
                overlay_lines={7: {"tenkan": current_line}},
                forward_horizon=2,
            ),
        },
    }
    app._theme = {"text": "#ffffff"}
    app._pane_value_labels = {}
    app._last_readout_key = None

    app._update_readout(11.0)

    assert box._main_text.value == "Projected +2 bars"
    assert "O " not in box._main_text.value
    assert box._ind_rows[0]["outputs"][0]["value_textarea"].value == "123.45 "
    assert box.visible is True
    plt.close(fig)


def test_cloud_state_resolves_replacement_lines_after_slice_rebuild() -> None:
    from tradinglab.gui.readout_legend import OverlayState

    fig, ax = plt.subplots()

    def _line(value: float):
        line, = ax.plot([9.0], [value])
        line._sc_x_data = np.array([9.0])
        line._sc_y_data = np.array([value])
        line._sc_panel_offset = 0.0
        return line

    stale_a, stale_b = _line(1.0), _line(2.0)
    current_a, current_b = _line(3.0), _line(2.0)
    state_text = _Text()
    state_meta = {
        "spec": OverlayState(
            "senkou_span_a_raw", "senkou_span_b_raw", "Cloud", "cloud",
            "↑", "↓", "=",
        ),
        "textarea": state_text,
        "first_line": stale_a,
        "second_line": stale_b,
        "direction": "neutral",
    }
    box = _Box(_line(100.0), state=state_meta)
    candles = [
        Candle(
            date=dt.datetime(2026, 1, 2) + dt.timedelta(days=i),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1_000,
            session="regular",
        )
        for i in range(10)
    ]
    app = object.__new__(InteractionMixin)
    app._readout_artists = {ax: box}
    app._ax_candle_map = {ax: (candles, "price", 0)}
    app._panel_state = {
        "primary": {
            "price_ax": ax,
            "render_start": 0,
            "render_end": len(candles),
            "ind_state": PanelIndicatorState(overlay_lines={
                7: {
                    "tenkan": _line(100.0),
                    "senkou_span_a_raw": current_a,
                    "senkou_span_b_raw": current_b,
                },
            }),
        },
    }
    app._theme = {"text": "#ffffff"}
    app._pane_value_labels = {}
    app._last_readout_key = None

    app._update_readout(9.0)

    assert state_text.value == "Cloud ↑ "
    assert state_meta["direction"] == "bull"
    plt.close(fig)


def test_shared_future_gutter_suppresses_compare_ohlcv_too() -> None:
    fig = plt.figure()
    primary_ax = fig.add_subplot(211)
    compare_ax = fig.add_subplot(212, sharex=primary_ax)
    candles = [
        Candle(
            date=dt.datetime(2026, 1, 2) + dt.timedelta(days=i),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1_000,
            session="regular",
        )
        for i in range(10)
    ]
    primary_box = _Box(None)
    compare_box = _Box(None)
    app = object.__new__(InteractionMixin)
    app._readout_artists = {
        primary_ax: primary_box,
        compare_ax: compare_box,
    }
    app._ax_candle_map = {
        primary_ax: (candles, "price", 0),
        compare_ax: (candles, "price", 0),
    }
    app._panel_state = {
        "primary": {
            "price_ax": primary_ax,
            "render_start": 0,
            "render_end": len(candles),
            "ind_state": PanelIndicatorState(forward_horizon=2),
        },
        "compare": {
            "price_ax": compare_ax,
            "render_start": 0,
            "render_end": len(candles),
            "ind_state": PanelIndicatorState(forward_horizon=0),
        },
    }
    app._theme = {"text": "#ffffff"}
    app._pane_value_labels = {}
    app._last_readout_key = None

    app._update_readout(11.0)

    assert primary_box._main_text.value == "Projected +2 bars"
    assert compare_box._main_text.value == "Projected +2 bars"
    plt.close(fig)
