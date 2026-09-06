from __future__ import annotations

import datetime as dt
import math

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import to_rgba

from tradinglab import constants
from tradinglab.indicators import render
from tradinglab.indicators.base import LineStyle
from tradinglab.indicators.cache import IndicatorCache
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager
from tradinglab.models import Candle


def _candles(n: int, *, gap_at: tuple[int, ...] = ()) -> list[Candle]:
    out: list[Candle] = []
    for i in range(n):
        stamp = dt.datetime(2026, 1, 2) + dt.timedelta(days=i)
        if i in gap_at:
            out.append(Candle.gap(stamp))
            continue
        mid = 100.0 + i * 0.5
        out.append(Candle(
            date=stamp,
            open=mid,
            high=mid + 0.4,
            low=mid - 0.4,
            close=mid + 0.1,
            volume=1_000,
            session="regular",
        ))
    return out


def _config(**params) -> IndicatorConfig:
    values = {
        "conversion_period": 2,
        "base_period": 3,
        "span_b_period": 4,
        "displacement": 2,
    }
    values.update(params)
    return IndicatorConfig(
        kind_id="ichimoku",
        display_name="Ichimoku",
        params=values,
        scopes=frozenset({"main"}),
    )


def _render(
    candles: list[Candle],
    cfg: IndicatorConfig,
    *,
    symbol: str = "AAPL",
) -> tuple[plt.Figure, object, render.PanelIndicatorState]:
    manager = IndicatorManager()
    manager.add(cfg)
    state = render.PanelIndicatorState()
    fig, ax = plt.subplots()
    render.render_for_slot(
        price_ax=ax,
        pane_axes=[],
        candles=candles,
        offset=0,
        manager=manager,
        cache=IndicatorCache(),
        interval="1d",
        scope="main",
        state=state,
        symbol=symbol,
    )
    return fig, ax, state


def test_displaced_lines_cloud_and_forward_horizon() -> None:
    candles = _candles(20)
    cfg = _config()
    fig, _ax, state = _render(candles, cfg)
    lines = state.overlay_lines[cfg.id]
    assert set(lines) == {
        "tenkan", "kijun", "senkou_span_a_raw", "senkou_span_b_raw",
    }
    span_x = np.asarray(lines["senkou_span_a_raw"].get_xdata())
    assert span_x[-1] == len(candles) - 1 + 2
    assert "chikou_source" not in lines
    assert state.forward_horizon == 2
    assert state.overlay_fills[cfg.id]["cloud"]
    assert set(state.all_artists()) >= set(lines.values())
    plt.close(fig)


def test_gap_displacement_counts_observed_bars() -> None:
    candles = _candles(10, gap_at=(3,))
    cfg = _config(
        conversion_period=1,
        base_period=1,
        span_b_period=1,
        displacement=2,
    )
    fig, _ax, state = _render(candles, cfg)
    line = state.overlay_lines[cfg.id]["senkou_span_a_raw"]
    x = np.asarray(line.get_xdata())
    y = np.asarray(line.get_ydata())
    # Source ordinal 1 targets source ordinal 3, whose aligned x is 4.
    at_four = np.flatnonzero(np.isclose(x, 4.0))
    assert at_four.size == 1
    assert y[at_four[0]] == 100.5
    # Alignment-only x=3 remains a hard gap rather than shortening D.
    at_three = np.flatnonzero(np.isclose(x, 3.0))
    assert at_three.size == 1
    assert np.isnan(y[at_three[0]])
    plt.close(fig)


def test_visible_chikou_is_shifted_back_without_wraparound() -> None:
    candles = _candles(10)
    cfg = _config()
    cfg.style = {"chikou_source": LineStyle(color="#9467bd", visible=True)}
    fig, _ax, state = _render(candles, cfg)
    line = state.overlay_lines[cfg.id]["chikou_source"]
    np.testing.assert_allclose(line.get_xdata(), np.arange(8, dtype=float))
    np.testing.assert_allclose(
        line.get_ydata(),
        [c.close for c in candles[2:]],
    )
    plt.close(fig)


