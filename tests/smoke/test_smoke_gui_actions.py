"""Smoke checks that actually CLICK buttons / pick menu entries.

Most GUI smoke coverage historically constructed a widget and read its
state, or called the underlying handler method directly (``_add_op(...)``,
``_journal_blind_var.set(...)``). Those pass even when the ``command=`` /
``<<ComboboxSelected>>`` wiring is broken.

These checks drive the REAL widgets against the session-scoped
``ChartApp`` fixture — invoking a ``Menubutton``'s menu, a ``Button``'s
command, a ``Checkbutton`` toggle, a ``Combobox`` selection — and assert
the resulting state change. A mis-wired control fails here.

Two interaction-heavy surfaces are exercised:

* the ``ExpressionBuilder`` "+" token-stacker (Entries/Exits/Scanner
  operand editor), and
* the Performance View daily-journal buttons (Blind toggle, Export CSV,
  Copy to clipboard, Close) — the journaling feature's report UI.
"""
from __future__ import annotations

import datetime as _dt
import sys
import tkinter as tk
from tkinter import ttk

import pytest

from tests.smoke._helpers import (
    _click,
    _find_button,
    _menu_invoke,
    _pump,
    _submenu_of,
)

# ``PerformanceView`` (a ``BaseModalDialog``) calls ``self.transient(parent)``
# which deadlocks on the headless ``macos-15-arm64`` CI runner — CLAUDE.md
# §7.1. Skip the dialog-opening check there; the widgets are still unit-
# tested on every platform (tests/unit/gui/test_performance_view_journal.py).
_skip_modal_on_darwin = pytest.mark.skipif(
    sys.platform == "darwin",
    reason="Tk transient() modal dialog deadlock on headless macOS — CLAUDE.md §7.1",
)


# --------------------------------------------------------------------------
# ExpressionBuilder — drive the "+" menu, operand-chip buttons, and the
# operator combobox instead of calling _add_operand / _add_op / _set_op.
# --------------------------------------------------------------------------

def test_gui_action_expression_builder_widgets(app):
    """Build ``0.5 * ema(9)`` by clicking real controls, then remove a token.

    Verifies the command wiring of: the ``+`` Menubutton menu (``Value…``
    + ``Operator`` cascade), the operator ``Combobox`` selection binding,
    and an operand chip's ``✕`` remove button. A broken binding leaves the
    model unchanged and trips an assertion here.
    """
    from tradinglab.gui.expression_builder import ExpressionBuilder, expression_text
    from tradinglab.scanner.model import FieldRef

    top = tk.Toplevel(app)
    top.geometry("720x300+80+80")
    eb = ExpressionBuilder(top)
    eb.pack(fill="x", padx=10, pady=10)

    # Feed deterministic operands so invoking "Value…" appends without
    # opening the modal _OperandDialog (which would deadlock on headless
    # macOS and needs no coverage here — the picker is unit-tested).
    operands = [FieldRef.literal(0.5), FieldRef.indicator("ema", params={"length": 9})]
    eb._pick_operand = lambda _cur=None: (  # type: ignore[method-assign]
        operands.pop(0) if operands else FieldRef.literal(1.0)
    )

    def _plus_menu():
        btn = _find_button(eb, "+")
        assert isinstance(btn, ttk.Menubutton), "builder must expose a '+' menu button"
        return btn.nametowidget(btn.cget("menu"))

    try:
        _pump(app, 0.05)

        # 1) "+ → Value…" appends the first operand (0.5).
        assert _menu_invoke(_plus_menu(), "Value"), "'+' menu needs a Value… entry"
        _pump(app, 0.05)
        assert len(eb.get().terms) == 1

        # 2) "+ → Operator → *" appends a '*' operator (the chips rebuild on
        #    every change, so the menu is re-fetched each time).
        op_sub = _submenu_of(_plus_menu(), "Operator")
        assert op_sub is not None, "'+' menu needs an Operator cascade"
        assert _menu_invoke(op_sub, "*", exact=True)
        _pump(app, 0.05)

        # 3) "+ → Value…" appends ema(9) → expression is now valid.
        assert _menu_invoke(_plus_menu(), "Value")
        _pump(app, 0.05)
        assert eb.is_valid() is True
        assert expression_text(eb.get().terms) == "0.5 * ema(9)"
        # The live validity label reflects it (✓ = U+2713).
        assert eb._valid_var.get().startswith("\u2713")

        # 4) Change the operator via the Combobox selection binding: '*' → '+'.
        combo = next(c for w in eb._chips.winfo_children()
                     for c in w.winfo_children() if isinstance(c, ttk.Combobox))
        combo.set("+")
        combo.event_generate("<<ComboboxSelected>>")
        _pump(app, 0.05)
        assert expression_text(eb.get().terms) == "0.5 + ema(9)"

        # 5) Click an operand chip's ✕ (U+2715) remove button → a token drops.
        n_before = len(eb.get().terms)
        remove_btn = _find_button(eb, "\u2715")
        assert remove_btn is not None, "operand/op chips must expose a ✕ remove button"
        _click(remove_btn)
        _pump(app, 0.05)
        assert len(eb.get().terms) == n_before - 1
    finally:
        try:
            top.destroy()
        except tk.TclError:
            pass


