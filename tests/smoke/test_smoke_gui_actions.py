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
    _pump_until,
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


def _settle_app(app) -> None:
    """Drain any in-flight async work before returning to the shared session.

    These checks run against the session-scoped ``ChartApp`` interleaved
    with the mega-test's checks. If a check leaves an async baseline
    reload in flight (e.g. a sandbox ``end_session`` reloading the prior
    ticker), it can land mid-assertion in a LATER check — the mega-test's
    ``check_d58`` stage F reads ``app._panel_state["primary"]["candles"]``
    and indexes it directly, so a transient short list there is an
    ``IndexError``. Pump until the primary candles are back to a healthy
    length (the yfinance stub loads 150), then a final settle.
    """
    _pump_until(
        app,
        lambda: len(
            (app._panel_state.get("primary") or {}).get("candles") or []
        ) >= 100,
        timeout=5.0,
    )
    # A fixed final drain so any pending Tk ``after()`` jobs left by a
    # dialog teardown (e.g. the §7.19 auto-stack ``after(100)`` reclassify)
    # fire HERE — inside this check's teardown — rather than during a
    # later check's pump.
    _pump(app, 0.5)


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


# --------------------------------------------------------------------------
# Entries dialog — click the Validate + Save & Close footer buttons.
# --------------------------------------------------------------------------

@_skip_modal_on_darwin
def test_gui_action_entries_dialog_validate_and_save(app):
    """Click the EntriesDialog's real Validate and Save & Close buttons.

    A valid strategy must pass validation and reach ``on_save`` when the
    Save button is clicked (existing tests call ``_on_save_clicked``
    directly, which skips the button wiring).
    """
    from tradinglab.entries.model import (
        Direction,
        EntryStrategy,
        EntryTrigger,
        SizingKind,
        SizingRule,
        TriggerKind,
        Universe,
    )
    from tradinglab.gui.entries_dialog import EntriesDialog

    strat = EntryStrategy(
        name="smoke-ga-long-AAPL", direction=Direction.LONG,
        universe=Universe(symbols=("AAPL",)),
        trigger=EntryTrigger(kind=TriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=100.0),
    )
    captured: list = []
    dlg = EntriesDialog(app, strategy=strat, on_save=captured.append)
    dlg.withdraw()
    try:
        _pump(app, 0.05)
        # Validate button runs without error (wiring check).
        vbtn = _find_button(dlg, "Validate")
        assert vbtn is not None, "EntriesDialog must expose a Validate button"
        _click(vbtn)
        _pump(app, 0.05)
        # Save & Close: valid strategy reaches on_save and the dialog closes.
        sbtn = _find_button(dlg, "Save & Close")
        assert sbtn is not None, "EntriesDialog must expose a Save & Close button"
        _click(sbtn)
        _pump(app, 0.05)
        assert len(captured) == 1
        assert captured[0].name == "smoke-ga-long-AAPL"
        assert not dlg.winfo_exists()
    finally:
        try:
            if dlg.winfo_exists():
                dlg.destroy()
        except tk.TclError:
            pass
        _settle_app(app)


# --------------------------------------------------------------------------
# Exits dialog — click + New, + Add leg, then Save; assert storage round-trip.
# --------------------------------------------------------------------------

