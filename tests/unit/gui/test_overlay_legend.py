"""Unit tests for the per-overlay legend widget (big-bet item #9)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List

import pytest

tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")

from tradinglab.gui.overlay_legend import (
    OverlayLegend,
    collect_overlay_configs,
)
from tradinglab.indicators.base import LineStyle
from tradinglab.indicators.config import IndicatorConfig, IndicatorManager


@pytest.fixture()
def root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        r.geometry("400x300-3000-3000")
    except tk.TclError:
        pass
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


@pytest.fixture()
def manager() -> IndicatorManager:
    m = IndicatorManager()
    return m


def _make_sma_cfg(visible: bool = True, color: str = "#ff8800") -> IndicatorConfig:
    return IndicatorConfig(
        kind_id="sma",
        display_name="SMA(20)",
        params={"length": 20},
        style={"sma": LineStyle(color=color, width=1.2, visible=True)},
        intervals=(),
        scopes=frozenset({"main"}),
        visible=visible,
    )


def _make_ema_cfg(visible: bool = True) -> IndicatorConfig:
    return IndicatorConfig(
        kind_id="ema",
        display_name="EMA(50)",
        params={"length": 50},
        style={"ema": LineStyle(color="#00aaff", width=1.2, visible=True)},
        intervals=(),
        scopes=frozenset({"main"}),
        visible=visible,
    )


def test_legend_empty_list_hides_frame(root, manager):
    legend = OverlayLegend(root, manager=manager, theme={"text": "#fff", "spine": "#888"})
    legend.refresh([])
    # Hidden via place_forget — geometry manager reports nothing for the frame.
    assert legend._placed is False


def test_legend_builds_row_per_config(root, manager):
    legend = OverlayLegend(root, manager=manager, theme={"text": "#fff", "spine": "#888"})
    cfg1 = _make_sma_cfg()
    cfg2 = _make_ema_cfg()
    manager.add(cfg1)
    manager.add(cfg2)
    legend.refresh([cfg1, cfg2])
    assert len(legend._rows) == 2, f"expected 2 rows, got {len(legend._rows)}"
    assert cfg1.id in legend._buttons_by_id
    assert cfg2.id in legend._buttons_by_id
    assert legend._placed is True


def test_legend_button_glyph_reflects_visible(root, manager):
    legend = OverlayLegend(root, manager=manager, theme={"text": "#fff", "spine": "#888"})
    cfg_on = _make_sma_cfg(visible=True)
    cfg_off = _make_ema_cfg(visible=False)
    manager.add(cfg_on)
    manager.add(cfg_off)
    legend.refresh([cfg_on, cfg_off])
    on_glyph = legend._buttons_by_id[cfg_on.id].cget("text")
    off_glyph = legend._buttons_by_id[cfg_off.id].cget("text")
    assert on_glyph != off_glyph, (
        f"visible / hidden configs must use different glyphs: "
        f"got both = {on_glyph!r}")


def test_legend_toggle_flips_visible_via_manager(root, manager):
    legend = OverlayLegend(root, manager=manager, theme={"text": "#fff", "spine": "#888"})
    cfg = _make_sma_cfg(visible=True)
    manager.add(cfg)
    legend.refresh([cfg])
    assert manager.get(cfg.id).visible is True
    legend._toggle(cfg.id)
    assert manager.get(cfg.id).visible is False, (
        "toggle should have flipped cfg.visible to False")
    legend._toggle(cfg.id)
    assert manager.get(cfg.id).visible is True, (
        "second toggle should restore visible=True")


def test_legend_toggle_missing_id_is_noop(root, manager):
    legend = OverlayLegend(root, manager=manager, theme={"text": "#fff", "spine": "#888"})
    legend.refresh([])
    legend._toggle(99999)


def test_legend_swatch_uses_style_color(root, manager):
    legend = OverlayLegend(root, manager=manager, theme={"text": "#fff", "spine": "#888"})
    cfg = _make_sma_cfg(color="#ff00ff")
    color = legend._color_for(cfg)
    assert color == "#ff00ff", f"expected style color #ff00ff, got {color!r}"


def test_legend_refresh_clears_old_rows(root, manager):
    legend = OverlayLegend(root, manager=manager, theme={"text": "#fff", "spine": "#888"})
    cfg1 = _make_sma_cfg()
    cfg2 = _make_ema_cfg()
    manager.add(cfg1)
    manager.add(cfg2)
    legend.refresh([cfg1, cfg2])
    assert len(legend._rows) == 2
    # Now remove one and refresh — old row must be torn down.
    legend.refresh([cfg1])
    assert len(legend._rows) == 1
    assert cfg1.id in legend._buttons_by_id
    assert cfg2.id not in legend._buttons_by_id


def test_legend_apply_theme_updates_palette(root, manager):
    legend = OverlayLegend(root, manager=manager, theme={"text": "#fff", "spine": "#888"})
    new_theme = {"text": "#000", "spine": "#444"}
    legend.apply_theme(new_theme)
    assert legend._theme["spine"] == "#444"
    assert legend._theme["text"] == "#000"


def test_collect_overlay_configs_includes_hidden(root, manager):
    """The legend MUST include hidden configs so they can be re-enabled."""
    cfg_visible = _make_sma_cfg(visible=True)
    cfg_hidden = _make_ema_cfg(visible=False)
    manager.add(cfg_visible)
    manager.add(cfg_hidden)
    out = collect_overlay_configs(manager, "main", "1d")
    ids = {c.id for c in out}
    assert cfg_visible.id in ids, "visible cfg must be in the list"
    assert cfg_hidden.id in ids, (
        "hidden cfg must be in the list — the legend "
        "must let users re-enable hidden overlays")


def test_collect_overlay_configs_filters_by_scope(root, manager):
    cfg_main = _make_sma_cfg()
    cfg_compare = IndicatorConfig(
        kind_id="ema", display_name="EMA(50) compare",
        params={"length": 50},
        scopes=frozenset({"compare"}),
        visible=True,
    )
    manager.add(cfg_main)
    manager.add(cfg_compare)
    out = collect_overlay_configs(manager, "main", "1d")
    ids = {c.id for c in out}
    assert cfg_main.id in ids
    assert cfg_compare.id not in ids, (
        "compare-only cfg must NOT appear in the main legend")


def test_collect_overlay_configs_filters_by_interval(root, manager):
    cfg_minutes = IndicatorConfig(
        kind_id="sma", display_name="SMA(5m)",
        params={"length": 20},
        intervals=("5m",),  # restricted to 5m
        scopes=frozenset({"main"}),
        visible=True,
    )
    manager.add(cfg_minutes)
    out_5m = collect_overlay_configs(manager, "main", "5m")
    out_1d = collect_overlay_configs(manager, "main", "1d")
    assert cfg_minutes.id in {c.id for c in out_5m}
    assert cfg_minutes.id not in {c.id for c in out_1d}, (
        "1d should not include a cfg with intervals=('5m',)")


def test_collect_overlay_configs_excludes_non_overlay_kinds(root, manager):
    """RSI / ATR / RVOL are NON-overlay (pane indicators) — must be excluded."""
    cfg_rsi = IndicatorConfig(
        kind_id="rsi", display_name="RSI(14)",
        params={"length": 14},
        scopes=frozenset({"main"}),
        visible=True,
    )
    manager.add(cfg_rsi)
    out = collect_overlay_configs(manager, "main", "1d")
    assert cfg_rsi.id not in {c.id for c in out}, (
        "RSI (non-overlay pane indicator) must NOT appear in the overlay legend")


# -------- Reposition (per-axes anchoring) ---------------------------------


class _FakeAxes:
    """Minimal axes stand-in: provides ``get_window_extent`` only."""

    def __init__(self, x0: int, y0: int, x1: int, y1: int):
        self._bbox = type("BBox", (), {"x0": x0, "y0": y0, "x1": x1, "y1": y1})()

    def get_window_extent(self):
        return self._bbox


def test_reposition_for_axes_hides_when_no_anchor(root, manager):
    legend = OverlayLegend(root, manager=manager,
                           theme={"text": "#fff", "spine": "#888"})
    legend.refresh([_make_sma_cfg()])  # legacy auto-place
    assert legend._placed is True
    legend.reposition_for_axes(None, root)
    assert legend._placed is False, (
        "passing ax=None must hide the legend so a vanished axes "
        "doesn't leave a stray strip behind")


def test_reposition_for_axes_hides_when_empty_rows(root, manager):
    legend = OverlayLegend(root, manager=manager,
                           theme={"text": "#fff", "spine": "#888"})
    legend.refresh([])  # legend is empty
    legend.reposition_for_axes(_FakeAxes(0, 0, 100, 100), root)
    assert legend._placed is False, (
        "reposition must not show an empty legend even with a valid axes")


def test_reposition_for_axes_records_anchor(root, manager):
    legend = OverlayLegend(root, manager=manager,
                           theme={"text": "#fff", "spine": "#888"})
    cfg = _make_sma_cfg()
    manager.add(cfg)
    legend.refresh([cfg])
    fake_ax = _FakeAxes(0, 0, 100, 100)
    # The Tk canvas may not be laid out in headless test runs — the
    # method should silently no-op rather than crash. We still expect
    # the anchor to be recorded so a subsequent draw_event picks it up.
    legend.reposition_for_axes(fake_ax, root)
    assert legend._anchor_ax is fake_ax, (
        "reposition must remember the anchor axes for subsequent "
        "refreshes / draw events")


def test_refresh_skips_legacy_placement_when_anchored(root, manager):
    """When ``_anchor_ax`` is set, ``refresh()`` does NOT auto-place.

    Placement is delegated to ``reposition_for_axes``. This avoids the
    legend briefly flashing in the top-right corner before the next
    draw_event snaps it under its axes.
    """
    legend = OverlayLegend(root, manager=manager,
                           theme={"text": "#fff", "spine": "#888"})
    legend._anchor_ax = _FakeAxes(0, 0, 100, 100)
    cfg = _make_sma_cfg()
    manager.add(cfg)
    legend.refresh([cfg])
    assert legend._placed is False, (
        "anchored legends should not auto-place from refresh(); "
        "the next reposition_for_axes call handles placement")


# -------- Double-click + right-click callbacks ---------------------------


def _row_descendants(row_frame):
    """Return [row_frame, swatch, label] — the three Tk widgets the
    legend wires double-click / right-click bindings to."""
    out = [row_frame]
    for w in row_frame.winfo_children():
        cls = w.__class__.__name__
        if cls in ("Frame", "Label", "TLabel"):
            out.append(w)
    return out


def test_legend_dblclick_callback_fires_with_config_id(root, manager):
    """A `<Double-Button-1>` binding on a row routes to
    ``on_row_dblclick(config_id)`` exactly once."""
    received: List[int] = []
    legend = OverlayLegend(
        root, manager=manager,
        theme={"text": "#fff", "spine": "#888"},
        on_row_dblclick=lambda cid: received.append(cid),
    )
    cfg = _make_sma_cfg()
    manager.add(cfg)
    legend.refresh([cfg])
    row_frame = legend._rows[0]
    # The Tk event_generate API rejects "<Double-Button-1>" directly
    # ("Double, Triple, or Quadruple modifier not allowed.") so we
    # verify the binding is wired AND fire the dispatch helper that
    # the binding closure invokes.
    assert row_frame.bind("<Double-Button-1>") != "", (
        "row container must have a <Double-Button-1> binding")
    legend._fire_dblclick(cfg.id)
    assert received == [cfg.id], (
        f"expected one dblclick with config_id={cfg.id}, got {received!r}")


def test_legend_dblclick_no_callback_is_silent(root, manager):
    """If ``on_row_dblclick`` is None (legacy path), the legend must
    not install any binding nor a hand cursor."""
    legend = OverlayLegend(root, manager=manager,
                           theme={"text": "#fff", "spine": "#888"})
    cfg = _make_sma_cfg()
    manager.add(cfg)
    legend.refresh([cfg])
    row_frame = legend._rows[0]
    # No binding for <Double-Button-1>.
    assert row_frame.bind("<Double-Button-1>") == "", (
        "legacy legend (no callback) must not bind <Double-Button-1>")
    # Cursor should NOT be hand2 — it's the legacy default.
    cursor = str(row_frame.cget("cursor") or "")
    assert cursor != "hand2", (
        f"legacy legend must not set hand2 cursor on rows; got {cursor!r}")


def test_legend_dblclick_sets_hand_cursor(root, manager):
    """With a dblclick callback wired, the row signals interactivity
    via the ``hand2`` cursor on the row/swatch/label surfaces."""
    legend = OverlayLegend(
        root, manager=manager,
        theme={"text": "#fff", "spine": "#888"},
        on_row_dblclick=lambda _cid: None,
    )
    cfg = _make_sma_cfg()
    manager.add(cfg)
    legend.refresh([cfg])
    row_frame = legend._rows[0]
    cursor = str(row_frame.cget("cursor") or "")
    assert cursor == "hand2", (
        f"row container cursor should be hand2 when callback wired; "
        f"got {cursor!r}")


def test_legend_context_menu_callback_fires_with_screen_coords(root, manager):
    """A `<Button-3>` (right-click) on a row invokes
    ``on_row_context_menu(config_id, x_root, y_root)``."""
    received: List[tuple] = []
    legend = OverlayLegend(
        root, manager=manager,
        theme={"text": "#fff", "spine": "#888"},
        on_row_context_menu=lambda cid, x, y: received.append((cid, x, y)),
    )
    cfg = _make_sma_cfg()
    manager.add(cfg)
    legend.refresh([cfg])
    row_frame = legend._rows[0]
    # Verify the binding is wired AND the dispatch helper invokes
    # the user callback with screen coords. (Tk's event_generate uses
    # ``-rootx`` / ``-rooty`` for screen coords; we exercise the
    # internal dispatcher directly to keep the test deterministic
    # across CI environments where event_generate is flaky.)
    assert row_frame.bind("<Button-3>") != "", (
        "row container must have a <Button-3> binding")
    legend._fire_context_menu(cfg.id, 100, 200)
    assert received == [(cfg.id, 100, 200)]


def test_legend_dblclick_callback_exception_swallowed(root, manager):
    """A broken host callback must not propagate out of the legend
    (would leave the chart in an unusable state)."""
    legend = OverlayLegend(
        root, manager=manager,
        theme={"text": "#fff", "spine": "#888"},
        on_row_dblclick=lambda _cid: (_ for _ in ()).throw(
            RuntimeError("boom")),
    )
    cfg = _make_sma_cfg()
    manager.add(cfg)
    legend.refresh([cfg])
    # Should not raise.
    legend._fire_dblclick(cfg.id)


def test_legend_eye_button_does_not_get_dblclick_binding(root, manager):
    """The eye-toggle button keeps its single-click toggle semantics;
    binding <Double-Button-1> on it as well would fire both the
    toggle AND the popup, which is jarring."""
    legend = OverlayLegend(
        root, manager=manager,
        theme={"text": "#fff", "spine": "#888"},
        on_row_dblclick=lambda _cid: None,
    )
    cfg = _make_sma_cfg()
    manager.add(cfg)
    legend.refresh([cfg])
    btn = legend._buttons_by_id[cfg.id]
    assert btn.bind("<Double-Button-1>") == "", (
        "eye toggle button must NOT have <Double-Button-1> bound "
        "(would clash with single-click toggle)")
