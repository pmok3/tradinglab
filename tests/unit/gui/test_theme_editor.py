"""Unit tests for the Theme Editor Toplevel (big-bet item #7).

The dialog is tested with a lightweight stub-app pattern: we use a real
``tk.Tk`` root and dynamically attach the exact surface
``ThemeEditorDialog`` reaches into on the parent app
(``_theme_overrides``, ``dark_var``, ``set_theme_override``,
``clear_theme_overrides``, ``replace_theme_overrides``,
``_apply_theme``). The root must be a real Tk widget because
``tk.Toplevel(parent)`` calls internal Tcl methods (``parent.tk``,
``parent._w``) under the hood. This keeps the tests fast and isolated
from full :class:`ChartApp` startup.
"""

from __future__ import annotations

from typing import Dict

import pytest

tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")

from tradinglab.constants import CUSTOMIZABLE_THEME_KEYS, DEFAULT_THEMES
from tradinglab.gui.theme_editor import (
    _BLOOMBERG_DARK,
    _PRESETS,
    ThemeEditorDialog,
    open_theme_editor,
)


def _attach_stub_app_surface(root: tk.Tk) -> tk.Tk:
    """Decorate ``root`` with the ChartApp-like surface the dialog uses."""
    root._theme_overrides = {"light": {}, "dark": {}}
    root.dark_var = tk.BooleanVar(master=root, value=False)
    root.apply_count = 0

    def set_theme_override(mode, key, color):
        if mode not in ("light", "dark"):
            return
        if not isinstance(color, str) or not color:
            return
        allowed = {k for k, _ in CUSTOMIZABLE_THEME_KEYS}
        if key not in allowed:
            return
        root._theme_overrides.setdefault(mode, {})[key] = color

    def clear_theme_overrides(mode=None):
        if mode in ("light", "dark"):
            root._theme_overrides[mode] = {}
        else:
            root._theme_overrides = {"light": {}, "dark": {}}

    def replace_theme_overrides(overrides):
        root._theme_overrides = {
            "light": dict(overrides.get("light", {})),
            "dark":  dict(overrides.get("dark", {})),
        }

    def _apply_theme():
        root.apply_count += 1

    root.set_theme_override = set_theme_override
    root.clear_theme_overrides = clear_theme_overrides
    root.replace_theme_overrides = replace_theme_overrides
    root._apply_theme = _apply_theme
    return root


@pytest.fixture()
def stub_app():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        r.geometry("400x300-3000-3000")
    except tk.TclError:
        pass
    _attach_stub_app_surface(r)
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


def test_construction_builds_swatches_for_both_modes(stub_app):
    dlg = ThemeEditorDialog(stub_app)
    try:
        for mode in ("light", "dark"):
            for key, _label in CUSTOMIZABLE_THEME_KEYS:
                assert key in dlg._swatch_buttons[mode], (
                    f"{mode}.{key} swatch missing")
        assert len(dlg._swatch_buttons["light"]) == len(CUSTOMIZABLE_THEME_KEYS)
        assert len(dlg._swatch_buttons["dark"]) == len(CUSTOMIZABLE_THEME_KEYS)
    finally:
        dlg.destroy()


def test_initial_swatch_colors_reflect_overrides(stub_app):
    stub_app.set_theme_override("light", "win_bg", "#abcdef")
    dlg = ThemeEditorDialog(stub_app)
    try:
        btn = dlg._swatch_buttons["light"]["win_bg"]
        assert btn.cget("bg").lower() == "#abcdef"
        btn2 = dlg._swatch_buttons["dark"]["win_bg"]
        assert btn2.cget("bg").lower() == DEFAULT_THEMES["dark"]["win_bg"].lower()
    finally:
        dlg.destroy()


def test_current_color_falls_back_to_default(stub_app):
    dlg = ThemeEditorDialog(stub_app)
    try:
        for mode in ("light", "dark"):
            for key, _label in CUSTOMIZABLE_THEME_KEYS:
                assert dlg._current_color(mode, key) == DEFAULT_THEMES[mode][key]
    finally:
        dlg.destroy()


def test_refresh_swatches_repaints_after_external_change(stub_app):
    dlg = ThemeEditorDialog(stub_app)
    try:
        stub_app.set_theme_override("dark", "text", "#fedcba")
        dlg._refresh_swatches()
        assert dlg._swatch_buttons["dark"]["text"].cget("bg").lower() == "#fedcba"
    finally:
        dlg.destroy()


def test_preset_default_light_clears_light_overrides(stub_app):
    stub_app.set_theme_override("light", "text", "#111111")
    stub_app.set_theme_override("dark", "ax_bg", "#222222")
    stub_app.dark_var.set(True)
    dlg = ThemeEditorDialog(stub_app)
    try:
        dlg._on_apply_preset(0)
        assert stub_app._theme_overrides["light"] == {}
        assert stub_app._theme_overrides["dark"] == {"ax_bg": "#222222"}
        assert stub_app.dark_var.get() is False
    finally:
        dlg.destroy()


def test_preset_default_dark_switches_mode(stub_app):
    stub_app.set_theme_override("dark", "text", "#abc")
    stub_app.dark_var.set(False)
    dlg = ThemeEditorDialog(stub_app)
    try:
        dlg._on_apply_preset(1)
        assert stub_app._theme_overrides["dark"] == {}
        assert stub_app.dark_var.get() is True
    finally:
        dlg.destroy()


def test_bloomberg_preset_loads_amber_palette(stub_app):
    dlg = ThemeEditorDialog(stub_app)
    try:
        bloomberg_idx = next(
            i for i, p in enumerate(_PRESETS) if p[0] == "Bloomberg")
        dlg._on_apply_preset(bloomberg_idx)
        assert stub_app._theme_overrides["dark"] == _BLOOMBERG_DARK
        assert stub_app.dark_var.get() is True
        allowed = {k for k, _ in CUSTOMIZABLE_THEME_KEYS}
        for key in _BLOOMBERG_DARK:
            assert key in allowed, f"Bloomberg key {key!r} not in allow-list"
    finally:
        dlg.destroy()


def test_reset_clears_both_modes(stub_app):
    stub_app.set_theme_override("light", "win_bg", "#aaa")
    stub_app.set_theme_override("dark", "text", "#bbb")
    dlg = ThemeEditorDialog(stub_app)
    try:
        dlg._on_reset()
        assert stub_app._theme_overrides == {"light": {}, "dark": {}}
    finally:
        dlg.destroy()


def test_open_theme_editor_is_singleton(stub_app):
    dlg1 = open_theme_editor(stub_app)
    try:
        dlg2 = open_theme_editor(stub_app)
        assert dlg1 is dlg2, "second open must reuse the existing dialog"
    finally:
        dlg1.destroy()


def test_open_theme_editor_recreates_after_close(stub_app):
    dlg1 = open_theme_editor(stub_app)
    dlg1.destroy()
    try:
        stub_app.update()
    except tk.TclError:
        pass
    dlg2 = open_theme_editor(stub_app)
    try:
        assert dlg2 is not dlg1
        assert isinstance(dlg2, ThemeEditorDialog)
    finally:
        dlg2.destroy()


def test_preset_apply_calls_apply_theme(stub_app):
    dlg = ThemeEditorDialog(stub_app)
    try:
        before = stub_app.apply_count
        dlg._on_apply_preset(0)
        assert stub_app.apply_count > before, (
            "preset apply must trigger _apply_theme for live preview")
    finally:
        dlg.destroy()

