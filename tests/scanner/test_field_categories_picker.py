"""Builtin field picker categorization (Phase 2 of the block-builder work).

Constructs the shared ``_FieldRefPicker`` directly and verifies the
builtin dropdown is grouped by category with non-selectable header rows,
that clicking a header is rejected (reverts, no commit), and that picking
a real field still commits + fires.
"""
from __future__ import annotations

from tkinter import ttk

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.gui.scanner_block_editor import _FieldRefPicker
from tradinglab.scanner.field_categories import grouped_combo_values, is_category_header
from tradinglab.scanner.model import FieldRef


def _builtin_combo(picker: _FieldRefPicker) -> ttk.Combobox | None:
    for w in picker._value_pane.winfo_children():
        if isinstance(w, ttk.Combobox):
            return w
    return None


def test_builtin_dropdown_is_categorized(root):
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    combo = _builtin_combo(picker)
    assert combo is not None
    values = tuple(combo.cget("values"))
    assert any(is_category_header(v) for v in values), (
        "builtin dropdown should carry category header rows"
    )
    assert "close" in values and "ha_streak" in values


def test_selecting_header_reverts_and_does_not_commit(root):
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    _values, header_set = grouped_combo_values("builtin")
    a_header = next(iter(header_set))
    picker._field_id_var.set(a_header)
    picker._commit_builtin()
    assert picker.get().id == "close"            # ref unchanged
    assert picker._field_id_var.get() == "close"  # display reverted off the header


def test_selecting_real_builtin_commits_and_fires(root):
    fired = {"n": 0}
    picker = _FieldRefPicker(
        root, ref=FieldRef.builtin("close"),
        on_change=lambda: fired.__setitem__("n", fired["n"] + 1))
    picker._field_id_var.set("volume")
    picker._commit_builtin()
    assert picker.get().id == "volume"
    assert fired["n"] >= 1
