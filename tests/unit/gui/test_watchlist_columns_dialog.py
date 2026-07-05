"""Unit tests for the watchlist Columns… dialog (Tk/Agg, headless)."""

from __future__ import annotations

import pytest

pytest.importorskip("tkinter")
import tkinter as tk  # noqa: E402

from tradinglab.gui.watchlist_columns_dialog import WatchlistColumnsDialog  # noqa: E402
from tradinglab.watchlists.columns import (  # noqa: E402
    KIND_SIGNAL,
    LOCKED_COLUMN_ID,
    default_columns,
)

# NOTE: the ``root`` fixture (a withdrawn Toplevel under one shared
# session Tk root) comes from ``tests/unit/conftest.py``. Reusing that
# single root avoids the Windows-on-ARM ``tk.Tk()`` init race that a
# per-test fresh root intermittently trips (see
# ``test_dialog_combobox_no_flicker.py`` for the same rationale).


def _make(root, columns=None):
    captured: dict = {}

    def on_apply(cols):
        captured["cols"] = cols

    dlg = WatchlistColumnsDialog(
        root,
        watchlist_name="Test",
        columns=columns if columns is not None else default_columns(),
        on_apply=on_apply,
    )
    return dlg, captured


def test_lists_default_columns_with_ticker_locked_first(root):
    dlg, _ = _make(root)
    try:
        assert dlg._listbox.size() == len(default_columns())
        assert dlg._cols[0].id == LOCKED_COLUMN_ID
        # Selecting ticker disables Remove (it is locked).
        dlg._listbox.selection_clear(0, tk.END)
        dlg._listbox.selection_set(0)
        dlg._on_select()
        assert str(dlg._btn_remove.cget("state")) == "disabled"
    finally:
        dlg.destroy()


def test_add_signal_column_then_apply_returns_validated_list(root):
    dlg, captured = _make(root)
    try:
        n0 = dlg._listbox.size()
        # Default picker ref is a builtin (close) — add it as a signal column.
        dlg._on_add_signal()
        assert dlg._listbox.size() == n0 + 1
        assert dlg._cols[-1].kind == KIND_SIGNAL
        dlg._on_ok()
        cols = captured["cols"]
        assert cols[0].id == LOCKED_COLUMN_ID  # ticker stays first
        assert any(c.kind == KIND_SIGNAL for c in cols)
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass


def test_reorder_never_moves_a_column_above_ticker(root):
    dlg, _ = _make(root)
    try:
        dlg._on_add_signal()
        idx = len(dlg._cols) - 1
        dlg._listbox.selection_clear(0, tk.END)
        dlg._listbox.selection_set(idx)
        dlg._on_select()
        for _ in range(10):
            dlg._on_move(-1)
        assert dlg._cols[0].id == LOCKED_COLUMN_ID
    finally:
        dlg.destroy()


def test_remove_signal_column(root):
    dlg, _ = _make(root)
    try:
        dlg._on_add_signal()
        n = dlg._listbox.size()
        idx = len(dlg._cols) - 1
        dlg._listbox.selection_clear(0, tk.END)
        dlg._listbox.selection_set(idx)
        dlg._on_select()
        dlg._on_remove()
        assert dlg._listbox.size() == n - 1
    finally:
        dlg.destroy()


def test_reset_restores_default_columns(root):
    dlg, _ = _make(root)
    try:
        dlg._on_add_signal()
        assert dlg._listbox.size() > len(default_columns())
        dlg._on_reset()
        assert dlg._listbox.size() == len(default_columns())
        assert all(c.kind != KIND_SIGNAL for c in dlg._cols)
    finally:
        dlg.destroy()