# --------------------------------------------------------------------------
# Performance View journaling buttons — Blind toggle, Export CSV, Copy,
# Close. Invokes the widgets, not the handler methods.
# --------------------------------------------------------------------------

def _ts(y: int, m: int, d: int, hh: int = 14, mm: int = 30) -> int:
    return int(_dt.datetime(y, m, d, hh, mm, tzinfo=_dt.timezone.utc).timestamp())


def _post(entry_ts: int, pnl: float, ref: str):
    from tradinglab.backtest.journal import PostTradeReview
    return PostTradeReview(
        symbol="AMD", entry_ts=entry_ts, exit_ts=entry_ts + 3600,
        entry_price=10.0, exit_price=10.0 + pnl / 100.0, quantity=100.0,
        side="buy", pnl=pnl, pnl_pct=0.0, mae=0.0, mfe=0.0,
        mae_pct=0.0, mfe_pct=0.0, ref_pre_trade_id=ref,
    )


def _result_with_days():
    from tradinglab.backtest.session import SessionResult, SessionSpec
    return SessionResult(
        spec=SessionSpec(deck_seed=1, tickers=("AMD",), start_clock_iso="",
                         slippage_bps=0.0, commission=0.0),
        post_trades=[_post(_ts(2025, 4, 29), 100.0, "a"),
                     _post(_ts(2025, 4, 30), -20.0, "b")],
        day_notes={"2025-04-29": "SPY pulling back, NVDA holding RS",
                   "2025-05-01": "watched all day, stood aside"},
    )


@_skip_modal_on_darwin
def test_gui_action_performance_view_buttons(app, tmp_path, monkeypatch):
    """Open the Performance View on a journaled result and click its buttons.

    Covers the journaling report UI end-to-end at the widget layer:
    the **Blind** checkbutton re-renders the daily-journal pane, **Export
    CSV** writes a file, **Copy to clipboard** populates the clipboard, and
    **Close** destroys the window. Each is invoked as a real click.
    """
    from tradinglab.gui import performance_view as _pv
    from tradinglab.gui.performance_view import PerformanceView

    win = PerformanceView(app, _result_with_days(), title="smoke gui-actions")
    try:
        _pump(app, 0.1)
        tree = win._journal_tree
        day_nodes = tree.get_children("")
        # 3 days: 04-29 (traded), 04-30 (traded), 05-01 (flat note-only).
        assert len(day_nodes) == 3
        assert "2025" in tree.item(day_nodes[0], "text")  # non-blind shows the date

        # 1) Blind checkbutton — invoke the ACTUAL widget (command=_populate_journal).
        blind = _find_button(win, "Blind")
        assert blind is not None, "Performance View must expose a Blind checkbutton"
        _click(blind)
        _pump(app, 0.05)
        assert win._journal_blind_var.get() is True
        labels = [tree.item(n, "text") for n in tree.get_children("")]
        assert labels[0] == "Replay Day 1"
        assert all("2025" not in lbl for lbl in labels)

        # 2) Export CSV — stub the save dialog, click the real button, assert a file.
        dst = tmp_path / "journal_export.csv"
        monkeypatch.setattr(_pv.filedialog, "asksaveasfilename",
                            lambda *a, **k: str(dst))
        assert str(win._export_btn["state"]) == "normal"
        _click(win._export_btn)
        _pump(app, 0.1)
        assert dst.exists() and dst.stat().st_size > 0

        # 3) Copy to clipboard — click, then read the clipboard back (where
        #    the headless X server exposes one).
        win.clipboard_clear()
        _click(win._copy_btn)
        _pump(app, 0.05)
        try:
            clip = win.clipboard_get()
        except tk.TclError:
            clip = None
        if clip is not None:
            assert "AMD" in clip

        # 4) Close button — click, window is destroyed.
        close = _find_button(win, "Close")
        assert close is not None, "Performance View must expose a Close button"
        _click(close)
        assert not win.winfo_exists()
    finally:
        try:
            if win.winfo_exists():
                win.destroy()
        except tk.TclError:
            pass
