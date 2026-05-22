"""Unit tests for ``ChartStackPanel.apply_theme`` (dark-mode plumbing).

User-reported bug: ChartStack didn't pick up dark-mode colors when
the rest of the app flipped to dark. Root cause: the panel's
``apply_theme`` hardcoded ``#1e1e1e`` / ``#ffffff`` (ignoring the
resolved palette dict the rest of the app uses) AND drew card text
without a color so every re-render reset the symbol/placeholder
artists to matplotlib's default black, leaking through on top of
the dark background.

These tests guard:

1. The panel accepts the resolved palette dict and stashes it on
   ``self._theme_palette`` for downstream draw calls.
2. The figure patch + axes facecolors adopt the palette's
   ``fig_bg`` / ``ax_bg``.
3. Card text artists adopt the palette's ``text`` color.
4. String-mode inputs (legacy ``"dark"`` / ``"light"``) still work.
5. Re-rendering a card after ``apply_theme`` honors the stored
   palette (text doesn't revert to black).
"""
from __future__ import annotations

import os

os.environ.setdefault("MPLBACKEND", "Agg")

import pytest


def _make_panel(root):
    """Build a bare ChartStackPanel with no owner (M1 unit-test path)."""
    from tradinglab.gui.chartstack import ChartStackPanel
    panel = ChartStackPanel(root, owner=None)
    return panel


def _destroy(panel):
    try:
        panel.destroy()
    except Exception:  # noqa: BLE001
        pass


def test_apply_theme_accepts_resolved_palette_dict(root):
    """The dict path is the new primary entry from ``ChartApp._apply_theme``."""
    panel = _make_panel(root)
    try:
        palette = {
            "fig_bg": "#101010",
            "ax_bg": "#202020",
            "text": "#eeeeee",
            "win_bg": "#101010",
        }
        panel.apply_theme(palette)
        assert panel._theme_palette is not None
        assert panel._theme_palette["text"] == "#eeeeee"
        assert panel._theme_palette["ax_bg"] == "#202020"
    finally:
        _destroy(panel)


def test_apply_theme_dict_repaints_figure_and_axes_bg(root):
    panel = _make_panel(root)
    try:
        palette = {
            "fig_bg": "#101010",
            "ax_bg": "#202020",
            "text": "#eeeeee",
        }
        panel.apply_theme(palette)
        from matplotlib.colors import to_hex
        assert to_hex(panel._figure.patch.get_facecolor()).lower() == "#101010"
        for card in panel._cards:
            assert to_hex(card.ax.get_facecolor()).lower() == "#202020"
    finally:
        _destroy(panel)


def test_apply_theme_recolors_non_right_aligned_text_artists(root):
    """Symbol-header text adopts theme color; %chg label is untouched.

    The right-aligned %chg label encodes direction (bull / bear /
    flat) so the theme repaint must skip it. The left-aligned
    symbol label is theme-painted.
    """
    panel = _make_panel(root)
    try:
        # Force a card to draw a non-empty render so we have left + right text.
        from tradinglab.gui.chartstack.binding import CardBinding
        from tradinglab.gui.chartstack.series_cache import Bar
        from tradinglab.gui.chartstack.render import draw_card_candles
        bars = [Bar(ts=i, open=100.0 + i, high=101.0 + i, low=99.0 + i,
                    close=100.5 + i, volume=1000.0, session="regular")
                for i in range(5)]
        card = panel._cards[0]
        card.binding = CardBinding(symbol="AAPL", source_label="w")
        draw_card_candles(card.ax, bars, binding=card.binding)
        # Confirm pre-state: theme not yet applied, defaults present.
        left = [t for t in card.ax.texts if t.get_ha() == "left"]
        right = [t for t in card.ax.texts if t.get_ha() == "right"]
        assert left and right
        original_right_color = right[0].get_color()

        # Apply theme.
        panel.apply_theme({
            "fig_bg": "#101010",
            "ax_bg": "#202020",
            "text": "#eeeeee",
        })
        # Symbol header recolored to theme.
        assert left[0].get_color() == "#eeeeee"
        # Direction-encoded %chg label NOT touched.
        assert right[0].get_color() == original_right_color
    finally:
        _destroy(panel)