def test_cloud_visibility_is_independent_and_cache_neutral() -> None:
    candles = _candles(20)
    cfg = _config()
    manager = IndicatorManager()
    manager.add(cfg)
    cache = IndicatorCache()
    state = render.PanelIndicatorState()
    fig, ax = plt.subplots()
    kwargs = dict(
        price_ax=ax,
        pane_axes=[],
        candles=candles,
        offset=0,
        manager=manager,
        cache=cache,
        interval="1d",
        scope="main",
        state=state,
        symbol="AAPL",
    )
    render.render_for_slot(**kwargs)
    lines_before = dict(state.overlay_lines[cfg.id])
    assert state.overlay_fills[cfg.id]["cloud"]

    manager.update(cfg.id, fill_visibility={"cloud": False})
    render.render_for_slot(**kwargs)
    assert state.overlay_fills[cfg.id] == {}
    assert state.overlay_lines[cfg.id] == lines_before
    assert state.forward_horizon == 2  # Senkou outlines remain visible.
    plt.close(fig)


def test_repeated_cloud_render_does_not_accumulate_collections() -> None:
    candles = _candles(80)
    cfg = _config()
    manager = IndicatorManager()
    manager.add(cfg)
    state = render.PanelIndicatorState()
    fig, ax = plt.subplots()
    kwargs = dict(
        price_ax=ax,
        pane_axes=[],
        candles=candles,
        offset=0,
        manager=manager,
        cache=IndicatorCache(),
        interval="1d",
        scope="main",
        state=state,
        symbol="AAPL",
    )
    for _ in range(5):
        render.render_for_slot(**kwargs)
        assert len(ax.collections) == len(
            state.overlay_fills[cfg.id]["cloud"],
        )
    plt.close(fig)


def test_quotient_ratio_suppresses_config_without_mutating_it() -> None:
    cfg = _config()
    fig, _ax, state = _render(_candles(20), cfg, symbol="AMD/NVDA")
    assert state.overlay_lines == {}
    assert state.overlay_fills == {}
    assert state.forward_horizon == 0
    assert cfg.visible is True
    assert cfg.scopes == frozenset({"main"})
    plt.close(fig)


def test_horizon_disappears_when_all_forward_visuals_are_hidden() -> None:
    cfg = _config()
    cfg.style = {
        "senkou_span_a_raw": LineStyle(visible=False),
        "senkou_span_b_raw": LineStyle(visible=False),
    }
    cfg.fill_visibility = {"cloud": False}
    fig, _ax, state = _render(_candles(20), cfg)
    assert state.forward_horizon == 0
    assert state.overlay_fills[cfg.id] == {}
    assert "tenkan" in state.overlay_lines[cfg.id]
    plt.close(fig)


def test_visible_overlay_bounds_include_projected_cloud() -> None:
    cfg = _config()
    fig, _ax, state = _render(_candles(20), cfg)
    bounds = render.visible_overlay_y_bounds(state, 19.0, 21.0)
    assert bounds is not None
    assert bounds[0] < bounds[1]
    plt.close(fig)


def test_cloud_fill_reads_live_semantic_palette(monkeypatch) -> None:
    monkeypatch.setattr(constants, "BULL_COLOR", "#e69f00")
    cfg = _config()
    fig, _ax, state = _render(_candles(80), cfg)
    collection = state.overlay_fills[cfg.id]["cloud"][0]
    assert tuple(collection.get_facecolor()[0]) == to_rgba("#e69f00", alpha=0.16)
    plt.close(fig)


def test_span_outlines_read_live_semantic_palette(monkeypatch) -> None:
    monkeypatch.setattr(constants, "BULL_COLOR", "#e69f00")
    monkeypatch.setattr(constants, "BEAR_COLOR", "#56b4e9")
    cfg = _config()
    fig, _ax, state = _render(_candles(80), cfg)
    lines = state.overlay_lines[cfg.id]
    assert lines["senkou_span_a_raw"].get_color() == "#e69f00"
    assert lines["senkou_span_b_raw"].get_color() == "#56b4e9"
    plt.close(fig)


def test_cloud_creates_distinct_bull_and_bear_regions() -> None:
    candles = []
    for i in range(80):
        mid = 100.0 + 5.0 * math.sin(i / 5.0)
        candles.append(Candle(
            date=dt.datetime(2026, 1, 2) + dt.timedelta(minutes=i),
            open=mid,
            high=mid + 1.0,
            low=mid - 1.0,
            close=mid,
            volume=1_000,
            session="regular",
        ))
    cfg = _config()
    fig, _ax, state = _render(candles, cfg)
    assert len(state.overlay_fills[cfg.id]["cloud"]) == 2
    plt.close(fig)
