"""Pin DrawingDialog's color-swatch border to follow the active theme.

Audit finding ``tk-frame-swatch-theme`` (1 of 75): the colour swatch
inside :class:`gui.drawing_dialog.DrawingDialog` was a plain
:class:`tkinter.Frame` with a hardcoded ``highlightbackground="#888888"``.
The ring rendered as a mid-grey hoop regardless of light/dark mode —
fine on a pale window background, but visually jarring against the
dark-mode ``#1e1e1e`` window background.

Fix: a new ``DrawingDialog._apply_theme`` method reads
``self._app._theme`` and configures the swatch's
``highlightbackground`` to ``theme["grid"]``. ``ChartApp._apply_theme``
now cascades into ``self._drawing_dialogs.values()`` so live light↔dark
toggles propagate.

This test exercises both halves:

1. Open a dialog with a stub parent owning a known ``_theme`` dict;
   the swatch border must match ``theme["grid"]`` immediately.
2. Swap the parent's ``_theme`` to the other palette and call
   ``_apply_theme()`` directly; the swatch border must repaint.
3. The swatch is tagged ``_no_theme = True`` so the walker never
   trashes its background colour (which IS the data).
"""

from __future__ import annotations

import tkinter as tk
from types import SimpleNamespace
from typing import Any

import pytest

from tradinglab.drawings.model import make_hline_drawing
from tradinglab.drawings.store import DrawingStore
from tradinglab.gui.drawing_dialog import DrawingDialog

_LIGHT = {
    "win_bg": "#f0f0f0",
    "ax_bg": "#ffffff",
    "text": "#111111",
    "grid": "#cccccc",
}

_DARK = {
    "win_bg": "#1e1e1e",
    "ax_bg": "#2b2b2b",
    "text": "#dcdcdc",
    "grid": "#444444",
}


@pytest.fixture()
def root_with_store():
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        root.geometry("100x100-3000-3000")
    except tk.TclError:
        pass
    try:
        root.withdraw()
    except tk.TclError:
        pass
    # Stub the ChartApp-style ``_theme`` attribute so the dialog
    # can find a palette to apply.
    root._theme = dict(_LIGHT)  # type: ignore[attr-defined]
    store = DrawingStore(autosave=False)
    try:
        yield root, store
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


def _make_dialog(root: tk.Tk, store: DrawingStore) -> DrawingDialog:
    drawing = make_hline_drawing(
        ticker="AAPL", price=150.0, color="#2962ff",
    )
    store.add(drawing)
    try:
        return DrawingDialog(root, store=store, drawing=drawing)
    except tk.TclError as exc:  # pragma: no cover - Tcl init flake
        pytest.skip(f"Tk widget creation failed: {exc}")


def test_swatch_border_follows_theme_on_open(root_with_store) -> None:
    root, store = root_with_store
    root._theme = dict(_DARK)  # type: ignore[attr-defined]
    dlg = _make_dialog(root, store)
    try:
        actual = str(dlg._color_swatch.cget("highlightbackground"))
        # Tk accepts both ``#444444`` and ``#444`` shorthand. Compare
        # case-insensitively too (the colour came from the theme dict
        # so it should match exactly, but stay lenient).
        assert actual.lower() == _DARK["grid"], (
            f"swatch border in dark mode must equal theme['grid'] "
            f"({_DARK['grid']!r}); got {actual!r}"
        )
    finally:
        dlg.destroy()


def test_swatch_border_repaints_on_theme_toggle(root_with_store) -> None:
    root, store = root_with_store
    root._theme = dict(_LIGHT)  # type: ignore[attr-defined]
    dlg = _make_dialog(root, store)
    try:
        light_actual = str(dlg._color_swatch.cget("highlightbackground"))
        assert light_actual.lower() == _LIGHT["grid"]
        # Simulate a live light→dark toggle: swap the parent's palette
        # and re-invoke ``_apply_theme`` (this is exactly what
        # ChartApp._apply_theme does in the cascade loop).
        root._theme = dict(_DARK)  # type: ignore[attr-defined]
        dlg._apply_theme()
        dark_actual = str(dlg._color_swatch.cget("highlightbackground"))
        assert dark_actual.lower() == _DARK["grid"], (
            f"swatch border must repaint to theme['grid'] ({_DARK['grid']!r}) "
            f"on theme toggle; got {dark_actual!r}"
        )
    finally:
        dlg.destroy()


def test_swatch_no_theme_tag_preserves_data_background(root_with_store) -> None:
    """The walker in ``_apply_theme`` MUST skip the swatch (its
    ``background`` IS the drawing color). Verify the swatch carries
    the ``_no_theme = True`` tag and that its background survives
    a theme apply round-trip."""
    root, store = root_with_store
    dlg = _make_dialog(root, store)
    try:
        assert getattr(dlg._color_swatch, "_no_theme", False) is True, (
            "swatch must carry ``_no_theme = True`` so the theme walker "
            "doesn't repaint its background (which represents the user's "
            "drawing colour)."
        )
        data_bg_before = str(dlg._color_swatch.cget("background"))
        # Switch theme + re-apply.
        root._theme = dict(_DARK)  # type: ignore[attr-defined]
        dlg._apply_theme()
        data_bg_after = str(dlg._color_swatch.cget("background"))
        assert data_bg_before == data_bg_after, (
            "swatch background must NOT be touched by _apply_theme; "
            f"was {data_bg_before!r}, became {data_bg_after!r}"
        )
    finally:
        dlg.destroy()


def test_apply_theme_swallows_missing_parent_theme(root_with_store) -> None:
    """When the parent has no ``_theme`` attribute (defensive: stale
    or never-themed parent), ``_apply_theme`` is a silent no-op —
    NOT a crash that takes the dialog with it."""
    root, store = root_with_store
    if hasattr(root, "_theme"):
        delattr(root, "_theme")  # type: ignore[arg-type]
    dlg = _make_dialog(root, store)
    try:
        # Must not raise even with no palette.
        dlg._apply_theme()
    finally:
        dlg.destroy()


def test_chartapp_cascade_includes_drawing_dialogs() -> None:
    """Source-level pin: ``ChartApp._apply_theme`` must cascade into
    ``self._drawing_dialogs.values()`` so live light↔dark toggles
    propagate. Without this, the dialog stays out of sync until the
    user re-opens it."""
    from pathlib import Path

    app_py = (
        Path(__file__).resolve().parents[3]
        / "src" / "tradinglab" / "app.py"
    )
    src = app_py.read_text(encoding="utf-8")
    needle = '_drawing_dialogs'
    # The needle must appear inside ``_apply_theme`` — we use a
    # cheap textual proximity check: find the def then scan ahead.
    def_idx = src.index('def _apply_theme(')
    # Scan forward up to the next ``def `` boundary (excluding nested
    # closures, which `def _walk(...)` would be — but those don't
    # appear in app.py's _apply_theme today).
    next_def = src.index('\n    def ', def_idx + 1)
    body = src[def_idx:next_def]
    assert needle in body, (
        "tk-frame-swatch-theme regression: ChartApp._apply_theme no "
        "longer cascades into self._drawing_dialogs. Light↔dark "
        "toggles will silently skip the drawing-edit dialogs."
    )


# Silence the unused-import warning if pytest decides to skip the
# Tk-requiring tests in CI.
_ = (SimpleNamespace, Any)
