"""ExitsTab widget tests.

Coverage (~12 cases):
- Construction: empty positions / empty library
- Toolbar buttons present (Edit / PANIC / Refresh) + badge
- Refresh populates attach panel rows for each open position
- "NO EXITS" warning shown when no strategy attached
- Attach action calls evaluator.attach_strategy and refreshes
- Detach action calls evaluator.detach_strategy after confirm
- Status Treeview has 1 row per (position, leg, trigger) for attached strategies
- Status state field shows "ARMED" / "DISARMED" / "BROKEN" / "FIRED×N"
- Status diff-update preserves selection across refreshes
- Two-phase panic flatten: first click arms, second flattens
- Panic flatten loops over positions calling evaluator.panic_flatten + submit_market_flatten
- Audit tail surfaces audit log lines
- Broken-strategy badge surfaces count
"""
from __future__ import annotations

import json
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

import pytest

from tradinglab.exits import storage as _exits_storage
from tradinglab.exits.audit import AuditLog
from tradinglab.exits.evaluator import ExitEvaluator
from tradinglab.exits.model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
    TriggerKind,
)
from tradinglab.exits.signals import ExitSignal
from tradinglab.gui.exits_tab import ExitsTab, _AttachRow, _format_audit_record
from tradinglab.positions.tracker import PositionTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingSink:
    """Minimal ExitSignalSink for tests — records calls, returns ids."""

    def __init__(self) -> None:
        self.submitted: List[ExitSignal] = []
        self.cancels: List[str] = []
        self.cancel_alls: List[str] = []
        self._counter = 0

    def submit(self, signal: ExitSignal) -> str:
        self.submitted.append(signal)
        order_id = f"order-{self._counter}"
        self._counter += 1
        return order_id

    def cancel(self, order_id: str) -> bool:
        self.cancels.append(order_id)
        return True

    def cancel_all_for_position(self, position_id: str) -> int:
        self.cancel_alls.append(position_id)
        return 0

    def working_order_ids_for_position(self, position_id: str) -> List[str]:
        return []


def _save_strategy(name: str = "S1") -> ExitStrategy:
    s = ExitStrategy(
        name=name,
        legs=[ExitLeg(label="exit",
                      triggers=[ExitTrigger(kind=TriggerKind.MARKET)])],
    )
    _exits_storage.save(s)
    return s


def _clear_storage() -> None:
    d = _exits_storage.exit_strategies_dir()
    for f in d.glob("*.json"):
        try:
            f.unlink()
        except Exception:  # noqa: BLE001
            pass


def _make_tab(root: tk.Toplevel, *, with_audit: bool = False):
    tracker = PositionTracker()
    sink = _RecordingSink()
    audit = AuditLog() if with_audit else None
    evaluator = ExitEvaluator(tracker=tracker, sink=sink, audit=audit)
    tab = ExitsTab(
        root, tracker=tracker, evaluator=evaluator, audit=audit,
    )
    return tab, tracker, sink, evaluator


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction_empty(root: tk.Toplevel) -> None:
    _clear_storage()
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        assert tab.library == ()
        assert tab.broken_count == 0
        assert tab.winfo_exists()
    finally:
        evaluator.close()
        tab.destroy()


def test_toolbar_has_buttons(root: tk.Toplevel) -> None:
    _clear_storage()
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        # Walk children to find PANIC button
        descendants: List[tk.Misc] = []

        def _walk(w: tk.Misc) -> None:
            for c in w.winfo_children():
                descendants.append(c)
                _walk(c)
        _walk(tab)
        labels: List[str] = []
        for w in descendants:
            try:
                labels.append(str(w.cget("text")))
            except (tk.TclError, AttributeError):
                pass
        joined = " | ".join(labels)
        assert "PANIC: Flatten All" in joined
        assert "Edit Strategies" in joined or "Edit Strategies…" in joined
        assert "Refresh" in joined
    finally:
        evaluator.close()
        tab.destroy()


# ---------------------------------------------------------------------------
# Attach panel
# ---------------------------------------------------------------------------


def test_attach_panel_shows_row_per_open_position(root: tk.Toplevel) -> None:
    _clear_storage()
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        tracker.open(symbol="AAPL", side="long", qty=100, price=180.0,
                     source="manual")
        tracker.open(symbol="MSFT", side="short", qty=50, price=420.0,
                     source="manual")
        tab.refresh()
        assert len(tab._attach_rows) == 2
        # Both rows have NO EXITS warning
        for row in tab._attach_rows.values():
            assert "NO EXITS" in row._warning_var.get()
    finally:
        evaluator.close()
        tab.destroy()


