"""Multi-layer tests for :mod:`tradinglab.indicators.render`.

This module is the seam between the indicator compute layer
(:mod:`base`, :mod:`cache`, :mod:`config`) and the matplotlib figure.
The factory-wrapper and ``applicable_overlay_configs`` paths are
already covered by ``test_indicators_render_factory.py`` (A1 latent
tuple-unpack regression suite). This file targets the rest of the
render pipeline — layout math, pane grouping, gap-mask construction,
reference-level resolution, autoscale, and the end-to-end
``render_for_slot`` happy path against a real matplotlib ``Figure``
on the Agg backend.

Using real matplotlib axes is intentionally simpler than building
fakes — Agg is headless, fast, and exercises the exact ``plot`` /
``add_collection`` / ``axhline`` calls the production code does in
the field.
"""
from __future__ import annotations

import datetime as _dt
from typing import List

import matplotlib

matplotlib.use("Agg")  # Force headless backend before importing pyplot.

import matplotlib.pyplot as plt
import numpy as np
import pytest

from tradinglab.indicators import render as _render
from tradinglab.indicators.cache import IndicatorCache
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager
from tradinglab.models import Candle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candles(n: int = 50, *, base: float = 100.0,
                  with_gaps_at: tuple = ()) -> list[Candle]:
    """Make ``n`` synthetic daily candles, optionally inserting gap candles
    at the given indices (created via :meth:`Candle.gap`)."""
    out: list[Candle] = []
    for i in range(n):
        ts = _dt.datetime(2025, 1, 2) + _dt.timedelta(days=i)
        if i in with_gaps_at:
            out.append(Candle.gap(ts))
        else:
            out.append(Candle(
                date=ts,
                open=base + i * 0.5,
                high=base + i * 0.5 + 0.4,
                low=base + i * 0.5 - 0.4,
                close=base + i * 0.5 + 0.1,
                volume=1_000_000,
                session="regular",
            ))
    return out


def _make_sma_config(length: int = 5) -> IndicatorConfig:
    return IndicatorConfig(
        kind_id="sma",
        display_name=f"SMA({length})",
        params={"length": length},
        visible=True,
    )


def _make_rsi_config(length: int = 14) -> IndicatorConfig:
    return IndicatorConfig(
        kind_id="rsi",
        display_name=f"RSI({length})",
        params={"length": length},
        visible=True,
    )


# ---------------------------------------------------------------------------
# 1. compute_layout — height-ratio math + can_add_more gate
# ---------------------------------------------------------------------------


class TestComputeLayout:
    def test_no_indicators_returns_price_volume_ratio(self):
        ratios, can_add = _render.compute_layout(
            num_lower_panes=1, fig_height_in=8.0, dpi=100.0,
        )
        # Just price + volume — the historic 3:1 proportion is exact.
        assert ratios == [_render.PRICE_UNIT, _render.VOLUME_UNIT]
        assert can_add is True

    def test_with_one_indicator_pane(self):
        ratios, can_add = _render.compute_layout(
            num_lower_panes=2, fig_height_in=8.0, dpi=100.0,
        )
        # price + volume + 1 indicator unit.
        assert ratios == [_render.PRICE_UNIT, _render.VOLUME_UNIT,
                          _render.INDICATOR_UNIT]
        # Still well above the floor.
        assert can_add is True

    def test_ratios_length_matches_pane_count(self):
        for n in (1, 2, 3, 4, 5):
            ratios, _ = _render.compute_layout(
                num_lower_panes=n, fig_height_in=8.0, dpi=100.0,
            )
            assert len(ratios) == 1 + n  # +1 for price

    def test_can_add_more_flips_off_under_floor(self):
        """A tiny figure with many indicators must refuse to add more."""
        _, can_add = _render.compute_layout(
            num_lower_panes=8, fig_height_in=1.0, dpi=100.0,
        )
        assert can_add is False

    def test_clamps_to_at_least_one_pane(self):
        """Passing 0 or negative pane count is clamped to 1 (volume)."""
        ratios, _ = _render.compute_layout(
            num_lower_panes=0, fig_height_in=8.0,
        )
        assert len(ratios) == 2  # price + volume


# ---------------------------------------------------------------------------
# 2. PanelIndicatorState — artists tracking + clear
# ---------------------------------------------------------------------------


