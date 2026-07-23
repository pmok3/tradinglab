"""Widget-level tests for the Performance View daily-journal pane.

Constructs a real (withdrawn) :class:`PerformanceView` and asserts the
``_journal_tree`` is populated from ``build_day_groups``: one expanded
parent per replay day, the day's watch note in the header, that day's
trades nested beneath, flat (note-only) days with no children, and the
Blind toggle swapping calendar dates for "Replay Day N".
"""
from __future__ import annotations

import contextlib
import datetime as _dt

import pytest

pytest.importorskip("tkinter")
import tkinter as tk  # noqa: E402

from tradinglab.backtest.journal import DecisionRecord, PostTradeReview
from tradinglab.backtest.session import SessionResult, SessionSpec
from tradinglab.gui import performance_view as performance_view_module
from tradinglab.gui.performance_view import PerformanceView


def _ts(year: int, month: int, day: int, hour: int = 14, minute: int = 30) -> int:
    return int(_dt.datetime(year, month, day, hour, minute,
                            tzinfo=_dt.timezone.utc).timestamp())


def _post(entry_ts: int, pnl: float, ref: str) -> PostTradeReview:
    return PostTradeReview(
        symbol="AMD", entry_ts=entry_ts, exit_ts=entry_ts + 3600,
        entry_price=10.0, exit_price=10.0 + pnl / 100.0, quantity=100.0,
        side="buy", pnl=pnl, pnl_pct=0.0, mae=0.0, mfe=0.0,
        mae_pct=0.0, mfe_pct=0.0, ref_pre_trade_id=ref,
    )


def _result_with_days() -> SessionResult:
    spec = SessionSpec(deck_seed=1, tickers=("AMD",), start_clock_iso="",
                       slippage_bps=0.0, commission=0.0)
    return SessionResult(
        spec=spec,
        post_trades=[
            _post(_ts(2025, 4, 29), 100.0, "a"),
            _post(_ts(2025, 4, 30), -20.0, "b"),
        ],
        day_notes={
            "2025-04-29": "SPY pulling back, NVDA holding RS",
            "2025-05-01": "watched all day, stood aside",
        },
        decisions=[
            DecisionRecord(
                ts=_ts(2025, 4, 29, 14, 25),
                symbol="AMD",
                action="watch",
                setup_tag="opening range",
                confidence=4,
                note="waiting for confirmation",
            ),
        ],
    )


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:  # pragma: no cover - no display
        pytest.skip("no display for Tk")
    r.withdraw()
    yield r
    with contextlib.suppress(tk.TclError):
        r.destroy()


def test_journal_pane_groups_days_and_trades(root):
    win = PerformanceView(root, _result_with_days(), title="test")
    try:
        tree = win._journal_tree
        day_nodes = tree.get_children("")
        # 3 days: 04-29 (1 trade), 04-30 (1 trade), 05-01 (flat note).
        assert len(day_nodes) == 3
        first_text = tree.item(day_nodes[0], "text")
        assert "Apr 29, 2025" in first_text
        assert "Day 1" in first_text
        # Header's detail column carries the note.
        assert "NVDA holding RS" in tree.item(day_nodes[0], "values")[1]
        # 04-29 has a decision followed by a trade; flat 05-01 has none.
        first_children = tree.get_children(day_nodes[0])
        assert len(first_children) == 2
        assert "DECISION · WATCH" in tree.item(
            first_children[0], "values")[1]
        assert "TRADE" in tree.item(first_children[1], "values")[1]
        assert len(tree.get_children(day_nodes[2])) == 0
        assert "1 logged decision(s) (logged decisions only)" in (
            win._summary_var.get())
    finally:
        with contextlib.suppress(tk.TclError):
            win.destroy()


def test_journal_blind_toggle_hides_dates(root):
    win = PerformanceView(root, _result_with_days(), title="test")
    try:
        win._journal_blind_var.set(True)
        win._populate_journal()
        tree = win._journal_tree
        labels = [tree.item(n, "text") for n in tree.get_children("")]
        assert labels[0] == "Replay Day 1"
        assert all("2025" not in lbl for lbl in labels)
    finally:
        with contextlib.suppress(tk.TclError):
            win.destroy()


def test_decision_only_session_can_export_decisions(
    root, tmp_path, monkeypatch,
):
    result = SessionResult(
        spec=SessionSpec(
            deck_seed=1,
            tickers=("AMD",),
            start_clock_iso="",
            slippage_bps=0.0,
            commission=0.0,
            decision_logging_enabled=True,
        ),
        decisions=[
            DecisionRecord(
                ts=_ts(2025, 5, 1, 15, 5),
                symbol="AMD",
                action="pass",
                setup_tag="failed breakout",
                confidence=5,
                note="No volume confirmation",
            ),
        ],
    )
    win = PerformanceView(root, result, title="test")
    try:
        assert str(win._export_btn["state"]) == "disabled"
        assert str(win._copy_btn["state"]) == "disabled"
        assert str(win._decision_export_btn["state"]) == "normal"
        assert len(win._journal_tree.get_children("")) == 1

        target = tmp_path / "decisions.csv"
        monkeypatch.setattr(
            performance_view_module.filedialog,
            "asksaveasfilename",
            lambda **_kwargs: str(target),
        )
        win._on_export_decisions_csv()
        assert target.is_file()
        assert "AMD,pass,failed breakout,5,No volume confirmation" in (
            target.read_text(encoding="utf-8"))
    finally:
        with contextlib.suppress(tk.TclError):
            win.destroy()