def test_attach_panel_no_warning_when_strategy_attached(root: tk.Toplevel) -> None:
    _clear_storage()
    s = _save_strategy("test-strat")
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        pos = tracker.open(symbol="AAPL", side="long", qty=100, price=180.0,
                           source="manual")
        evaluator.attach_strategy(pos.id, s)
        tab.refresh()
        row = tab._attach_rows[pos.id]
        assert row._warning_var.get() == ""
    finally:
        evaluator.close()
        tab.destroy()


def test_attach_strategy_for_calls_evaluator(root: tk.Toplevel) -> None:
    _clear_storage()
    s = _save_strategy("strat-attach")
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        pos = tracker.open(symbol="AAPL", side="long", qty=100, price=180.0,
                           source="manual")
        tab.refresh()
        tab.attach_strategy_for(pos.id, s.id)
        assert evaluator.attached_strategy(pos.id) is not None
        assert evaluator.attached_strategy(pos.id).id == s.id
    finally:
        evaluator.close()
        tab.destroy()


def test_detach_strategy_for_calls_evaluator(root: tk.Toplevel) -> None:
    _clear_storage()
    s = _save_strategy("strat-detach")
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        pos = tracker.open(symbol="AAPL", side="long", qty=100, price=180.0,
                           source="manual")
        evaluator.attach_strategy(pos.id, s)
        # Suppress the confirmation dialog
        with patch("tradinglab.gui.exits_tab.messagebox.askyesno",
                   return_value=True):
            tab.detach_strategy_for(pos.id)
        assert evaluator.attached_strategy(pos.id) is None
    finally:
        evaluator.close()
        tab.destroy()


# ---------------------------------------------------------------------------
# Status Treeview
# ---------------------------------------------------------------------------


def test_status_tree_has_row_per_trigger(root: tk.Toplevel) -> None:
    _clear_storage()
    s = ExitStrategy(
        name="multi",
        legs=[
            ExitLeg(label="A",
                    triggers=[ExitTrigger(kind=TriggerKind.MARKET)]),
            ExitLeg(label="B",
                    triggers=[ExitTrigger(kind=TriggerKind.STOP, price=170.0),
                              ExitTrigger(kind=TriggerKind.LIMIT, price=200.0)]),
        ],
    )
    _exits_storage.save(s)
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        pos = tracker.open(symbol="AAPL", side="long", qty=100, price=180.0,
                           source="manual")
        evaluator.attach_strategy(pos.id, s)
        tab.refresh()
        items = tab._tree.get_children("")
        # 1 trigger in leg A + 2 in leg B = 3
        assert len(items) == 3
        # Each row's iid encodes (pos|leg|trigger)
        for iid in items:
            assert iid.startswith(pos.id)
    finally:
        evaluator.close()
        tab.destroy()


def test_status_tree_state_field(root: tk.Toplevel) -> None:
    _clear_storage()
    s = ExitStrategy(
        name="x",
        legs=[ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])],
    )
    _exits_storage.save(s)
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        pos = tracker.open(symbol="AAPL", side="long", qty=100, price=180.0,
                           source="manual")
        evaluator.attach_strategy(pos.id, s)
        tab.refresh()
        items = tab._tree.get_children("")
        assert len(items) == 1
        values = tab._tree.item(items[0], "values")
        # state column is index 6 in _TREEVIEW_COLS
        assert values[6] == "ARMED"
    finally:
        evaluator.close()
        tab.destroy()


def test_status_tree_diff_update_preserves_selection(root: tk.Toplevel) -> None:
    _clear_storage()
    s = ExitStrategy(
        name="x",
        legs=[ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])],
    )
    _exits_storage.save(s)
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        pos = tracker.open(symbol="AAPL", side="long", qty=100, price=180.0,
                           source="manual")
        evaluator.attach_strategy(pos.id, s)
        tab.refresh()
        items = tab._tree.get_children("")
        assert items
        tab._tree.selection_set(items[0])
        # Refresh should preserve the selected iid
        tab.refresh()
        assert items[0] in tab._tree.selection()
    finally:
        evaluator.close()
        tab.destroy()