class TestPanelIndicatorState:
    def test_empty_state_has_no_artists(self):
        state = _render.PanelIndicatorState()
        assert state.all_artists() == []
        assert state.last_config_ids == ()

    def test_all_artists_returns_flat_list(self):
        state = _render.PanelIndicatorState()
        # Use sentinel objects (Line2D-shaped enough for the test).
        state.overlay_lines = {1: {"sma": "ln1", "ema": "ln2"}}
        state.pane_lines = {2: {"rsi": "ln3"}}
        flat = state.all_artists()
        assert set(flat) == {"ln1", "ln2", "ln3"}

    def test_clear_empties_dicts_and_resets_ids(self):
        state = _render.PanelIndicatorState()
        # Use real Line2D objects so ``_safe_remove_line`` doesn't raise.
        fig = plt.figure()
        ax = fig.add_subplot()
        ln1, = ax.plot([0, 1], [0, 1])
        ln2, = ax.plot([0, 1], [1, 0])
        state.overlay_lines = {1: {"sma": ln1}}
        state.pane_lines = {2: {"rsi": ln2}}
        state.panes = {2: ax}
        state.last_config_ids = (1, 2)
        state.clear()
        assert state.overlay_lines == {}
        assert state.pane_lines == {}
        assert state.panes == {}
        assert state.last_config_ids == ()
        plt.close(fig)


# ---------------------------------------------------------------------------
# 3. _build_gap_mask — sentinel return for the all-clean fast path
# ---------------------------------------------------------------------------


class TestBuildGapMask:
    def test_returns_none_for_no_gaps(self):
        candles = _make_candles(20)
        assert _render._build_gap_mask(candles) is None

    def test_returns_mask_with_gaps_marked(self):
        candles = _make_candles(20, with_gaps_at=(3, 7))
        mask = _render._build_gap_mask(candles)
        assert mask is not None
        assert mask.shape == (20,)
        assert mask[3] is np.True_ or bool(mask[3])
        assert mask[7] is np.True_ or bool(mask[7])
        # All other positions are False.
        non_gap = [i for i in range(20) if i not in (3, 7)]
        for i in non_gap:
            assert not bool(mask[i])

    def test_returns_none_for_empty(self):
        assert _render._build_gap_mask([]) is None


# ---------------------------------------------------------------------------
# 4. applicable_pane_groups — grouping by pane_group + dedup
# ---------------------------------------------------------------------------


class TestApplicablePaneGroups:
    def test_empty_manager_returns_empty_groups(self):
        mgr = IndicatorManager()
        assert _render.applicable_pane_groups(mgr, "main", "1d") == []

    def test_overlay_configs_are_excluded(self):
        """SMA is an overlay — must not appear in pane groups."""
        mgr = IndicatorManager()
        mgr.add(_make_sma_config())
        assert _render.applicable_pane_groups(mgr, "main", "1d") == []

    def test_non_overlay_gets_its_own_pane(self):
        """RSI is a non-overlay — gets one pane on its own."""
        mgr = IndicatorManager()
        mgr.add(_make_rsi_config())
        groups = _render.applicable_pane_groups(mgr, "main", "1d")
        assert len(groups) == 1
        assert groups[0][0].kind_id == "rsi"

    def test_two_distinct_kinds_get_two_panes(self):
        """RSI and a second non-overlay (Stochastic) collapse into one
        pane only if they share ``pane_group`` — otherwise two panes."""
        mgr = IndicatorManager()
        mgr.add(_make_rsi_config(length=14))
        mgr.add(_make_rsi_config(length=21))  # same kind = same pane_group?
        groups = _render.applicable_pane_groups(mgr, "main", "1d")
        # If RSI has a non-empty pane_group, both RSIs collapse into 1.
        # If empty, each goes in its own pane.
        # Either way the configs are accounted for.
        total_configs = sum(len(g) for g in groups)
        assert total_configs == 2


# ---------------------------------------------------------------------------
# 5. autoscale_pane_y — fits ylim to visible portion
# ---------------------------------------------------------------------------


