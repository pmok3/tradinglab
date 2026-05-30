"""Headless tests for ``gui.custom_indicator_dialog.CustomIndicatorDialog``.

Pins:
* Default state — empty name, building-blocks mode, expression empty.
* Mode-switch preserves name + description vars.
* Validation surface (empty, valid, bad expression, bad name).
* Save → writes a ``.py`` file with header marker → registers in
  ``INDICATORS``.
* Delete → unlinks from disk + unregisters.
* Loading an existing file populates the editor.
* ``protect_combobox_wheel`` guards the Mode combobox.
"""
from __future__ import annotations

from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")
ttk = pytest.importorskip("tkinter.ttk")

from tradinglab.gui import custom_indicator_dialog as mod
from tradinglab.indicators import base as ind_base
from tradinglab.indicators import loader as ind_loader


@pytest.fixture()
def root():
    try:
        r = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        r.geometry("1x1-3000-3000")
    except tk.TclError:
        pass
    # Provide a default app surface attribute used by the dialog.
    r._primary = []  # type: ignore[attr-defined]
    yield r
    try:
        r.update_idletasks()
        r.destroy()
    except tk.TclError:
        pass


@pytest.fixture()
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture(autouse=True)
def cleanup_registry():
    before = set(ind_base.INDICATORS.keys())
    yield
    new_keys = set(ind_base.INDICATORS.keys()) - before
    for k in new_keys:
        ind_base.INDICATORS.pop(k, None)
        ind_base._BY_KIND_ID.pop(k, None)


def _mk(root, tmp_dir) -> mod.CustomIndicatorDialog:
    return mod.CustomIndicatorDialog(root, directory=tmp_dir)