@_skip_modal_on_darwin
def test_gui_action_exits_dialog_new_addleg_save(app):
    """Drive the ExitsDialog CRUD + Save buttons.

    ``+ New`` seeds a draft (enabling the add buttons), the real
    ``+ Add leg`` button appends a leg, and clicking the real ``Save``
    button persists a uniquely-named strategy that round-trips through
    exits storage. The persisted record is deleted in teardown so the
    shared (session-tmpdir) storage isn't polluted for later checks.
    """
    from tradinglab.exits import storage as _exits_storage
    from tradinglab.exits.model import (
        ExitLeg,
        ExitStrategy,
        ExitTrigger,
        TriggerKind,
    )
    from tradinglab.gui.exits_dialog import ExitsDialog

    dlg = ExitsDialog(app)
    dlg.withdraw()
    saved_id = None
    uniq = "smoke-ga-exit-roundtrip"
    try:
        _pump(app, 0.05)
        # + New enables the editor + add buttons.
        newbtn = _find_button(dlg, "+ New")
        assert newbtn is not None, "ExitsDialog must expose a + New button"
        _click(newbtn)
        _pump(app, 0.05)
        assert dlg.get_draft() is not None
        assert "disabled" not in dlg._add_leg_btn.state()

        # + Add leg (real widget) appends a leg.
        n0 = len(dlg.get_draft().legs)
        _click(dlg._add_leg_btn)
        _pump(app, 0.05)
        assert len(dlg.get_draft().legs) == n0 + 1

        # Save button round-trip: load a uniquely-named valid strategy and
        # click the real Save button; it must land in storage.
        strat = ExitStrategy(
            name=uniq,
            legs=[ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])])
        saved_id = strat.id
        dlg.load_strategy_into_editor(strat)
        sbtn = _find_button(dlg, "Save")
        assert sbtn is not None, "ExitsDialog must expose a Save button"
        _click(sbtn)
        _pump(app, 0.05)
        loaded, _broken = _exits_storage.load_all()
        assert any(x.name == uniq for x in loaded), (
            "clicking Save must persist the strategy to storage")
    finally:
        try:
            if saved_id:
                _exits_storage.delete(saved_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            dlg.destroy()
        except tk.TclError:
            pass
        _settle_app(app)


# --------------------------------------------------------------------------
# Custom Indicator Builder — flip authoring mode via the real combobox.
# --------------------------------------------------------------------------

@_skip_modal_on_darwin
def test_gui_action_custom_indicator_mode_toggle(app, tmp_path):
    """Switch the Custom Indicator Builder authoring mode via the Mode
    combobox and assert the composition body actually swaps.

    Conditions -> Expression mounts the expression Text; -> Python mounts
    the Python Text; -> Conditions re-mounts the BlockEditor canvas. No
    save (so the indicator registry is untouched)."""
    from tradinglab.gui.custom_indicator_dialog import CustomIndicatorDialog

    dlg = CustomIndicatorDialog(app, directory=tmp_path)
    dlg.withdraw()
    try:
        _pump(app, 0.05)
        assert dlg._mode_var.get() == "Conditions"
        assert dlg._rendered_mode == "Conditions"
        combo = dlg._mode_combo

        def _switch(mode: str) -> None:
            combo.set(mode)
            combo.event_generate("<<ComboboxSelected>>")
            _pump(app, 0.05)

        _switch("Expression")
        assert dlg._rendered_mode == "Expression"
        assert dlg._expr_text is not None, "Expression mode mounts an expression Text"

        _switch("Python")
        assert dlg._rendered_mode == "Python"
        assert dlg._python_text is not None, "Python mode mounts a Python Text"

        _switch("Conditions")
        assert dlg._rendered_mode == "Conditions"
        assert dlg._conditions_canvas is not None, (
            "Conditions mode re-mounts the BlockEditor canvas")
    finally:
        try:
            dlg.destroy()
        except tk.TclError:
            pass
        _settle_app(app)


# --------------------------------------------------------------------------
# Sandbox panel — type a watch note, click "Next bar", assert it committed.
# (Not a modal; runs on every platform. The Buy/Sell buttons open a modal
# and are covered by their own dialog's unit tests.)
# --------------------------------------------------------------------------

def test_gui_action_sandbox_day_note_commit_on_next_bar(app):
    """The daily watch note typed into the sandbox panel must be committed
    to the controller when the Next-bar button is clicked (the journaling
    capture path). Drives the real ``Next bar`` button, not ``_on_next_bar``.
    """
    import datetime as dt
    from zoneinfo import ZoneInfo

    from tradinglab.backtest.replay import SandboxController
    from tradinglab.backtest.session import ENGINE_VERSION, SessionSpec
    from tradinglab.gui.sandbox_panel import SandboxPanel
    from tradinglab.models import Candle

    et = ZoneInfo("America/New_York")
    day_open = dt.datetime(2024, 6, 6, 9, 30, tzinfo=et)
    bars = [
        Candle(date=day_open + dt.timedelta(minutes=5 * i),
               open=100.0 + i * 0.1, high=100.5 + i * 0.1,
               low=99.5 + i * 0.1, close=100.2 + i * 0.1,
               volume=1000, session="regular")
        for i in range(12)
    ]
    spec = SessionSpec(
        deck_seed=1, tickers=(), start_clock_iso="", slippage_bps=0.0,
        commission=0.0, engine_version=ENGINE_VERSION, starting_cash=10_000.0)

    pre_sb = app._sandbox
    pre_panel = app._sandbox_panel
    ctl = SandboxController(app=app)
    host = None
    try:
        ctl.start_session(
            spec=spec, session_date=dt.date(2024, 6, 6), interval="5m",
            reference_symbol="REF", reference_candles=bars, lookback_days=1,
            include_extended=False, display_intervals=["5m"])
        app._sandbox = ctl
        host = tk.Toplevel(app)
        panel = SandboxPanel(host, controller=ctl)
        app._sandbox_panel = panel
        _pump(app, 0.05)

        note = "SPY pulling back; AMD holding RS -- watching for the entry"
        panel._notes_text.delete("1.0", "end")
        panel._notes_text.insert("1.0", note)
        nextbtn = _find_button(panel, "Next bar")
        assert nextbtn is not None, "sandbox panel must expose a Next bar button"
        _click(nextbtn)
        _pump(app, 0.05)
        # Same-day advance: the committed note is retrievable for the day.
        assert ctl.current_day_note() == note
    finally:
        try:
            ctl.end_session()
        except Exception:  # noqa: BLE001
            pass
        app._sandbox = pre_sb
        app._sandbox_panel = pre_panel
        if host is not None:
            try:
                host.destroy()
            except tk.TclError:
                pass
        _settle_app(app)