# ---------------------------------------------------------------------------
# PANIC two-phase
# ---------------------------------------------------------------------------


def test_panic_first_click_arms(root: tk.Toplevel) -> None:
    _clear_storage()
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        with patch("tradinglab.gui.exits_tab.messagebox.askyesno",
                   return_value=True):
            tab._on_panic_clicked()
        assert tab._panic_armed is True
        assert "Confirm" in tab._panic_btn.cget("text")
    finally:
        evaluator.close()
        tab.destroy()


def test_panic_second_click_flattens_all_positions(root: tk.Toplevel) -> None:
    _clear_storage()
    s = _save_strategy("s")
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        p1 = tracker.open(symbol="AAPL", side="long", qty=100, price=180.0,
                          source="manual")
        p2 = tracker.open(symbol="MSFT", side="long", qty=50, price=420.0,
                          source="manual")
        evaluator.attach_strategy(p1.id, s)
        evaluator.attach_strategy(p2.id, s)
        # First click arms
        with patch("tradinglab.gui.exits_tab.messagebox.askyesno",
                   return_value=True):
            tab._on_panic_clicked()
            assert tab._panic_armed is True
            # Second click executes
            tab._on_panic_clicked()
        # cancel_all should have been called for each position
        assert sorted(sink.cancel_alls) == sorted([p1.id, p2.id])
        # market exits submitted for both
        assert len(sink.submitted) == 2
        # Disarmed after execution
        assert tab._panic_armed is False
    finally:
        evaluator.close()
        tab.destroy()


def test_panic_first_click_cancelled_does_not_arm(root: tk.Toplevel) -> None:
    _clear_storage()
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        with patch("tradinglab.gui.exits_tab.messagebox.askyesno",
                   return_value=False):
            tab._on_panic_clicked()
        assert tab._panic_armed is False
    finally:
        evaluator.close()
        tab.destroy()


# ---------------------------------------------------------------------------
# Broken-strategy badge
# ---------------------------------------------------------------------------


def test_badge_surfaces_broken_count(root: tk.Toplevel) -> None:
    _clear_storage()
    # Write an intentionally-broken strategy file: invalid trigger.
    d = _exits_storage.exit_strategies_dir()
    bogus_path = d / "deadbeef-cafe-baad-cafe-cafe.json"
    payload = {
        "id": "deadbeef-cafe-baad-cafe-cafe",
        "name": "broken-one",
        "legs": [{
            "id": "leg1",
            "label": "x",
            "enabled": True,
            "triggers": [{
                # STOP with no price/offset → invalid
                "id": "trigger1",
                "kind": "stop",
                "qty_pct": 100.0,
            }],
        }],
        "oco_groups": [],
        "eod_kill_switch": True,
        "eod_offset_min": 5,
        "schema_version": 1,
        "created_with": {"app": "tradinglab", "version": "0"},
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "extra": {},
    }
    bogus_path.write_text(json.dumps(payload), encoding="utf-8")

    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        assert tab.broken_count >= 1
        assert "needing attention" in tab._badge_var.get()
    finally:
        evaluator.close()
        tab.destroy()


# ---------------------------------------------------------------------------
# Audit tail
# ---------------------------------------------------------------------------


def test_audit_tail_surfaces_lines(root: tk.Toplevel) -> None:
    _clear_storage()
    tab, tracker, sink, evaluator = _make_tab(root, with_audit=True)
    try:
        evaluator._audit.append(  # pyright: ignore[reportOptionalMemberAccess]
            "strategy_attach",
            strategy_id="s",
            position_id="p",
        )
        tab.refresh()
        text = tab._audit_txt.get("1.0", "end")
        assert "strategy_attach" in text
    finally:
        evaluator.close()
        tab.destroy()


# ---------------------------------------------------------------------------
# Closed positions removed from attach panel
# ---------------------------------------------------------------------------


def test_closed_position_removed_from_attach_panel(root: tk.Toplevel) -> None:
    _clear_storage()
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        p = tracker.open(symbol="AAPL", side="long", qty=100, price=180.0,
                         source="manual")
        tab.refresh()
        assert p.id in tab._attach_rows
        # Close the position
        tracker.apply_fill(position_id=p.id, qty=100, price=185.0)
        tab.refresh()
        assert p.id not in tab._attach_rows
    finally:
        evaluator.close()
        tab.destroy()


