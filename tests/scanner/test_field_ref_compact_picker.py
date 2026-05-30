"""Compact-mode ``_FieldRefPicker`` tests (CI scope: tests/scanner).

The compact display mode collapses parameter-heavy indicators (RRVOL,
BBANDS, SMI) into a one-line summary token plus an "Edit…" button so the
picker never clips off-screen on a narrow dialog. Pins:

* Compact indicator branch renders an Edit button + summary token, and
  builds NO per-parameter inline widgets.
* The summary token reflects the ref's params (and output key for
  multi-output indicators).
* Builtin / literal refs render identically in compact mode (no token,
  no Edit button — they are already compact).
* Committing the cross-symbol pin in compact mode preserves params.
* ``get()`` returns the current ref unchanged.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.gui.scanner_block_editor import _FieldRefPicker
from tradinglab.scanner.model import FieldRef


def _descendants(widget):
    out = []
    for child in widget.winfo_children():
        out.append(child)
        out.extend(_descendants(child))
    return out


def _edit_buttons(picker):
    return [
        w
        for w in _descendants(picker)
        if isinstance(w, ttk.Button) and str(w.cget("text")).startswith("Edit")
    ]


def test_compact_indicator_renders_edit_button(root):
    ref = FieldRef.indicator("rrvol", params={"length": 20})
    picker = _FieldRefPicker(root, ref=ref, display_mode="compact")
    root.update_idletasks()
    assert _edit_buttons(picker), "compact indicator picker must have an Edit… button"
    # No per-parameter inline widgets were built.
    assert picker._param_widgets == {}


def test_compact_summary_reflects_params(root):
    ref = FieldRef.indicator("rrvol", params={"length": 30, "compare_symbol": "QQQ"})
    picker = _FieldRefPicker(root, ref=ref, display_mode="compact")
    root.update_idletasks()
    summary = picker._compact_summary_var.get()
    assert "30" in summary
    assert "QQQ" in summary


def test_compact_builtin_has_no_edit_button(root):
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"), display_mode="compact")
    root.update_idletasks()
    assert not _edit_buttons(picker)


def test_compact_literal_has_no_edit_button(root):
    picker = _FieldRefPicker(root, ref=FieldRef.literal(100.0), display_mode="compact")
    root.update_idletasks()
    assert not _edit_buttons(picker)


def test_compact_symbol_commit_preserves_params(root):
    ref = FieldRef.indicator("rrvol", params={"length": 30, "compare_symbol": "QQQ"})
    picker = _FieldRefPicker(root, ref=ref, display_mode="compact")
    root.update_idletasks()
    # Simulate the user pinning a cross-symbol ticker and tabbing out.
    picker._symbol_var.set("SPY")
    picker._symbol_is_placeholder = False
    picker._commit_symbol()
    out = picker.get()
    assert out.symbol == "SPY"
    # Params must survive the symbol commit.
    assert out.params["length"] == 30
    assert out.params["compare_symbol"] == "QQQ"


def test_compact_apply_dialog_result_updates_ref(root):
    ref = FieldRef.indicator("rrvol", params={"length": 20})
    fires: list[int] = []
    picker = _FieldRefPicker(
        root, ref=ref, display_mode="compact", on_change=lambda: fires.append(1)
    )
    root.update_idletasks()
    # Emulate the popup returning an edited ref (avoids a modal wait).
    new_ref = FieldRef.indicator("rrvol", params={"length": 50})
    picker._ref = new_ref
    picker._rebuild_value_pane()
    picker._fire()
    assert picker.get().params["length"] == 50
    assert "50" in picker._compact_summary_var.get()
    assert fires