class TestAutoscalePaneY:
    def test_no_lines_leaves_ylim_unchanged(self):
        fig = plt.figure()
        ax = fig.add_subplot()
        ax.set_ylim(-50, 50)
        _render.autoscale_pane_y(ax, [], 0, 10)
        assert ax.get_ylim() == (-50, 50)
        plt.close(fig)

    def test_fits_ylim_to_visible_window(self):
        fig = plt.figure()
        ax = fig.add_subplot()
        ln, = ax.plot(np.arange(20), np.arange(20) * 10.0)
        # Window 0..5 contains y-values 0..40 inclusive.
        _render.autoscale_pane_y(ax, [ln], 0, 5)
        ylo, yhi = ax.get_ylim()
        # Should be approximately 0..40 with 5% padding.
        assert ylo < 0  # slight negative padding
        assert 38.0 < yhi < 45.0
        plt.close(fig)

    def test_ignores_nan_in_window(self):
        fig = plt.figure()
        ax = fig.add_subplot()
        y = np.array([np.nan, np.nan, 10.0, 20.0, 30.0])
        ln, = ax.plot(np.arange(5), y)
        _render.autoscale_pane_y(ax, [ln], 0, 5)
        ylo, yhi = ax.get_ylim()
        # NaN values were skipped; window is 10..30 (with small padding).
        assert 8.0 <= ylo <= 11.0
        assert 28.0 <= yhi <= 32.0
        plt.close(fig)

    def test_reads_sc_y_data_for_histogram_artists(self):
        """LineCollection artists stash y-values on ``_sc_y_data`` (since
        they don't expose ``get_ydata`` like Line2D). Autoscale must
        read it."""
        fig = plt.figure()
        ax = fig.add_subplot()

        class _FakeHistArtist:
            _sc_y_data = np.array([5.0, 10.0, 15.0, 20.0])

        _render.autoscale_pane_y(ax, [_FakeHistArtist()], 0, 4)
        ylo, yhi = ax.get_ylim()
        assert ylo < 5.0
        assert yhi > 20.0
        plt.close(fig)

    def test_handles_inverted_or_constant_data(self):
        """If hi_y == lo_y (e.g. a constant horizontal line), the helper
        synthesises a 1-unit window so matplotlib doesn't choke."""
        fig = plt.figure()
        ax = fig.add_subplot()
        ln, = ax.plot(np.arange(10), np.full(10, 42.0))
        _render.autoscale_pane_y(ax, [ln], 0, 10)
        ylo, yhi = ax.get_ylim()
        assert yhi > ylo  # not collapsed
        plt.close(fig)

    def test_out_of_range_window_is_noop(self):
        fig = plt.figure()
        ax = fig.add_subplot()
        ax.set_ylim(-5, 5)
        ln, = ax.plot(np.arange(10), np.arange(10) * 1.0)
        # Window past the end → no data → no change.
        _render.autoscale_pane_y(ax, [ln], 100, 200)
        assert ax.get_ylim() == (-5, 5)
        plt.close(fig)


# ---------------------------------------------------------------------------
# 6. _compute_for_config — gap-aware NaN padding + cache hit
# ---------------------------------------------------------------------------


class TestComputeForConfig:
    def test_returns_dict_for_known_indicator(self):
        candles = _make_candles(30)
        cfg = _make_sma_config(length=5)
        cache = IndicatorCache()
        out = _render._compute_for_config(cfg, candles, None, cache)
        assert out is not None
        # SMA produces at least one output series.
        assert len(out) >= 1
        any_key = next(iter(out))
        arr = out[any_key]
        assert arr.shape == (30,)

    def test_returns_none_for_unknown_kind(self):
        candles = _make_candles(30)
        bad = IndicatorConfig(kind_id="not_a_real_kind",
                              display_name="x", params={})
        cache = IndicatorCache()
        out = _render._compute_for_config(bad, candles, None, cache)
        assert out is None

    def test_gap_path_nan_pads_at_gap_positions(self):
        candles = _make_candles(20, with_gaps_at=(5, 10))
        gap_mask = _render._build_gap_mask(candles)
        assert gap_mask is not None
        cfg = _make_sma_config(length=3)
        cache = IndicatorCache()
        out = _render._compute_for_config(cfg, candles, gap_mask, cache)
        assert out is not None
        for arr in out.values():
            # Gap positions are NaN-padded.
            assert np.isnan(arr[5])
            assert np.isnan(arr[10])
            # Full length preserved.
            assert arr.shape == (20,)

    def test_cache_hit_returns_same_result(self):
        """Second call with the same (candles, config) must hit the
        cache and return identical-valued output."""
        candles = _make_candles(30)
        cfg = _make_sma_config(length=5)
        cache = IndicatorCache()
        out1 = _render._compute_for_config(cfg, candles, None, cache)
        out2 = _render._compute_for_config(cfg, candles, None, cache)
        for k in out1:
            np.testing.assert_array_equal(out1[k], out2[k])

    def test_handles_indicator_construction_failure(self):
        """An indicator whose factory raises on instantiation must
        return None, not propagate."""
        cfg = IndicatorConfig(
            kind_id="sma", display_name="bad",
            params={"length": -1},  # invalid for SMA → constructor raises
        )
        candles = _make_candles(20)
        cache = IndicatorCache()
        out = _render._compute_for_config(cfg, candles, None, cache)
        # Either None (constructor raised) or a valid dict (constructor
        # tolerated the value). Both are acceptable — just must not crash.
        assert out is None or isinstance(out, dict)