def test_apply_theme_string_mode_dark_resolves_via_constants(root):
    """Legacy ``"dark"`` input must still produce a dark palette.

    Backwards-compat for any external caller that hasn't migrated
    to passing a resolved dict.
    """
    panel = _make_panel(root)
    try:
        panel.apply_theme("dark")
        assert panel._theme_palette is not None
        # constants.DARK_THEME["fig_bg"] is "#1e1e1e".
        assert panel._theme_palette["fig_bg"].lower() == "#1e1e1e"
        # constants.DARK_THEME["text"] is "#dcdcdc".
        assert panel._theme_palette["text"].lower() == "#dcdcdc"
    finally:
        _destroy(panel)


def test_apply_theme_string_mode_light_resolves_via_constants(root):
    panel = _make_panel(root)
    try:
        panel.apply_theme("light")
        assert panel._theme_palette is not None
        assert panel._theme_palette["fig_bg"].lower() == "#fafafa"
        assert panel._theme_palette["text"].lower() == "#111111"
    finally:
        _destroy(panel)


def test_apply_theme_none_falls_back_to_light(root):
    panel = _make_panel(root)
    try:
        panel.apply_theme(None)
        # Light defaults.
        assert panel._theme_palette is not None
        assert panel._theme_palette["fig_bg"].lower() == "#fafafa"
    finally:
        _destroy(panel)


def test_apply_theme_legacy_dict_with_dark_key_resolves_dark(root):
    """A bare ``{"dark": True}`` still routes through resolve_theme.

    Some early callers used a tiny dict to flag mode. We preserve
    that semantic so old tests don't break.
    """
    panel = _make_panel(root)
    try:
        panel.apply_theme({"dark": True})
        # Resolves through constants to dark palette.
        assert panel._theme_palette is not None
        assert panel._theme_palette["fig_bg"].lower() == "#1e1e1e"
    finally:
        _destroy(panel)


def test_set_binding_picks_up_panel_theme_palette(root):
    """A subsequent ``card.set_binding`` must use the stored palette.

    This is the regression-guard for the bug — without the panel
    forwarding ``self._theme_palette`` into ``draw_card_placeholder``
    via ``card.set_binding``, the placeholder text would revert to
    default-black on every binding swap, leaking through on dark mode.
    """
    panel = _make_panel(root)
    try:
        from tradinglab.gui.chartstack.binding import CardBinding
        panel.apply_theme({
            "fig_bg": "#101010", "ax_bg": "#202020", "text": "#eeeeee",
        })
        card = panel._cards[0]
        # Triggers draw_card_placeholder via card.py.
        card.set_binding(CardBinding(symbol="NVDA", source_label="w"))
        assert card.ax.texts
        assert card.ax.texts[0].get_color() == "#eeeeee"
    finally:
        _destroy(panel)


def test_resolve_theme_palette_honors_owner_overrides(root):
    """Owner-side ``_theme_overrides`` flow through ``constants.resolve_theme``."""
    from tradinglab.gui.chartstack import ChartStackPanel

    class _OwnerStub:
        _theme_overrides = {"dark": {"win_bg": "#123456"}, "light": {}}

    owner = _OwnerStub()
    panel = ChartStackPanel(root, owner=owner)
    try:
        palette = panel._resolve_theme_palette("dark")
        # The override surfaces because resolve_theme merges it in.
        assert palette["win_bg"].lower() == "#123456"
    finally:
        _destroy(panel)


def test_apply_theme_does_not_raise_on_torn_down_canvas(root):
    """Idempotency / defensiveness — never raises even if canvas dies."""
    panel = _make_panel(root)
    try:
        # Break the canvas so anything internal that touches draw_idle
        # raises; apply_theme must still swallow the error.
        class _BrokenCanvas:
            def draw_idle(self):
                raise RuntimeError("canvas destroyed")
        panel._canvas = _BrokenCanvas()
        # Must not raise:
        panel.apply_theme({
            "fig_bg": "#101010", "ax_bg": "#202020", "text": "#eeeeee",
        })
    finally:
        _destroy(panel)