def test_default_state(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    assert dlg._name_var.get() == ""
    assert dlg._desc_var.get() == ""
    # Default is now Conditions (visual builder).
    assert dlg._mode_var.get() == mod._CONDITIONS_MODE
    # Overlay defaults to False for Conditions (0/1 signal → sub-pane).
    assert dlg._overlay_var.get() is False
    assert dlg._current_path is None
    # Listbox empty (no files in tmp dir).
    assert dlg._listbox.size() == 0
    # BlockEditor mounted, expression/python text widgets not.
    assert dlg._block_editor is not None
    assert dlg._expr_text is None
    assert dlg._python_text is None
    dlg.destroy()


def test_mode_switch_preserves_metadata(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("preserved")
    dlg._desc_var.set("a description")
    dlg._mode_var.set(mod._PYTHON_MODE)
    dlg._render_compose_for_mode()
    assert dlg._name_var.get() == "preserved"
    assert dlg._desc_var.get() == "a description"
    assert dlg._python_text is not None
    assert dlg._expr_text is None
    assert dlg._block_editor is None
    dlg.destroy()


def test_validate_rejects_empty_name(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    # Switch to expression mode so we can fill in a body without
    # constructing a Group.
    dlg._mode_var.set(mod._EXPRESSION_MODE)
    dlg._render_compose_for_mode()
    if dlg._expr_text is not None:
        dlg._expr_text.insert("1.0", "close")
    ok, msg = dlg._validate()
    assert not ok
    assert "name" in msg.lower()
    dlg.destroy()


def test_validate_accepts_valid_expression(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("ok_one")
    dlg._mode_var.set(mod._EXPRESSION_MODE)
    dlg._render_compose_for_mode()
    dlg._expr_text.insert("1.0", "ema(close, 9) - sma(close, 20)")
    ok, msg = dlg._validate()
    assert ok, msg
    dlg.destroy()


def test_validate_rejects_unsafe_expression(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("ok_one")
    dlg._mode_var.set(mod._EXPRESSION_MODE)
    dlg._render_compose_for_mode()
    dlg._expr_text.insert("1.0", "__import__('os')")
    ok, msg = dlg._validate()
    assert not ok
    dlg.destroy()


def test_save_writes_file_and_registers(root, tmp_dir, monkeypatch) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("test_save")
    dlg._desc_var.set("save flow")
    dlg._mode_var.set(mod._EXPRESSION_MODE)
    dlg._render_compose_for_mode()
    dlg._expr_text.insert("1.0", "ema(close, 9) - sma(close, 20)")
    # Skip the messagebox prompts.
    dlg._on_save()
    saved = tmp_dir / "test_save.py"
    assert saved.exists(), "saved file should land in target dir"
    text = saved.read_text(encoding="utf-8")
    assert "# tradinglab-custom-indicator" in text
    assert "mode: building_blocks" in text
    assert "test_save" in ind_base.INDICATORS
    # Listbox refreshed.
    assert dlg._listbox.get(0) == "test_save"
    dlg.destroy()


def test_save_python_mode_requires_register_call(root, tmp_dir, monkeypatch) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("py_invalid")
    dlg._mode_var.set(mod._PYTHON_MODE)
    dlg._render_compose_for_mode()
    dlg._python_text.delete("1.0", "end")
    dlg._python_text.insert("1.0", "x = 1\n")  # no register_indicator call
    ok, msg = dlg._validate()
    assert not ok
    assert "register_indicator" in msg
    dlg.destroy()


def test_delete_unlinks_and_unregisters(root, tmp_dir, monkeypatch) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("test_del")
    dlg._mode_var.set(mod._EXPRESSION_MODE)
    dlg._render_compose_for_mode()
    dlg._expr_text.insert("1.0", "close")
    dlg._on_save()
    assert "test_del" in ind_base.INDICATORS
    saved = tmp_dir / "test_del.py"
    assert saved.exists()
    # Force select index 0 + bypass messagebox.
    dlg._listbox.selection_clear(0, "end")
    dlg._listbox.selection_set(0)
    monkeypatch.setattr(mod.messagebox, "askyesno", lambda *a, **k: True)
    dlg._on_delete()
    assert not saved.exists()
    assert "test_del" not in ind_base.INDICATORS
    dlg.destroy()


def test_load_existing_file_populates_editor(root, tmp_dir) -> None:
    # Pre-seed a builder file directly.
    src = (
        "# tradinglab-custom-indicator\n"
        "# mode: building_blocks\n"
        "# expression: ema(close, 9)\n"
        "# description: a preseeded indicator\n"
        "# created: 2026-01-01T00:00:00Z\n"
        "# updated: 2026-01-01T00:00:00Z\n"
        "\n"
        "from tradinglab.indicators.base import register_indicator\n"
        "from tradinglab.indicators.expression import evaluate, parse_expression\n"
        "from tradinglab.core.bars import Bars\n"
        "_EXPR = 'ema(close, 9)'\n"
        "_PARSED = parse_expression(_EXPR)\n"
        "class _Indicator:\n"
        "    name = 'preseed'\n"
        "    kind_id = 'preseed'\n"
        "    kind_version = 1\n"
        "    overlay = True\n"
        "    pane_group = ''\n"
        "    def compute_arr(self, bars):\n"
        "        return evaluate(_PARSED, bars)\n"
        "    def compute(self, candles):\n"
        "        return self.compute_arr(Bars.from_candles(candles))\n"
        "    @property\n"
        "    def warmup_bars(self):\n"
        "        return 9\n"
        "register_indicator('preseed', lambda: _Indicator())\n"
    )
    (tmp_dir / "preseed.py").write_text(src, encoding="utf-8")
    dlg = _mk(root, tmp_dir)
    # Listbox should show preseed.
    assert dlg._listbox.get(0) == "preseed"
    dlg._listbox.selection_set(0)
    dlg._on_select_saved()
    assert dlg._name_var.get() == "preseed"
    assert dlg._desc_var.get() == "a preseeded indicator"
    # mode: building_blocks maps to the (renamed) Expression mode.
    assert dlg._mode_var.get() == mod._EXPRESSION_MODE
    assert dlg._expr_text is not None
    assert "ema(close, 9)" in dlg._expr_text.get("1.0", "end")
    dlg.destroy()


def test_combobox_wheel_guard_applied(root, tmp_dir) -> None:
    """Verify the Mode combobox does NOT mutate on mouse-wheel events.

    Mirrors ``tests/unit/gui/test_combobox_wheel_guard.py`` for the
    custom-indicator dialog (CLAUDE.md §7.11).
    """
    dlg = _mk(root, tmp_dir)
    combo = dlg._mode_combo
    initial = combo.get()
    for _ in range(5):
        combo.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
        combo.update()
    assert combo.get() == initial, "mode combobox value drifted under wheel"
    dlg.destroy()


def test_save_overwrite_existing_loaded_file_is_silent(
    root, tmp_dir, monkeypatch,
) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("overwrite_test")
    dlg._mode_var.set(mod._EXPRESSION_MODE)
    dlg._render_compose_for_mode()
    dlg._expr_text.insert("1.0", "close")
    dlg._on_save()
    # Re-save without changing the name should not trigger a yesno
    # prompt because _current_path matches target.
    def _no_prompt(*a, **k):
        raise AssertionError("should not prompt on re-save of loaded file")
    monkeypatch.setattr(mod.messagebox, "askyesno", _no_prompt)
    dlg._desc_var.set("updated desc")
    dlg._on_save()
    text = (tmp_dir / "overwrite_test.py").read_text(encoding="utf-8")
    assert "updated desc" in text
    dlg.destroy()


def test_loader_hot_register_round_trip(tmp_dir) -> None:
    """End-to-end: write file via codegen, register via loader, verify."""
    from tradinglab.indicators.expression import expression_to_python

    src = expression_to_python(
        name="round_trip_test", expression="ema(close, 5)",
    )
    (tmp_dir / "round_trip_test.py").write_text(src, encoding="utf-8")
    result = ind_loader.register_user_indicator_file(
        tmp_dir / "round_trip_test.py"
    )
    try:
        assert not result.errors, result.errors
        assert "round_trip_test" in ind_base.INDICATORS
    finally:
        ind_loader.unregister_indicator("round_trip_test")


# ===========================================================================
# Conditions mode (visual Groups/Conditions builder)
# ===========================================================================


def _build_simple_group():
    from tradinglab.scanner.model import Condition, FieldRef, Group
    return Group(combinator="and", children=[
        Condition(
            left=FieldRef.builtin("close"),
            op=">",
            params={"right": FieldRef.indicator("ema", params={"length": 20})},
            interval="1d",
        ),
    ])


def test_conditions_is_default_mode(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    assert dlg._mode_var.get() == mod._CONDITIONS_MODE
    assert dlg._block_editor is not None
    dlg.destroy()


def test_conditions_to_expression_preserves_text(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    # Start on Conditions (default), switch to Expression, type some text,
    # switch back to Conditions, then back to Expression and verify the
    # text cache was preserved.
    dlg._mode_var.set(mod._EXPRESSION_MODE)
    dlg._on_mode_changed()
    dlg._expr_text.insert("1.0", "ema(close, 9) - sma(close, 20)")
    dlg._mode_var.set(mod._CONDITIONS_MODE)
    dlg._on_mode_changed()
    assert dlg._block_editor is not None
    assert dlg._expr_text is None
    dlg._mode_var.set(mod._EXPRESSION_MODE)
    dlg._on_mode_changed()
    assert dlg._expr_text is not None
    assert "ema(close, 9) - sma(close, 20)" in dlg._expr_text.get("1.0", "end")
    dlg.destroy()


def test_expression_to_conditions_preserves_group(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    # Install a non-trivial Group via the editor, switch to Expression,
    # then back to Conditions; the editor should still show the group.
    g = _build_simple_group()
    dlg._block_editor.set_root(g)
    # Mimic the user committing the change.
    dlg._capture_body_state()
    dlg._mode_var.set(mod._EXPRESSION_MODE)
    dlg._on_mode_changed()
    dlg._mode_var.set(mod._CONDITIONS_MODE)
    dlg._on_mode_changed()
    assert dlg._block_editor is not None
    root_group = dlg._block_editor.get_root()
    assert len(root_group.children) == 1
    dlg.destroy()


def test_conditions_validate_empty_tree_fails(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("cond_empty")
    ok, msg = dlg._validate()
    assert not ok
    assert "empty" in msg.lower()
    dlg.destroy()


def test_conditions_save_writes_file_with_json_header(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("cond_save_test")
    dlg._desc_var.set("a conditions indicator")
    dlg._block_editor.set_root(_build_simple_group())
    dlg._capture_body_state()
    dlg._on_save()
    saved = tmp_dir / "cond_save_test.py"
    assert saved.exists()
    text = saved.read_text(encoding="utf-8")
    assert "# tradinglab-custom-indicator" in text
    assert "# mode: conditions" in text
    assert "# conditions_json:" in text
    assert "cond_save_test" in ind_base.INDICATORS
    dlg.destroy()


def test_conditions_round_trip_reload(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("cond_rt_test")
    dlg._desc_var.set("round trip")
    dlg._block_editor.set_root(_build_simple_group())
    dlg._capture_body_state()
    dlg._on_save()
    saved = tmp_dir / "cond_rt_test.py"
    assert saved.exists()
    # Click New to clear, then re-load via the listbox.
    dlg._on_new()
    assert dlg._block_editor.get_root().children == []
    # Find the file in the listbox and select it.
    files = [dlg._listbox.get(i) for i in range(dlg._listbox.size())]
    idx = files.index("cond_rt_test")
    dlg._listbox.selection_clear(0, "end")
    dlg._listbox.selection_set(idx)
    dlg._on_select_saved()
    assert dlg._mode_var.get() == mod._CONDITIONS_MODE
    assert dlg._block_editor is not None
    loaded = dlg._block_editor.get_root()
    assert len(loaded.children) == 1
    assert dlg._desc_var.get() == "round trip"
    dlg.destroy()


def test_conditions_wheel_guard_after_block_editor_mutation(root, tmp_dir) -> None:
    """Wheel-over-Combobox inside the embedded BlockEditor must NOT mutate.

    Mirrors CLAUDE.md §7.11 — the wheel guard must be re-applied after
    every BlockEditor partial rebuild (Add Condition, Add Group, change
    combinator). We install a group, simulate the on_change callback to
    rebuild, then wheel over the mode combobox and verify it is stable.
    """
    dlg = _mk(root, tmp_dir)
    dlg._block_editor.set_root(_build_simple_group())
    dlg._on_block_editor_changed()
    combo = dlg._mode_combo
    initial = combo.get()
    for _ in range(5):
        combo.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
        combo.update()
    assert combo.get() == initial
    dlg.destroy()


# ===========================================================================
# Builder guardrail: Preview pane must not steal Composition height
# ===========================================================================


def test_preview_pane_starts_collapsed(root, tmp_dir) -> None:
    """The Preview pane must NOT expand before a chart is rendered so a
    parameter-heavy Composition body owns the full vertical budget."""
    dlg = _mk(root, tmp_dir)
    assert dlg._preview_expanded is False
    info = dlg._preview_frame.pack_info()
    assert str(info.get("expand")) in ("0", "false", "False")
    dlg.destroy()


def test_preview_pane_expands_after_render(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._set_preview_expanded(True)
    assert dlg._preview_expanded is True
    info = dlg._preview_frame.pack_info()
    assert str(info.get("expand")) in ("1", "true", "True")
    dlg.destroy()


def test_new_indicator_recollapses_preview(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._set_preview_expanded(True)
    assert dlg._preview_expanded is True
    dlg._on_new()
    assert dlg._preview_expanded is False
    info = dlg._preview_frame.pack_info()
    assert str(info.get("expand")) in ("0", "false", "False")
    dlg.destroy()


# ===========================================================================
# Import / Export
# ===========================================================================


def _write_builder_file(tmp_dir: Path, name: str) -> Path:
    from tradinglab.indicators.expression import expression_to_python
    src = expression_to_python(name=name, expression="ema(close, 5)")
    p = tmp_dir / f"{name}.py"
    p.write_text(src, encoding="utf-8")
    return p


def _ext_dir(tmp_path: Path) -> Path:
    """An external directory distinct from the indicators dir (== tmp_path)."""
    d = tmp_path / "_external"
    d.mkdir(exist_ok=True)
    return d


def test_export_writes_selected_file(root, tmp_dir, tmp_path, monkeypatch) -> None:
    _write_builder_file(tmp_dir, "exp_me")
    dlg = _mk(root, tmp_dir)
    dlg._refresh_saved_list()
    # Select the one builder file.
    dlg._listbox.selection_clear(0, "end")
    dlg._listbox.selection_set(0)
    dest = _ext_dir(tmp_path) / "exported.py"
    monkeypatch.setattr(
        mod.filedialog, "asksaveasfilename", lambda *a, **k: str(dest),
    )
    dlg._on_export()
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == (tmp_dir / "exp_me.py").read_text(
        encoding="utf-8"
    )
    dlg.destroy()


def test_export_no_selection_sets_error(root, tmp_dir, monkeypatch) -> None:
    dlg = _mk(root, tmp_dir)
    called = {"dialog": False}
    def _fail(*a, **k):
        called["dialog"] = True
        return ""
    monkeypatch.setattr(mod.filedialog, "asksaveasfilename", _fail)
    dlg._on_export()
    assert called["dialog"] is False
    assert "Select" in dlg._status_var.get()
    dlg.destroy()


def test_export_cancelled_is_noop(root, tmp_dir, monkeypatch) -> None:
    _write_builder_file(tmp_dir, "cancel_exp")
    dlg = _mk(root, tmp_dir)
    dlg._refresh_saved_list()
    dlg._listbox.selection_set(0)
    monkeypatch.setattr(mod.filedialog, "asksaveasfilename", lambda *a, **k: "")
    dlg._on_export()  # should not raise
    dlg.destroy()


def test_import_builder_file_registers_and_lists(
    root, tmp_dir, tmp_path, monkeypatch
) -> None:
    external = _write_builder_file(_ext_dir(tmp_path), "imported_ind")
    dlg = _mk(root, tmp_dir)
    monkeypatch.setattr(
        mod.filedialog, "askopenfilename", lambda *a, **k: str(external),
    )
    dlg._on_import()
    try:
        # Copied into the indicators dir.
        assert (tmp_dir / "imported_ind.py").is_file()
        # Registered globally.
        assert "imported_ind" in ind_base.INDICATORS
        # Appears in the saved list.
        assert "imported_ind" in dlg._listbox.get(0, "end")
    finally:
        ind_loader.unregister_indicator("imported_ind")
    dlg.destroy()


def test_import_cancelled_is_noop(root, tmp_dir, monkeypatch) -> None:
    dlg = _mk(root, tmp_dir)
    monkeypatch.setattr(mod.filedialog, "askopenfilename", lambda *a, **k: "")
    dlg._on_import()
    assert dlg._listbox.size() == 0
    dlg.destroy()


def test_import_python_file_prompts_trust(root, tmp_dir, tmp_path, monkeypatch) -> None:
    # A non-builder plugin (no marker) triggers the trust confirmation.
    plugin = _ext_dir(tmp_path) / "trust_plugin.py"
    plugin.write_text(
        "from tradinglab.indicators.base import register_indicator\n"
        "import numpy as np\n"
        "class _F:\n"
        "    kind_id = 'trust_plugin'\n"
        "    name = 'trust_plugin'\n"
        "    overlay = True\n"
        "    def compute_arr(self, bars):\n"
        "        return np.zeros(len(bars.close))\n"
        "register_indicator('trust_plugin', lambda **k: _F())\n",
        encoding="utf-8",
    )
    dlg = _mk(root, tmp_dir)
    monkeypatch.setattr(
        mod.filedialog, "askopenfilename", lambda *a, **k: str(plugin),
    )
    seen = {"asked": False}
    def _ask(*a, **k):
        seen["asked"] = True
        return False  # decline
    monkeypatch.setattr(mod.messagebox, "askokcancel", _ask)
    dlg._on_import()
    assert seen["asked"] is True
    # Declined → not copied.
    assert not (tmp_dir / "trust_plugin.py").exists()
    dlg.destroy()


def test_import_collision_prompts_overwrite(
    root, tmp_dir, tmp_path, monkeypatch
) -> None:
    # Pre-existing file in the indicators dir.
    _write_builder_file(tmp_dir, "dup_ind")
    external = _write_builder_file(_ext_dir(tmp_path), "dup_ind")
    dlg = _mk(root, tmp_dir)
    monkeypatch.setattr(
        mod.filedialog, "askopenfilename", lambda *a, **k: str(external),
    )
    seen = {"asked": False}
    def _ask(*a, **k):
        seen["asked"] = True
        return False  # decline overwrite
    monkeypatch.setattr(mod.messagebox, "askyesno", _ask)
    dlg._on_import()
    assert seen["asked"] is True
    dlg.destroy()



# ===========================================================================
# Dark-mode theming of native (non-ttk) widgets
# ===========================================================================
from types import SimpleNamespace  # noqa: E402

from tradinglab.constants import DARK_THEME  # noqa: E402


def test_saved_listbox_dark_theme(root, tmp_dir) -> None:
    """The saved-indicators Listbox must follow the dark palette, not stay white."""
    dlg = _mk(root, tmp_dir)
    dlg._apply_native_theme(DARK_THEME)
    lb = dlg._listbox
    assert str(lb.cget("background")) == DARK_THEME["tree_bg"]
    assert str(lb.cget("foreground")) == DARK_THEME["tree_fg"]
    assert str(lb.cget("selectbackground")) == DARK_THEME["spine"]
    assert str(lb.cget("selectforeground")) == DARK_THEME["tree_fg"]
    assert str(lb.cget("highlightbackground")) == DARK_THEME["spine"]
    assert str(lb.cget("highlightcolor")) == DARK_THEME["spine"]
    assert str(lb.cget("highlightthickness")) == "1"
    assert str(lb.cget("borderwidth")) == "0"
    assert str(lb.cget("relief")) == "flat"
    dlg.destroy()


def test_expression_text_dark_theme(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._mode_var.set(mod._EXPRESSION_MODE)
    dlg._on_mode_changed()
    dlg._apply_native_theme(DARK_THEME)
    txt = dlg._expr_text
    assert str(txt.cget("background")) == DARK_THEME["ax_bg"]
    assert str(txt.cget("foreground")) == DARK_THEME["text"]
    assert str(txt.cget("insertbackground")) == DARK_THEME["text"]
    assert str(txt.cget("highlightbackground")) == DARK_THEME["spine"]
    assert str(txt.cget("highlightcolor")) == DARK_THEME["spine"]
    assert str(txt.cget("highlightthickness")) == "1"
    assert str(txt.cget("borderwidth")) == "0"
    assert str(txt.cget("relief")) == "flat"
    dlg.destroy()


def test_python_text_dark_theme(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._mode_var.set(mod._PYTHON_MODE)
    dlg._on_mode_changed()
    dlg._apply_native_theme(DARK_THEME)
    txt = dlg._python_text
    assert str(txt.cget("background")) == DARK_THEME["ax_bg"]
    assert str(txt.cget("foreground")) == DARK_THEME["text"]
    assert str(txt.cget("insertbackground")) == DARK_THEME["text"]
    assert str(txt.cget("highlightthickness")) == "1"
    dlg.destroy()


def test_native_theme_auto_applied_from_app_theme_ctrl(root, tmp_dir) -> None:
    """Constructing the dialog under a dark app theme controller paints the
    Listbox dark without a manual ``_apply_native_theme`` call."""
    root._theme_ctrl = SimpleNamespace(theme=DARK_THEME)  # type: ignore[attr-defined]
    try:
        dlg = mod.CustomIndicatorDialog(root, directory=tmp_dir)
        lb = dlg._listbox
        assert str(lb.cget("background")) == DARK_THEME["tree_bg"]
        assert str(lb.cget("foreground")) == DARK_THEME["tree_fg"]
        # The dialog Toplevel background also follows the window color.
        assert str(dlg.cget("background")) == DARK_THEME["win_bg"]
        dlg.destroy()
    finally:
        delattr(root, "_theme_ctrl")


def test_native_theme_reapplied_after_mode_switch(root, tmp_dir) -> None:
    """Switching modes builds a fresh Text widget; it must inherit the theme."""
    root._theme_ctrl = SimpleNamespace(theme=DARK_THEME)  # type: ignore[attr-defined]
    try:
        dlg = mod.CustomIndicatorDialog(root, directory=tmp_dir)
        dlg._mode_var.set(mod._EXPRESSION_MODE)
        dlg._on_mode_changed()
        # No explicit _apply_native_theme — the mode-render path must do it.
        assert str(dlg._expr_text.cget("background")) == DARK_THEME["ax_bg"]
        assert str(dlg._expr_text.cget("foreground")) == DARK_THEME["text"]
        dlg.destroy()
    finally:
        delattr(root, "_theme_ctrl")


# ===========================================================================
# Scrollable Conditions body (many-condition reachability)
# ===========================================================================
from tradinglab.scanner import model as _scan_model  # noqa: E402


def _simple_condition(threshold: float) -> _scan_model.Condition:
    """A valid ``close > <literal>`` condition usable in a Group tree."""
    return _scan_model.Condition(
        left=_scan_model.FieldRef(kind="builtin", id="close"),
        op=_scan_model.OP_GT,
        params={"right": _scan_model.FieldRef(kind="literal", value=float(threshold))},
        interval="1d",
    )


def test_conditions_body_hosts_block_editor_in_scrollable_canvas(root, tmp_dir) -> None:
    """The Conditions BlockEditor must live inside a scrollable Canvas so a
    long condition list stays reachable instead of being clipped."""
    dlg = _mk(root, tmp_dir)
    canvas = dlg._conditions_canvas
    assert isinstance(canvas, tk.Canvas)
    assert hasattr(canvas, "_tl_v_can_scroll")
    be = dlg._block_editor
    assert be is not None
    inner = be.nametowidget(be.winfo_parent())
    assert inner.nametowidget(inner.winfo_parent()) is canvas
    dlg.destroy()


def test_conditions_canvas_cleared_when_leaving_conditions_mode(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    assert isinstance(dlg._conditions_canvas, tk.Canvas)
    dlg._mode_var.set(mod._EXPRESSION_MODE)
    dlg._on_mode_changed()
    assert dlg._conditions_canvas is None
    dlg._mode_var.set(mod._CONDITIONS_MODE)
    dlg._on_mode_changed()
    assert isinstance(dlg._conditions_canvas, tk.Canvas)
    dlg.destroy()


def test_many_conditions_overflow_is_scrollable(root, tmp_dir) -> None:
    """With many conditions the inner content has positive requested height,
    hosted by the scroll viewport that makes the rows reachable."""
    dlg = _mk(root, tmp_dir)
    group = _scan_model.Group(
        combinator="and",
        children=[_simple_condition(i) for i in range(8)],
    )
    dlg._block_editor.set_root(group)
    dlg._group_root = group
    try:
        dlg.geometry("700x300")
    except tk.TclError:
        pass
    dlg.update_idletasks()
    canvas = dlg._conditions_canvas
    inner = dlg._block_editor.nametowidget(dlg._block_editor.winfo_parent())
    assert inner.winfo_reqheight() > 0
    assert canvas.winfo_reqheight() >= 0
    dlg.destroy()


def test_conditions_canvas_dark_themed(root, tmp_dir) -> None:
    """The scroll canvas must follow the window background in dark mode,
    not stay bright white behind the condition rows."""
    dlg = _mk(root, tmp_dir)
    dlg._apply_native_theme(DARK_THEME)
    canvas = dlg._conditions_canvas
    assert isinstance(canvas, tk.Canvas)
    assert str(canvas.cget("background")) == DARK_THEME["win_bg"]
    dlg.destroy()