# ---------------------------------------------------------------------------
# Theming (dark mode cascade target)
# ---------------------------------------------------------------------------


def test_apply_theme_dark_paints_audit_text(root: tk.Toplevel) -> None:
    """``ExitsTab._apply_theme(DARK_THEME)`` flips the audit ``tk.Text``
    pane to the dark palette.

    Sibling regression to the EntriesTab dark-mode fix — same root
    cause (ttk.Style does not cover ``tk.Text``).
    """
    from tradinglab.constants import DARK_THEME
    _clear_storage()
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        tab._apply_theme(DARK_THEME)
        txt = tab._audit_txt
        assert str(txt.cget("background")) == DARK_THEME["ax_bg"]
        assert str(txt.cget("foreground")) == DARK_THEME["text"]
        assert str(txt.cget("insertbackground")) == DARK_THEME["text"]
        assert str(txt.cget("selectbackground")) == DARK_THEME["spine"]
        assert str(txt.cget("selectforeground")) == DARK_THEME["text"]
    finally:
        evaluator.close()
        tab.destroy()


def test_apply_theme_light_restores_palette(root: tk.Toplevel) -> None:
    """A subsequent light-theme call restores the light-mode colours
    after a dark-mode call.
    """
    from tradinglab.constants import LIGHT_THEME, DARK_THEME
    _clear_storage()
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        tab._apply_theme(DARK_THEME)
        tab._apply_theme(LIGHT_THEME)
        txt = tab._audit_txt
        assert str(txt.cget("background")) == LIGHT_THEME["ax_bg"]
        assert str(txt.cget("foreground")) == LIGHT_THEME["text"]
    finally:
        evaluator.close()
        tab.destroy()


def test_apply_theme_empty_dict_is_noop(root: tk.Toplevel) -> None:
    """Empty-dict input is a defensive no-op (matches the
    ``if not theme: return`` guard).
    """
    _clear_storage()
    tab, tracker, sink, evaluator = _make_tab(root)
    try:
        before_bg = str(tab._audit_txt.cget("background"))
        tab._apply_theme({})
        assert str(tab._audit_txt.cget("background")) == before_bg
    finally:
        evaluator.close()
        tab.destroy()


# ---------------------------------------------------------------------------
# _format_audit_record + lookback evidence rendering
# ---------------------------------------------------------------------------


def test_format_audit_record_compact_no_evidence() -> None:
    rec = {
        "ts": "2024-01-15T10:00:00+00:00",
        "kind": "fire",
        "strategy_id": "abc123def456",
        "position_id": "pid12345678",
        "qty": 100,
        "price": 150.5,
        "meta": {"reason": "indicator_true", "kind": "market"},
    }
    out = _format_audit_record(rec)
    assert "fire" in out
    assert "qty=100" in out
    assert "px=150.5" in out
    assert "\n" not in out


def test_format_audit_record_renders_lookback_evidence() -> None:
    """When the exits ``Decision.evidence`` cascades into the fire
    audit record's ``meta["evidence"]``, each leaf is rendered as an
    indented child line."""
    rec = {
        "ts": "2024-01-15T10:40:00+00:00",
        "kind": "fire",
        "strategy_id": "abc123",
        "position_id": "pid001",
        "qty": 100,
        "price": 150.0,
        "meta": {
            "reason": "indicator_true",
            "kind": "market",
            "evidence": [
                {
                    "node_id": "c0d1e2f3g4h5",
                    "bars_ago": 2,
                    "timestamp": "2024-01-15T10:30:00",
                    "value": 99.0,
                },
            ],
        },
    }
    out = _format_audit_record(rec)
    lines = out.split("\n")
    assert len(lines) == 2
    assert "c0d1e2" in lines[1]
    assert "2 bars ago" in lines[1]
    assert "10:30:00" in lines[1]


def test_format_audit_record_evidence_zero_bars_ago_says_this_bar() -> None:
    rec = {
        "ts": "2024-01-15T10:40:00+00:00",
        "kind": "fire",
        "strategy_id": "x",
        "meta": {
            "evidence": [
                {
                    "node_id": "n1",
                    "bars_ago": 0,
                    "timestamp": "2024-01-15T10:40:00",
                    "value": 1.0,
                },
            ],
        },
    }
    out = _format_audit_record(rec)
    assert "this bar" in out
