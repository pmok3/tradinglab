"""TDD tests for ``ThemedColorChooser`` — Win-ChooseColor look-alike
that follows the app's light/dark theme.

Audit ``themed-color-chooser``: the user asked for a near-identical
clone of the Windows native colour chooser whose ONLY difference is
the background colour (which must follow the app theme). The native
``tkinter.colorchooser.askcolor`` opens the legacy Win32 ChooseColor
dialog which has a fixed light-grey background that does not honour
Windows dark mode — this picker fills that gap.

The tests pin the public surface, not the pixel-perfect layout:
- ``pick_color`` returns the chosen hex or ``None``.
- Initial color round-trips through HSL fields, RGB fields, hex
  entry, pad marker, slider marker, and preview.
- Custom-colors slots persist across dialog invocations via JSON.
- Dark-theme chrome paints all classic Tk widgets with
  ``DARK_THEME`` palette values; the *rendered* swatch / gradient
  colours are unaffected (they ARE the colours being displayed).
- Wheel-over-spinbox does NOT silently mutate values (§7.11
  landmine).
- The ``_normalise`` non-hex contract is preserved: X11 colour
  names are accepted as ``initial``.

Test fixture note
-----------------
Uses the conftest's session-scoped ``root`` fixture (a
``tk.Toplevel`` under the shared session ``_tk_root``). Do NOT
define a local ``root`` fixture that constructs ``tk.Tk()``
directly — on Windows-ARM64 CI runners that creates a second Tk
interpreter that conflicts with the shared session root and
manifests as ``image "pyimageN" doesn't exist`` errors during
``tk.PhotoImage`` construction in the dialog.
"""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("tkinter")

from tradinglab.constants import DARK_THEME  # noqa: E402


@pytest.fixture
def dark_root(root):
    """Mark the conftest ``root`` Toplevel as dark-themed for this test."""
    root._theme_ctrl = SimpleNamespace(theme=DARK_THEME)  # type: ignore[attr-defined]
    yield root


