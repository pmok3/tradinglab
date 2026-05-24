"""Regression test: ``_SettingsDialog`` Combobox/Spinbox wheel-guard.

The Settings dialog wraps its body in a scrollable canvas and installs a
global ``canvas.bind_all("<MouseWheel>", _on_mousewheel)`` so the form
scrolls under the cursor. But ttk Combobox / Spinbox widgets consume
``<MouseWheel>`` natively and silently mutate their value on every wheel
tick (same hazard as the EMA 3/8 cross template corruption — see
``gui/_modal_base.py`` ``protect_combobox_wheel`` and CLAUDE.md §7.11).

Without the guard, a user scrolling the Settings dialog while the cursor
happened to pass over the worker-threads spinbox / UI-scale combobox /
startup-defaults combobox / watchlist-pin-cap spinbox would silently
mutate that setting and persist the corruption on Save.

This test builds the dialog headlessly with a minimal parent stub,
wheel-bombs every Combobox/Spinbox descendant, and asserts no value
changed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")

from ._wheel_guard_helpers import (
    snapshot_combobox_spinbox_values,
    wheel_bomb_all,
)


@pytest.fixture()
def parent_app():
    """Minimal Tk root with all attrs ``_SettingsDialog.__init__`` reads."""
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    # The dialog reads these directly on the parent — populate them with
    # values consistent with a freshly-initialised ChartApp.
    root._theme_overrides = {}  # type: ignore[attr-defined]
    root._startup_defaults = {}  # type: ignore[attr-defined]
    root._worker_count = 4  # type: ignore[attr-defined]
    root.dark_var = tk.BooleanVar(root, value=False)  # type: ignore[attr-defined]
    root.log_price_var = tk.BooleanVar(root, value=False)  # type: ignore[attr-defined]
    root._scroll_zoom_invert = False  # type: ignore[attr-defined]
    root._drawings_snap_to_ohlc = False  # type: ignore[attr-defined]
    root._ui_scale = 1.0  # type: ignore[attr-defined]
    root._display_tz = ""  # type: ignore[attr-defined]
    # Methods the dialog reads via getattr / direct call paths in __init__.
    # The post-init UI scale / colorblind / volume-tod toggles call back
    # into the parent; we stub them so callbacks fired during construction
    # don't blow up. (None are actually invoked from __init__ itself —
    # they fire on user interaction — but having stubs keeps cleanup safe.)
    root.set_ui_scale = lambda _v: None  # type: ignore[attr-defined]
    root.set_use_colorblind_palette = lambda _v: None  # type: ignore[attr-defined]
    root.set_volume_tod_enabled = lambda _v: None  # type: ignore[attr-defined]
    root.replace_theme_overrides = lambda _v: None  # type: ignore[attr-defined]
    root.replace_startup_defaults = lambda _v: None  # type: ignore[attr-defined]
    root.set_worker_count = lambda _v: None  # type: ignore[attr-defined]
    root.set_startup_default = lambda *_a, **_k: None  # type: ignore[attr-defined]
    root.set_display_tz = lambda _v: None  # type: ignore[attr-defined]
    root.set_scroll_zoom_invert = lambda _v: None  # type: ignore[attr-defined]
    root.set_drawings_snap_to_ohlc = lambda _v: None  # type: ignore[attr-defined]
    yield root
    try:
        root.update_idletasks()
        root.destroy()
    except tk.TclError:
        pass


def _open_settings(parent):
    from tradinglab.gui.dialogs import _SettingsDialog
    try:
        return _SettingsDialog(parent)
    except tk.TclError as exc:
        pytest.skip(f"_SettingsDialog could not open headlessly: {exc}")


def test_settings_dialog_has_combobox_or_spinbox_to_protect(parent_app):
    """Sanity: the dialog actually contains wheel-mutatable widgets.

    If a refactor strips every Combobox/Spinbox out of Settings, the
    wheel guard is no longer needed and this test (and the next one)
    can be retired. Until then this is the smoke that proves the guard
    has real surface area to protect.
    """
    dlg = _open_settings(parent_app)
    try:
        snap = snapshot_combobox_spinbox_values(dlg)
        # Performance spinbox + UI scale combobox + Display timezone
        # combobox + Watchlist pin-cap spinbox + Startup parameters
        # (interval / source / theme) comboboxes ⇒ at least 5 widgets.
        assert len(snap) >= 5, (
            f"expected ≥5 Combobox/Spinbox widgets in Settings; "
            f"found {len(snap)}: {snap}"
        )
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_settings_dialog_wheel_storm_does_not_mutate_values(parent_app):
    """8 wheel-down + 8 wheel-up over every Combobox/Spinbox must not
    silently rotate any value.

    Pre-fix this test would fail: the worker spinbox would tick from
    4 down to its lower bound and the UI scale combobox would walk
    through the available scales.
    """
    dlg = _open_settings(parent_app)
    try:
        parent_app.update_idletasks()
        before = snapshot_combobox_spinbox_values(dlg)
        assert before, "no Combobox/Spinbox widgets — test is vacuous"

        bombed = wheel_bomb_all(dlg, ticks=8)
        assert bombed == len(before), (
            f"walker mismatch: snapshot saw {len(before)} widgets, "
            f"bomber hit {bombed}"
        )

        after = snapshot_combobox_spinbox_values(dlg)
        assert after == before, (
            f"wheel-over silently mutated Settings values:\n"
            f"  before={before}\n"
            f"  after ={after}"
        )
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_settings_dialog_stashes_form_canvas(parent_app):
    """``_form_canvas`` is the host canvas the wheel-guard forwards to.

    Source-level invariant — without this attribute the guard either
    swallows wheel events (preventing scroll over a combobox from
    moving the form) or silently no-ops. Pinned so a future canvas
    refactor doesn't quietly orphan the guard.
    """
    dlg = _open_settings(parent_app)
    try:
        assert getattr(dlg, "_form_canvas", None) is not None, (
            "_SettingsDialog must stash the scrollable host canvas as "
            "_form_canvas so protect_combobox_wheel can forward wheel "
            "events into it."
        )
        # Defensive sanity: it really is a Tk Canvas, not just any widget.
        assert isinstance(dlg._form_canvas, tk.Canvas)
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


# Silence unused-import lint — SimpleNamespace is kept available for
# future stub expansion without churning the import block.
_ = SimpleNamespace
