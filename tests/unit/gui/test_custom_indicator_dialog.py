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
    assert dlg._mode_var.get() == mod._BUILDING_BLOCKS
    assert dlg._overlay_var.get() is True
    assert dlg._current_path is None
    # Listbox empty (no files in tmp dir).
    assert dlg._listbox.size() == 0
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
    dlg.destroy()


def test_validate_rejects_empty_name(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    if dlg._expr_text is not None:
        dlg._expr_text.insert("1.0", "close")
    ok, msg = dlg._validate()
    assert not ok
    assert "name" in msg.lower()
    dlg.destroy()


def test_validate_accepts_valid_expression(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("ok_one")
    dlg._expr_text.insert("1.0", "ema(close, 9) - sma(close, 20)")
    ok, msg = dlg._validate()
    assert ok, msg
    dlg.destroy()


def test_validate_rejects_unsafe_expression(root, tmp_dir) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("ok_one")
    dlg._expr_text.insert("1.0", "__import__('os')")
    ok, msg = dlg._validate()
    assert not ok
    dlg.destroy()


def test_save_writes_file_and_registers(root, tmp_dir, monkeypatch) -> None:
    dlg = _mk(root, tmp_dir)
    dlg._name_var.set("test_save")
    dlg._desc_var.set("save flow")
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
    assert dlg._mode_var.get() == mod._BUILDING_BLOCKS
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
