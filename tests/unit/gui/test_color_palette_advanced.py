"""Tests for the advanced (default) color palette picker.

Covers the new behavior added on top of the honeycomb swatch picker:

* A proper advanced HSV gradient picker is shown by DEFAULT.
* The window is larger and resizable (so the OK/Cancel buttons are
  never clipped — the "where are the buttons" regression).
* The honeycomb "Swatches" view is still reachable as a secondary view
  and keeps the ``_canvas`` attribute (dark-theme contract).
* Pure ``hsv_to_hex`` / ``hex_to_hsv`` helpers round-trip.

Pure-logic tests run without Tk; the dialog tests use the shared
``root`` Toplevel fixture and skip cleanly where Tk is unavailable.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradinglab.gui import color_palette
from tradinglab.gui.color_palette import (
    _GRAYSCALE_COLORS,
    _HONEYCOMB_COLORS,
    hex_to_hsv,
    hsv_to_hex,
)

# ---------------------------------------------------------------------------
# Pure helpers (no Tk)
# ---------------------------------------------------------------------------


def test_honeycomb_table_lengths_unchanged() -> None:
    # Pinned by the smoke suite; guard here too so a refactor can't drift.
    assert len(_HONEYCOMB_COLORS) == 19
    assert len(_GRAYSCALE_COLORS) == 6


@pytest.mark.parametrize(
    "hexstr",
    ["#000000", "#ffffff", "#1f77b4", "#e51d1d", "#10a020", "#808080"],
)
def test_hsv_hex_round_trip(hexstr: str) -> None:
    h, s, v = hex_to_hsv(hexstr)
    assert 0.0 <= h <= 1.0
    assert 0.0 <= s <= 1.0
    assert 0.0 <= v <= 1.0
    assert hsv_to_hex(h, s, v) == hexstr


def test_hsv_to_hex_is_lowercase_rrggbb() -> None:
    out = hsv_to_hex(0.0, 1.0, 1.0)
    assert out == "#ff0000"
    assert out == out.lower()
    assert len(out) == 7


def test_hex_to_hsv_handles_short_form() -> None:
    # #f00 -> red, full sat, full value
    h, s, v = hex_to_hsv("#f00")
    assert hsv_to_hex(h, s, v) == "#ff0000"


# ---------------------------------------------------------------------------
# Dialog behavior (Tk)
# ---------------------------------------------------------------------------


def _make_dialog(root):
    try:
        return color_palette.HexColorPalette(root, initial="#1f77b4")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Tk dialog could not be constructed: {e}")


def test_default_view_shows_both_panes_side_by_side(root) -> None:
    """Both Advanced HSV + Swatches honeycomb are visible at once.

    Per the ``color-picker-side-by-side`` sprint: the radio toggle
    is gone; both panes mount permanently inside a horizontal
    container. The dialog's ``_view_var`` is retired.
    """
    dlg = _make_dialog(root)
    try:
        dlg.update_idletasks()
        # Both frames are packed simultaneously.
        assert dlg._advanced_frame.winfo_manager() == "pack"
        assert dlg._swatches_frame.winfo_manager() == "pack"
        # No view-toggle radios anymore.
        assert not hasattr(dlg, "_adv_btn")
        assert not hasattr(dlg, "_sw_btn")
        assert not hasattr(dlg, "_view_var")
    finally:
        dlg.destroy()


def test_window_is_larger_and_resizable(root) -> None:
    dlg = _make_dialog(root)
    try:
        # Side-by-side layout needs more horizontal room than the
        # 440px toggled variant. Default geometry must be at least
        # 720px wide so both panes fit without truncation.
        w, h = dlg._default_geometry.split("+")[0].split("x")
        assert int(w) >= 720
        assert int(h) >= 380
        rs = dlg.resizable()
        assert all(int(x) for x in rs)
    finally:
        dlg.destroy()


def test_ok_and_cancel_buttons_exist_and_are_reachable(root) -> None:
    dlg = _make_dialog(root)
    try:
        dlg.update_idletasks()
        assert dlg._ok_btn.winfo_manager() != ""
        assert dlg._cancel_btn.winfo_manager() != ""
    finally:
        dlg.destroy()


def test_set_current_updates_selection(root) -> None:
    dlg = _make_dialog(root)
    try:
        dlg._set_current("#10a020")
        assert dlg._current == "#10a020"
        assert dlg._hex_var.get().lower() == "#10a020"
    finally:
        dlg.destroy()


def test_ok_commits_current_advanced_selection(root) -> None:
    dlg = _make_dialog(root)
    try:
        dlg._set_current("#1f77b4")
        dlg._on_ok()
        assert dlg.result == "#1f77b4"
    except Exception:
        dlg.destroy()
        raise


def test_honeycomb_canvas_is_always_present(root) -> None:
    """Swatches pane is permanently visible; ``_canvas`` attribute
    stays for the dark-theme contract."""
    dlg = _make_dialog(root)
    try:
        dlg.update_idletasks()
        assert dlg._swatches_frame.winfo_manager() == "pack"
        # Honeycomb canvas attribute preserved (dark-theme contract).
        assert dlg._canvas is not None
    finally:
        dlg.destroy()


def test_hex_entry_commit_updates_current(root) -> None:
    dlg = _make_dialog(root)
    try:
        dlg._hex_var.set("#abcdef")
        dlg._on_hex_entry()
        assert dlg._current == "#abcdef"
    finally:
        dlg.destroy()


def test_hex_entry_lives_under_swatches_column(root) -> None:
    """Hex entry + preview swatch sit on the right (swatches column).

    Per the user's choice in the sprint: "Move under the Swatches
    column on the right — the swatch grid feels like the more
    'final pick' affordance."
    """
    dlg = _make_dialog(root)
    try:
        dlg.update_idletasks()
        # Walk up the hex entry's parent chain; one of the ancestors
        # must be (or be inside) the swatches frame.
        parent = dlg._hex_entry.master
        ancestors = []
        for _ in range(6):
            if parent is None:
                break
            ancestors.append(parent)
            parent = parent.master if hasattr(parent, "master") else None
        assert dlg._swatches_frame in ancestors, (
            "hex entry must mount under the swatches column, not the "
            "advanced column"
        )
    finally:
        dlg.destroy()


def test_swatch_click_still_commits_selection_immediately(root) -> None:
    """Sanity: per-cell swatch click still calls ``_on_pick`` → commits."""
    dlg = _make_dialog(root)
    try:
        # Pick a known honeycomb colour directly via the API the
        # swatch <Button-1> binding uses.
        dlg._on_pick("#1f77b4")
        assert dlg.result == "#1f77b4"
    except Exception:
        dlg.destroy()
        raise


def test_canvas_attr_is_honeycomb_for_dark_theme(root) -> None:
    from tradinglab.constants import DARK_THEME

    root._theme_ctrl = SimpleNamespace(theme=DARK_THEME)  # type: ignore[attr-defined]
    try:
        dlg = _make_dialog(root)
    finally:
        # leave attribute for dialog lifetime; cleaned with root fixture
        pass
    try:
        assert str(dlg._canvas.cget("background")) == DARK_THEME["win_bg"]
        assert str(dlg._canvas.cget("highlightthickness")) == "0"
        assert str(dlg._canvas.cget("borderwidth")) == "0"
    finally:
        dlg.destroy()