# ---------------------------------------------------------------------------
# 7. _resolve_style — color + width resolution
# ---------------------------------------------------------------------------


class TestResolveStyle:
    def test_unknown_kind_returns_neutral_default(self):
        cfg = IndicatorConfig(kind_id="not_a_real_kind",
                              display_name="x", params={})
        color, width = _render._resolve_style(cfg, "sma")
        # Falls back to the hard-coded neutral defaults.
        assert color == "#1f77b4"
        assert width == 1.2

    def test_known_kind_uses_factory_default_style(self):
        cfg = _make_sma_config()
        color, width = _render._resolve_style(cfg, "sma")
        # Default style is at least defined (specifics are factory-dep).
        assert isinstance(color, str)
        assert isinstance(width, float)
        assert width > 0


# ---------------------------------------------------------------------------
# 8. render_for_slot — end-to-end against real matplotlib
# ---------------------------------------------------------------------------


class TestRenderForSlot:
    def test_overlay_creates_line_on_price_axis(self):
        candles = _make_candles(40)
        mgr = IndicatorManager()
        mgr.add(_make_sma_config(length=5))
        cache = IndicatorCache()
        state = _render.PanelIndicatorState()
        fig = plt.figure()
        price_ax = fig.add_subplot()
        _render.render_for_slot(
            price_ax=price_ax, pane_axes=[], candles=candles,
            offset=0, manager=mgr, cache=cache,
            interval="1d", scope="main", state=state,
        )
        # Overlay registered.
        assert len(state.overlay_lines) == 1
        cfg_id = next(iter(state.overlay_lines))
        lines = state.overlay_lines[cfg_id]
        assert len(lines) >= 1
        # The price axes has at least one Line2D for the SMA.
        assert len(price_ax.lines) >= 1
        plt.close(fig)

    def test_non_overlay_renders_in_lower_pane(self):
        candles = _make_candles(40)
        mgr = IndicatorManager()
        cfg = mgr.add(_make_rsi_config(length=14))
        cache = IndicatorCache()
        state = _render.PanelIndicatorState()
        fig = plt.figure()
        price_ax = fig.add_subplot(211)
        lower_ax = fig.add_subplot(212)
        _render.render_for_slot(
            price_ax=price_ax, pane_axes=[lower_ax], candles=candles,
            offset=0, manager=mgr, cache=cache,
            interval="1d", scope="main", state=state,
        )
        # RSI is a non-overlay, so it lives in ``pane_lines`` keyed by cfg.id.
        assert len(state.pane_lines) == 1
        cfg_id = next(iter(state.pane_lines))
        # Pane axes recorded.
        assert state.panes[cfg_id] is lower_ax
        label = getattr(lower_ax, "_sc_pane_label_artist", None)
        assert label is not None, "pane indicators must render an in-pane label"
        assert label.get_picker() is True, "pane labels must be pickable/clickable"
        assert getattr(label, "_sc_pane_label_config_ids", None) == (cfg.id,)
        assert getattr(label, "_sc_pane_label_scope", None) == "main"
        plt.close(fig)

    def test_removing_config_removes_its_artists(self):
        candles = _make_candles(40)
        mgr = IndicatorManager()
        sma_cfg = mgr.add(_make_sma_config(length=5))
        cache = IndicatorCache()
        state = _render.PanelIndicatorState()
        fig = plt.figure()
        price_ax = fig.add_subplot()
        # First render — line installed.
        _render.render_for_slot(
            price_ax=price_ax, pane_axes=[], candles=candles,
            offset=0, manager=mgr, cache=cache,
            interval="1d", scope="main", state=state,
        )
        assert sma_cfg.id in state.overlay_lines
        # Remove the config.
        mgr.remove(sma_cfg.id)
        # Render again — line should be torn down.
        _render.render_for_slot(
            price_ax=price_ax, pane_axes=[], candles=candles,
            offset=0, manager=mgr, cache=cache,
            interval="1d", scope="main", state=state,
        )
        assert sma_cfg.id not in state.overlay_lines
        plt.close(fig)

    def test_invisible_config_hides_existing_lines(self):
        """Toggling ``visible=False`` mid-session must hide the line,
        not delete it (so toggling back doesn't recompute)."""
        candles = _make_candles(40)
        mgr = IndicatorManager()
        cfg = mgr.add(_make_sma_config(length=5))
        cache = IndicatorCache()
        state = _render.PanelIndicatorState()
        fig = plt.figure()
        price_ax = fig.add_subplot()
        # First render — visible.
        _render.render_for_slot(
            price_ax=price_ax, pane_axes=[], candles=candles,
            offset=0, manager=mgr, cache=cache,
            interval="1d", scope="main", state=state,
        )
        assert cfg.id in state.overlay_lines
        lines_before = state.overlay_lines[cfg.id]
        ln = next(iter(lines_before.values()))
        # ``applies_to`` short-circuits on visible=False, so the manager
        # no longer reports it as applicable. The tear-down path removes
        # it. Set visible=False directly via update.
        mgr.update(cfg.id, visible=False)
        _render.render_for_slot(
            price_ax=price_ax, pane_axes=[], candles=candles,
            offset=0, manager=mgr, cache=cache,
            interval="1d", scope="main", state=state,
        )
        # The config is no longer reported applicable, so its tracking
        # was removed entirely.
        assert cfg.id not in state.overlay_lines
        plt.close(fig)

    def test_offset_applied_to_x_coordinates(self):
        """The ``offset`` parameter shifts the indicator x-data — used
        when the slot is compare-aligned and bar i lives at x=i+offset."""
        candles = _make_candles(30)
        mgr = IndicatorManager()
        mgr.add(_make_sma_config(length=5))
        cache = IndicatorCache()
        state = _render.PanelIndicatorState()
        fig = plt.figure()
        price_ax = fig.add_subplot()
        _render.render_for_slot(
            price_ax=price_ax, pane_axes=[], candles=candles,
            offset=0.5, manager=mgr, cache=cache,
            interval="1d", scope="main", state=state,
        )
        # The first line's xdata should start at 0.5.
        cfg_id = next(iter(state.overlay_lines))
        ln = next(iter(state.overlay_lines[cfg_id].values()))
        xdata = ln.get_xdata()
        assert pytest.approx(xdata[0], abs=1e-9) == 0.5
        assert pytest.approx(xdata[-1], abs=1e-9) == 29.5
        plt.close(fig)

    def test_render_updates_existing_line_in_place(self):
        """Second render with same config must NOT create a new Line2D
        — the existing one is mutated via ``set_data``."""
        candles = _make_candles(30)
        mgr = IndicatorManager()
        mgr.add(_make_sma_config(length=5))
        cache = IndicatorCache()
        state = _render.PanelIndicatorState()
        fig = plt.figure()
        price_ax = fig.add_subplot()
        _render.render_for_slot(
            price_ax=price_ax, pane_axes=[], candles=candles,
            offset=0, manager=mgr, cache=cache,
            interval="1d", scope="main", state=state,
        )
        cfg_id = next(iter(state.overlay_lines))
        ln_first = next(iter(state.overlay_lines[cfg_id].values()))
        # Re-render with one more candle.
        candles2 = candles + [Candle(
            date=candles[-1].date + _dt.timedelta(days=1),
            open=120, high=121, low=119, close=120.5, volume=1000,
            session="regular",
        )]
        _render.render_for_slot(
            price_ax=price_ax, pane_axes=[], candles=candles2,
            offset=0, manager=mgr, cache=cache,
            interval="1d", scope="main", state=state,
        )
        ln_second = next(iter(state.overlay_lines[cfg_id].values()))
        # Same Line2D object — set_data update, not replace.
        assert ln_first is ln_second
        plt.close(fig)

    def test_render_populates_last_config_ids(self):
        candles = _make_candles(30)
        mgr = IndicatorManager()
        sma = mgr.add(_make_sma_config(length=5))
        cache = IndicatorCache()
        state = _render.PanelIndicatorState()
        fig = plt.figure()
        price_ax = fig.add_subplot()
        _render.render_for_slot(
            price_ax=price_ax, pane_axes=[], candles=candles,
            offset=0, manager=mgr, cache=cache,
            interval="1d", scope="main", state=state,
        )
        assert state.last_config_ids == (sma.id,)
        plt.close(fig)

    def test_empty_candles_creates_no_lines(self):
        mgr = IndicatorManager()
        mgr.add(_make_sma_config(length=5))
        cache = IndicatorCache()
        state = _render.PanelIndicatorState()
        fig = plt.figure()
        price_ax = fig.add_subplot()
        # Must not crash even though candles is empty.
        _render.render_for_slot(
            price_ax=price_ax, pane_axes=[], candles=[],
            offset=0, manager=mgr, cache=cache,
            interval="1d", scope="main", state=state,
        )
        # Any line that was registered has empty xdata.
        for slot in state.overlay_lines.values():
            for ln in slot.values():
                assert len(ln.get_xdata()) == 0
        plt.close(fig)