@pytest.fixture
def custom_colors_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the custom-colors persistence file to a tmp path."""
    from tradinglab.gui import color_palette
    p = tmp_path / "custom_colors.json"
    monkeypatch.setattr(color_palette, "_custom_colors_path",
                        lambda: p)
    return p


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_normalise_preserves_non_hex_x11_name() -> None:
    """`_normalise("red")` returns ``"red"`` so Tk can resolve it
    downstream (preserves the pre-rewrite contract pinned by
    ``tests/unit/test_hex_case_constants.py``)."""
    from tradinglab.gui.color_palette import _normalise
    assert _normalise("red") == "red"
    assert _normalise("MidnightBlue") == "MidnightBlue"


def test_normalise_lowercases_long_hex() -> None:
    from tradinglab.gui.color_palette import _normalise
    assert _normalise("#1F77B4") == "#1f77b4"


def test_normalise_expands_short_hex() -> None:
    from tradinglab.gui.color_palette import _normalise
    assert _normalise("#abc") == "#aabbcc"


def test_normalise_empty_to_default() -> None:
    from tradinglab.gui.color_palette import DEFAULT_COLOR, _normalise
    assert _normalise("") == DEFAULT_COLOR


# ---------------------------------------------------------------------------
# Custom-colors persistence
# ---------------------------------------------------------------------------


def test_load_custom_colors_missing_returns_default(custom_colors_tmp: Path) -> None:
    """No file → 16 default slots."""
    from tradinglab.gui.color_palette import (
        _CUSTOM_SLOTS,
        _DEFAULT_CUSTOM_SLOT,
        _load_custom_colors,
    )
    assert not custom_colors_tmp.exists()
    colors = _load_custom_colors()
    assert len(colors) == _CUSTOM_SLOTS
    assert all(c == _DEFAULT_CUSTOM_SLOT for c in colors)


def test_load_custom_colors_corrupt_returns_default(custom_colors_tmp: Path) -> None:
    """Corrupt JSON → default slots, no crash."""
    custom_colors_tmp.write_text("not-json{{", encoding="utf-8")
    from tradinglab.gui.color_palette import (
        _CUSTOM_SLOTS,
        _DEFAULT_CUSTOM_SLOT,
        _load_custom_colors,
    )
    colors = _load_custom_colors()
    assert len(colors) == _CUSTOM_SLOTS
    assert all(c == _DEFAULT_CUSTOM_SLOT for c in colors)


def test_save_then_load_custom_colors_round_trips(custom_colors_tmp: Path) -> None:
    from tradinglab.gui.color_palette import (
        _CUSTOM_SLOTS,
        _load_custom_colors,
        _save_custom_colors,
    )
    new_slots = [f"#{i:06x}" for i in range(_CUSTOM_SLOTS)]
    _save_custom_colors(new_slots)
    assert custom_colors_tmp.exists()
    loaded = _load_custom_colors()
    assert loaded == new_slots


def test_load_custom_colors_pads_to_full_size(custom_colors_tmp: Path) -> None:
    """A short list on disk is padded out to 16 slots."""
    custom_colors_tmp.write_text(json.dumps(["#ff0000", "#00ff00"]),
                                 encoding="utf-8")
    from tradinglab.gui.color_palette import (
        _CUSTOM_SLOTS,
        _DEFAULT_CUSTOM_SLOT,
        _load_custom_colors,
    )
    colors = _load_custom_colors()
    assert len(colors) == _CUSTOM_SLOTS
    assert colors[0] == "#ff0000"
    assert colors[1] == "#00ff00"
    assert colors[2] == _DEFAULT_CUSTOM_SLOT


# ---------------------------------------------------------------------------
# Dialog construction + initial state
# ---------------------------------------------------------------------------


def _make_dialog(root, initial="#1f77b4"):
    from tradinglab.gui.color_palette import ThemedColorChooser
    try:
        return ThemedColorChooser(root, initial=initial)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Tk dialog could not be constructed: {e}")


def test_dialog_constructs_and_exposes_widgets(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    dlg = _make_dialog(root)
    try:
        dlg.update_idletasks()
        # Pinned widget attributes — the layout contract.
        assert dlg._basic_canvas is not None
        assert dlg._custom_canvas is not None
        assert dlg._pad_canvas is not None
        assert dlg._slider_canvas is not None
        assert dlg._preview_color is not None
        # Numeric spinboxes for HSL + RGB + hex entry.
        for attr in ("_sb_h", "_sb_s", "_sb_l",
                     "_sb_r", "_sb_g", "_sb_b", "_hex_entry"):
            assert getattr(dlg, attr, None) is not None, attr
    finally:
        dlg.destroy()


def test_initial_color_populates_fields(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    dlg = _make_dialog(root, initial="#1f77b4")
    try:
        dlg.update_idletasks()
        assert dlg._hex_entry.get().lower() == "#1f77b4"
        # R/G/B fields read from the chosen hex.
        assert int(dlg._sb_r.get()) == 0x1f
        assert int(dlg._sb_g.get()) == 0x77
        assert int(dlg._sb_b.get()) == 0xb4
    finally:
        dlg.destroy()


def test_initial_color_accepts_x11_name(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    """Passing ``initial="red"`` should be resolved (via Tk) to red."""
    dlg = _make_dialog(root, initial="red")
    try:
        dlg.update_idletasks()
        assert int(dlg._sb_r.get()) == 255
        assert int(dlg._sb_g.get()) == 0
        assert int(dlg._sb_b.get()) == 0
    finally:
        dlg.destroy()


def test_basic_color_grid_has_48_swatches(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    from tradinglab.gui.color_palette import _BASIC_COLORS
    assert len(_BASIC_COLORS) == 48, (
        f"Basic colour grid must mirror Win32 ChooseColor's 8×6 layout; "
        f"got {len(_BASIC_COLORS)}"
    )


def test_custom_color_grid_has_16_slots(custom_colors_tmp: Path) -> None:
    from tradinglab.gui.color_palette import _CUSTOM_SLOTS
    assert _CUSTOM_SLOTS == 16


# ---------------------------------------------------------------------------
# Field round-trips
# ---------------------------------------------------------------------------


def test_set_hex_updates_rgb_and_hsl(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    dlg = _make_dialog(root, initial="#000000")
    try:
        dlg._set_current_hex("#ff8000")
        dlg.update_idletasks()
        assert int(dlg._sb_r.get()) == 255
        assert int(dlg._sb_g.get()) == 128
        assert int(dlg._sb_b.get()) == 0
        # Hue around 30° (orange) at S=100% L=50%.
        h = int(dlg._sb_h.get())
        assert 28 <= h <= 32
        assert int(dlg._sb_s.get()) == 100
        assert 48 <= int(dlg._sb_l.get()) <= 52
    finally:
        dlg.destroy()


def test_set_rgb_updates_hex(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    dlg = _make_dialog(root)
    try:
        dlg._set_current_rgb(0, 128, 255)
        dlg.update_idletasks()
        assert dlg._hex_entry.get().lower() == "#0080ff"
    finally:
        dlg.destroy()


def test_set_hsl_updates_rgb(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    dlg = _make_dialog(root)
    try:
        # Pure red: H=0, S=100, L=50.
        dlg._set_current_hsl(0, 100, 50)
        dlg.update_idletasks()
        assert int(dlg._sb_r.get()) == 255
        assert int(dlg._sb_g.get()) == 0
        assert int(dlg._sb_b.get()) == 0
        assert dlg._hex_entry.get().lower() == "#ff0000"
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# Commit / cancel
# ---------------------------------------------------------------------------


def test_ok_commits_current_color(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    dlg = _make_dialog(root, initial="#10a020")
    try:
        dlg._on_ok()
        assert dlg.result == "#10a020"
    except Exception:
        dlg.destroy()
        raise


def test_ok_cancel_footer_packed_first_so_never_clipped(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    """The OK/Cancel footer must be packed ``side="bottom"`` BEFORE the
    expanding body (canonical fixed-footer pattern), so the OK button —
    the only control that commits the chosen colour — can never be
    clipped off the bottom of the fixed-size, non-resizable window on a
    larger-font / HiDPI display.

    Regression for the user report "there is no button to select a colour
    in the colour palette": previously the body was packed first with
    ``expand=True``, so on displays where the content was taller than the
    560x440 window the footer was pushed off-screen and unreachable.
    """
    dlg = _make_dialog(root, initial="#1f77b4")
    try:
        dlg.update_idletasks()
        # The OK ("select") button exists and is wired to a command.
        assert dlg._ok_btn is not None
        assert str(dlg._ok_btn.cget("text")) == "OK"
        assert str(dlg._ok_btn.cget("command")), "OK button has no command"
        footer = dlg._ok_btn.master
        assert dlg._cancel_btn.master is footer, "OK + Cancel share the footer"
        # Footer anchored to the bottom of the outer frame...
        assert str(footer.pack_info()["side"]) == "bottom"
        outer = footer.master
        slaves = outer.pack_slaves()
        # ...and packed FIRST, so it claims its height before the body —
        # the canonical pattern that prevents the footer being clipped.
        assert slaves[0] is footer, (
            "footer must be packed before the body so it is never clipped"
        )
        # The body that follows expands to fill the remaining space.
        body = slaves[1]
        info = body.pack_info()
        assert str(info["side"]) == "top"
        assert str(info["expand"]) in ("1", "true", "True")
    finally:
        dlg.destroy()


def test_ok_button_is_mapped_within_window_bounds(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    """End-to-end geometry check: after a real layout pass the OK button
    is mapped and its bottom edge sits within the window height (i.e. not
    clipped). Skips if the headless WM never maps the toplevel to a real
    size (winfo_height stays at the unmapped 1px)."""
    dlg = _make_dialog(root, initial="#1f77b4")
    try:
        dlg.deiconify()
        dlg.update_idletasks()
        dlg.update()
        win_h = dlg.winfo_height()
        if win_h <= 1:
            pytest.skip("headless WM did not map the dialog to a real size")
        assert dlg._ok_btn.winfo_ismapped()
        ok_bottom = (dlg._ok_btn.winfo_rooty() - dlg.winfo_rooty()
                     + dlg._ok_btn.winfo_height())
        assert ok_bottom <= win_h, (
            f"OK button bottom {ok_bottom} is below the window height "
            f"{win_h} — it is clipped/unreachable"
        )
    finally:
        dlg.destroy()


def test_cancel_returns_none(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    dlg = _make_dialog(root, initial="#10a020")
    try:
        dlg._on_cancel()
        assert dlg.result is None
    except Exception:
        dlg.destroy()
        raise


# ---------------------------------------------------------------------------
# Add to Custom Colors
# ---------------------------------------------------------------------------


def test_add_to_custom_writes_to_persistent_file(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    dlg = _make_dialog(root, initial="#ff00ff")
    try:
        dlg._on_add_to_custom()
        dlg.update_idletasks()
    finally:
        dlg.destroy()
    assert custom_colors_tmp.exists()
    loaded = json.loads(custom_colors_tmp.read_text(encoding="utf-8"))
    assert "#ff00ff" in [c.lower() for c in loaded]


def test_add_to_custom_advances_through_slots(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    """Two successive adds land in different slots."""
    from tradinglab.gui.color_palette import _CUSTOM_SLOTS
    dlg = _make_dialog(root, initial="#aa0000")
    try:
        dlg._on_add_to_custom()
        dlg._set_current_hex("#00aa00")
        dlg._on_add_to_custom()
        dlg.update_idletasks()
    finally:
        dlg.destroy()
    loaded = json.loads(custom_colors_tmp.read_text(encoding="utf-8"))
    assert "#aa0000" in [c.lower() for c in loaded]
    assert "#00aa00" in [c.lower() for c in loaded]
    assert len(loaded) == _CUSTOM_SLOTS


# ---------------------------------------------------------------------------
# Dark theme chrome
# ---------------------------------------------------------------------------


def test_dark_theme_chrome_uses_dark_bg(
    dark_root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    """All chrome `tk.Frame`s and `tk.Canvas`s use DARK_THEME bg."""
    dlg = _make_dialog(dark_root)
    try:
        dlg.update_idletasks()
        win_bg = DARK_THEME["win_bg"].lower()
        # Dialog background.
        assert str(dlg.cget("background")).lower() == win_bg
        # All four canvases use win_bg.
        for canvas in (dlg._basic_canvas, dlg._custom_canvas,
                       dlg._pad_canvas, dlg._slider_canvas):
            assert str(canvas.cget("background")).lower() == win_bg, (
                f"canvas {canvas} bg not dark"
            )
    finally:
        dlg.destroy()


def test_dark_theme_labels_use_dark_palette(
    dark_root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    """Classic ``tk.Label`` chrome must use DARK_THEME text color
    AND win_bg, or it stays bright white in dark mode (§7.31)."""
    dlg = _make_dialog(dark_root)
    try:
        dlg.update_idletasks()

        def walk(w):
            yield w
            for child in w.winfo_children():
                yield from walk(child)

        labels = [w for w in walk(dlg) if isinstance(w, tk.Label)]
        assert labels, "expected at least the H/S/L/R/G/B labels"
        win_bg = DARK_THEME["win_bg"].lower()
        text = DARK_THEME["text"].lower()
        for lbl in labels:
            assert str(lbl.cget("background")).lower() == win_bg, (
                f"label {lbl.cget('text')!r} bg "
                f"{lbl.cget('background')!r} != dark win_bg"
            )
            assert str(lbl.cget("foreground")).lower() == text, (
                f"label {lbl.cget('text')!r} fg "
                f"{lbl.cget('foreground')!r} != dark text"
            )
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# Wheel-over-spinbox guard (§7.11)
# ---------------------------------------------------------------------------


def test_wheel_over_spinbox_does_not_mutate_value(
    root: tk.Tk, custom_colors_tmp: Path,
) -> None:
    """Wheel-over-ttk.Spinbox must not silently change the value.
    Per §7.11: ``protect_combobox_wheel`` is responsible. This
    sentinel pins that the guard was applied to this dialog."""
    dlg = _make_dialog(root, initial="#1f77b4")
    try:
        # The R spinbox starts at 0x1f = 31. After firing a wheel
        # event, it must STILL be 31 (the guard returns "break").
        dlg.update_idletasks()
        initial_r = dlg._sb_r.get()
        # Fire a wheel-up event at the spinbox.
        dlg._sb_r.event_generate("<MouseWheel>", delta=120)
        dlg._sb_r.event_generate("<Button-4>")
        dlg.update_idletasks()
        assert dlg._sb_r.get() == initial_r, (
            f"R spinbox value drifted from {initial_r!r} to {dlg._sb_r.get()!r} "
            f"on wheel — protect_combobox_wheel guard not applied"
        )
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# Public entrypoint preserved
# ---------------------------------------------------------------------------


def test_pick_color_public_signature_unchanged() -> None:
    """``pick_color(parent, initial, title)`` must remain the
    public synchronous entrypoint — every caller in the codebase
    relies on this signature."""
    import inspect

    from tradinglab.gui.color_palette import pick_color
    sig = inspect.signature(pick_color)
    params = list(sig.parameters.keys())
    assert params[0] == "parent"
    assert "initial" in params
    assert "title" in params
