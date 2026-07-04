"""EntriesTab widget tests.

Mirrors :mod:`tests.gui.test_exits_tab` patterns. Covers:

* construction + Treeview population
* dialog open/save/cancel paths
* arm/disarm/disarm-all calls evaluator
* delete prompts then removes from storage
* duplicate clones with fresh id
* import/export round-trip
* template loader (load_template_from_path)
* audit-tail panel renders entries
* stats panel renders EvaluatorStats
* selected_strategy_id reflects Treeview selection
"""
from __future__ import annotations

import json
import tkinter as tk
from datetime import datetime, timezone
from datetime import time as dtime
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

import pytest

from tradinglab.core import thread_guard
from tradinglab.entries import storage as _entries_storage
from tradinglab.entries.audit import AuditLog
from tradinglab.entries.evaluator import EntryEvaluator
from tradinglab.entries.model import (
    CreatedWith,
    Direction,
    EntryStrategy,
    EntryTrigger,
    SizingKind,
    SizingRule,
    TriggerKind,
    Universe,
)
from tradinglab.entries.signals import EntryPaperSink
from tradinglab.exits.paper_engine import PaperBrokerEngine
from tradinglab.gui.entries_tab import EntriesTab, _format_audit_record
from tradinglab.positions.tracker import PositionTracker

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_tk():
    with thread_guard.tk_thread_check_disabled():
        yield


@pytest.fixture(autouse=True)
def _wipe_entries_storage():
    """Each test gets a clean entries-storage dir."""
    sd = _entries_storage.storage_dir()
    if sd.exists():
        for p in sd.glob("*.json"):
            try:
                p.unlink()
            except OSError:
                pass
    yield
    if sd.exists():
        for p in sd.glob("*.json"):
            try:
                p.unlink()
            except OSError:
                pass


def _strategy(name: str = "test", *, direction=Direction.LONG) -> EntryStrategy:
    return EntryStrategy(
        name=name,
        direction=direction,
        universe=Universe(symbols=("AAPL",)),
        trigger=EntryTrigger(kind=TriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=100.0),
    )


def _make_evaluator(audit: AuditLog | None = None) -> EntryEvaluator:
    tracker = PositionTracker()
    engine = PaperBrokerEngine(tracker)
    sink = EntryPaperSink(engine)
    return EntryEvaluator(tracker=tracker, sink=sink, audit=audit)


def _make_tab(root: tk.Toplevel, **kwargs) -> EntriesTab:
    evaluator = kwargs.pop("evaluator", None) or _make_evaluator()
    storage = kwargs.pop("storage", _entries_storage)
    tab = EntriesTab(root, evaluator=evaluator, storage=storage, **kwargs)
    # Cancel the auto-tick to keep tests deterministic.
    if tab._tick_after_id is not None:
        try:
            tab.after_cancel(tab._tick_after_id)
        except (tk.TclError, ValueError):
            pass
        tab._tick_after_id = None
    tab.pack(fill="both", expand=True)
    return tab


# ---------------------------------------------------------------------------
# Format helper
# ---------------------------------------------------------------------------


def test_format_audit_record_compact():
    rec = {
        "ts": "2024-01-15T10:00:00+00:00",
        "kind": "entry_fire",
        "strategy_id": "abc123def456",
        "symbol": "AAPL",
        "position_id": "pid12345678",
        "qty": 100,
        "price": 150.5,
        "meta": {"reason": "ok"},
    }
    line = _format_audit_record(rec)
    assert "entry_fire" in line
    assert "sym=AAPL" in line
    assert "qty=100" in line
    assert "px=150.5" in line


def test_format_audit_record_renders_lookback_evidence():
    """When meta.evidence is present, each leaf is appended as an
    indented child line so the user sees what bar each underlying
    condition fired on."""
    rec = {
        "ts": "2024-01-15T10:40:00+00:00",
        "kind": "entry_fire",
        "strategy_id": "abc123def456",
        "symbol": "AAPL",
        "qty": 100,
        "price": 150.5,
        "meta": {
            "reason": "ok",
            "evidence": [
                {
                    "node_id": "c0d1e2f3g4h5",
                    "bars_ago": 1,
                    "timestamp": "2024-01-15T10:35:00",
                    "value": 180.5,
                },
                {
                    "node_id": "abcdef012345",
                    "bars_ago": 0,
                    "timestamp": "2024-01-15T10:40:00",
                    "value": None,
                },
            ],
        },
    }
    out = _format_audit_record(rec)
    lines = out.split("\n")
    # Head + 2 evidence lines.
    assert len(lines) == 3
    # Evidence renders most-recent first (caller order); each line has
    # the short node id, the relative bars phrase, and the time.
    assert "c0d1e2" in lines[1]
    assert "1 bar ago" in lines[1]
    assert "10:35:00" in lines[1]
    assert "abcdef" in lines[2]
    assert "this bar" in lines[2]
    assert "10:40:00" in lines[2]


def test_format_audit_record_no_evidence_is_single_line():
    rec = {
        "ts": "2024-01-15T10:00:00+00:00",
        "kind": "entry_fire",
        "strategy_id": "abc",
        "symbol": "AAPL",
        "meta": {"reason": "ok"},
    }
    out = _format_audit_record(rec)
    assert "\n" not in out


# ---------------------------------------------------------------------------
# Construction / refresh
# ---------------------------------------------------------------------------


def test_construction_empty_library(root):
    tab = _make_tab(root)
    assert tab.library == ()
    assert tab.selected_strategy_id is None
    assert len(tab._tree.get_children("")) == 0
    tab.destroy()


def test_construction_populates_tree_from_storage(root):
    s1 = _strategy("alpha")
    s2 = _strategy("beta", direction=Direction.SHORT)
    _entries_storage.save(s1)
    _entries_storage.save(s2)
    tab = _make_tab(root)
    iids = tab._tree.get_children("")
    assert s1.id in iids
    assert s2.id in iids
    # Sorted by name → alpha before beta.
    assert tab.library[0].name == "alpha"
    tab.destroy()


def test_evaluator_set_strategies_called_on_refresh(root):
    s = _strategy("one")
    _entries_storage.save(s)
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev)
    assert ev.get_strategy(s.id) is not None
    tab.destroy()


# ---------------------------------------------------------------------------
# Selection / arm / disarm
# ---------------------------------------------------------------------------


def test_arm_calls_evaluator(root):
    s = _strategy("arm-me")
    _entries_storage.save(s)
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev)
    tab._tree.selection_set(s.id)
    tab._on_arm()
    assert ev.is_armed(s.id)
    # Tree row's "Armed" column should now read "yes".
    vals = tab._tree.item(s.id, "values")
    assert vals[4] == "yes"
    tab.destroy()


def test_disarm_calls_evaluator(root):
    s = _strategy("disarm-me")
    _entries_storage.save(s)
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev)
    tab._tree.selection_set(s.id)
    tab._on_arm()
    tab._on_disarm()
    assert not ev.is_armed(s.id)
    tab.destroy()


def test_disarm_all_calls_evaluator(root):
    s1 = _strategy("a")
    s2 = _strategy("b")
    _entries_storage.save(s1)
    _entries_storage.save(s2)
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev)
    ev.arm(s1.id)
    ev.arm(s2.id)
    tab._on_disarm_all()
    assert ev.armed_strategies() == set()
    tab.destroy()


def test_no_selection_arm_is_noop(root):
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev)
    # Should not raise.
    tab._on_arm()
    tab._on_disarm()
    tab._on_edit()
    tab._on_delete()
    tab._on_duplicate()
    tab._on_export()
    tab.destroy()


# ---------------------------------------------------------------------------
# Delete / Duplicate
# ---------------------------------------------------------------------------


def test_delete_removes_from_storage(root):
    s = _strategy("delete-me")
    _entries_storage.save(s)
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev)
    tab._tree.selection_set(s.id)
    with patch("tradinglab.gui.entries_tab.messagebox.askyesno",
               return_value=True):
        tab._on_delete()
    with pytest.raises(FileNotFoundError):
        _entries_storage.load(s.id)
    assert s.id not in tab._tree.get_children("")
    tab.destroy()


def test_delete_cancelled_keeps_strategy(root):
    s = _strategy("keep-me")
    _entries_storage.save(s)
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev)
    tab._tree.selection_set(s.id)
    with patch("tradinglab.gui.entries_tab.messagebox.askyesno",
               return_value=False):
        tab._on_delete()
    assert _entries_storage.load(s.id) is not None  # raises if missing
    tab.destroy()


def test_duplicate_creates_fresh_id(root):
    s = _strategy("dup-source")
    _entries_storage.save(s)
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev)
    tab._tree.selection_set(s.id)
    tab._on_duplicate()
    # Library should now have 2 items.
    assert len(tab.library) == 2
    names = sorted(s.name for s in tab.library)
    assert "dup-source" in names
    assert any(name.endswith("(copy)") for name in names)
    # Different ids.
    ids = {s.id for s in tab.library}
    assert len(ids) == 2
    tab.destroy()


# ---------------------------------------------------------------------------
# Import / Export
# ---------------------------------------------------------------------------


def test_export_then_import_round_trip(root, tmp_path: Path):
    s = _strategy("portable")
    _entries_storage.save(s)
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev)
    tab._tree.selection_set(s.id)
    out_path = tmp_path / "exported.json"
    with patch("tradinglab.gui.entries_tab.filedialog.asksaveasfilename",
               return_value=str(out_path)):
        tab._on_export()
    assert out_path.exists()
    # Wipe and re-import.
    _entries_storage.delete(s.id)
    tab.refresh()
    assert len(tab.library) == 0
    with patch("tradinglab.gui.entries_tab.filedialog.askopenfilename",
               return_value=str(out_path)):
        tab._on_import()
    assert len(tab.library) == 1
    assert tab.library[0].name == "portable"
    tab.destroy()


# ---------------------------------------------------------------------------
# Theming (dark mode cascade target)
# ---------------------------------------------------------------------------


def test_apply_theme_dark_paints_text_widgets(root):
    """``EntriesTab._apply_theme(DARK_THEME)`` flips the audit / stats
    ``tk.Text`` panes to the dark palette.

    Regression for the "Entries tab is not colour aligned with dark
    mode" report — ttk.Style does NOT cover classic ``tk.Text`` widgets,
    so the cascade hook on ``EntriesTab`` is what flips them.
    """
    from tradinglab.constants import DARK_THEME
    tab = _make_tab(root)
    try:
        tab._apply_theme(DARK_THEME)
        for txt in (tab._audit_txt, tab._stats_txt):
            assert str(txt.cget("background")) == DARK_THEME["ax_bg"]
            assert str(txt.cget("foreground")) == DARK_THEME["text"]
            assert str(txt.cget("insertbackground")) == DARK_THEME["text"]
            assert str(txt.cget("selectbackground")) == DARK_THEME["spine"]
            assert str(txt.cget("selectforeground")) == DARK_THEME["text"]
    finally:
        tab.destroy()


def test_apply_theme_light_paints_text_widgets(root):
    """Mirror of the dark-mode test: light palette restores the
    light-mode colours after a prior dark-mode call.
    """
    from tradinglab.constants import DARK_THEME, LIGHT_THEME
    tab = _make_tab(root)
    try:
        tab._apply_theme(DARK_THEME)
        tab._apply_theme(LIGHT_THEME)
        for txt in (tab._audit_txt, tab._stats_txt):
            assert str(txt.cget("background")) == LIGHT_THEME["ax_bg"]
            assert str(txt.cget("foreground")) == LIGHT_THEME["text"]
    finally:
        tab.destroy()


def test_apply_theme_empty_dict_is_noop(root):
    """Defensive: passing an empty dict is treated as a no-op (no
    KeyError, no widget mutation). Mirrors the ``not theme: return``
    guard in :meth:`EntriesTab._apply_theme`.
    """
    tab = _make_tab(root)
    try:
        before_bg = str(tab._audit_txt.cget("background"))
        tab._apply_theme({})  # type: ignore[arg-type]
        assert str(tab._audit_txt.cget("background")) == before_bg
    finally:
        tab.destroy()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def test_load_template_from_path_creates_new_id(root, tmp_path: Path):
    src = _strategy("template-source")
    template_path = tmp_path / "template.json"
    template_path.write_text(json.dumps(src.to_dict()), encoding="utf-8")
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev, templates_dir=tmp_path)
    saved = tab.load_template_from_path(template_path)
    assert saved.id != src.id
    assert saved.name == src.name
    assert saved.created_with.template is True
    tab.destroy()


def test_default_templates_dir_resolves(root):
    """The class-level templates dir should point at data/entry_strategy_templates."""
    assert EntriesTab.DEFAULT_TEMPLATES_DIR.name == "entry_strategy_templates"


# ---------------------------------------------------------------------------
# Audit / stats panes
# ---------------------------------------------------------------------------


def test_audit_pane_renders_records(root):
    audit = AuditLog()
    audit.append("entry_arm", strategy_id="abc")
    ev = _make_evaluator(audit=audit)
    tab = _make_tab(root, evaluator=ev)
    body = tab._audit_txt.get("1.0", "end")
    assert "entry_arm" in body
    tab.destroy()


def test_stats_pane_renders_counters(root):
    ev = _make_evaluator()
    ev._stats.fires = 7
    ev._stats.blocked = 3
    tab = _make_tab(root, evaluator=ev)
    body = tab._stats_txt.get("1.0", "end")
    assert "fires:" in body and "7" in body
    assert "blocked:" in body
    tab.destroy()


# ---------------------------------------------------------------------------
# Dialog interaction
# ---------------------------------------------------------------------------


def test_on_dialog_save_persists_and_refreshes(root):
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev)
    new_strat = _strategy("from-dialog")
    tab._on_dialog_save(new_strat)
    loaded = _entries_storage.load(new_strat.id)
    assert loaded is not None
    assert new_strat.id in tab._tree.get_children("")
    tab.destroy()


# ---------------------------------------------------------------------------
# Mine | Templates | All filter (audit ``template-filter``)
# ---------------------------------------------------------------------------


def _template_strategy(name: str, tmpl_id: str) -> EntryStrategy:
    """A bundled-seed-style strategy: same shape as ``_strategy`` but with
    a ``tmpl-`` id (the marker the filter keys on)."""
    s = _strategy(name)
    s.id = tmpl_id
    return s


def test_filter_defaults_to_all_and_shows_templates(root):
    user1 = _strategy("My Long")
    user2 = _strategy("My Short", direction=Direction.SHORT)
    tmpl1 = _template_strategy("Starter A", "tmpl-starter-a")
    tmpl2 = _template_strategy("Starter B", "tmpl-starter-b")
    for s in (user1, user2, tmpl1, tmpl2):
        _entries_storage.save(s)
    tab = _make_tab(root)
    try:
        assert tab._filter_var.get() == "all"
        # Default "All" shows the user's strategies AND bundled templates.
        assert set(tab._tree.get_children("")) == {
            user1.id, user2.id, "tmpl-starter-a", "tmpl-starter-b"}
    finally:
        tab.destroy()


def test_filter_templates_then_all(root):
    user1 = _strategy("My Long")
    tmpl1 = _template_strategy("Starter A", "tmpl-starter-a")
    tmpl2 = _template_strategy("Starter B", "tmpl-starter-b")
    for s in (user1, tmpl1, tmpl2):
        _entries_storage.save(s)
    tab = _make_tab(root)
    try:
        tab._filter_var.set("templates")
        tab._on_filter_change()
        assert set(tab._tree.get_children("")) == {
            "tmpl-starter-a", "tmpl-starter-b"}
        tab._filter_var.set("all")
        tab._on_filter_change()
        assert set(tab._tree.get_children("")) == {
            user1.id, "tmpl-starter-a", "tmpl-starter-b"}
    finally:
        tab.destroy()


def test_loaded_template_copy_counts_as_mine(root):
    """A copy the user makes via "Load template…" / Duplicate gets a fresh
    UUID id but ``created_with.template=True``. Per the chosen design it
    must show under "Mine" — so the filter keys on the ``tmpl-`` id
    prefix, NOT ``created_with.template``."""
    copy = _strategy("Loaded copy")
    copy.created_with = CreatedWith(
        app="tradinglab", version="1.0.0", template=True)
    assert not copy.id.startswith("tmpl-")
    seed = _template_strategy("Seed", "tmpl-seed")
    assert EntriesTab._is_template(copy) is False
    assert EntriesTab._is_template(seed) is True
    _entries_storage.save(copy)
    _entries_storage.save(seed)
    tab = _make_tab(root)
    try:
        tab._filter_var.set("mine")
        tab._on_filter_change()
        children = set(tab._tree.get_children(""))  # "mine" filter
        assert copy.id in children
        assert "tmpl-seed" not in children
    finally:
        tab.destroy()


def test_filter_labels_show_counts(root):
    _entries_storage.save(_strategy("U1"))
    _entries_storage.save(_template_strategy("T1", "tmpl-1"))
    _entries_storage.save(_template_strategy("T2", "tmpl-2"))
    tab = _make_tab(root)
    try:
        assert tab._filter_buttons["mine"].cget("text") == "Mine (1)"
        assert tab._filter_buttons["templates"].cget("text") == "Templates (2)"
        assert tab._filter_buttons["all"].cget("text") == "All (3)"
        # Nothing armed here → Active (0).
        assert tab._filter_buttons["active"].cget("text") == "Active (0)"
    finally:
        tab.destroy()


def test_active_filter_shows_only_enabled_and_armed(root):
    """The "Active" view shows only strategies that are BOTH enabled AND
    armed (the live alerts) — a decluttered slice of the library."""
    s_active = _strategy("Active One")          # enabled (default) + armed
    s_unarmed = _strategy("Enabled Unarmed")    # enabled + NOT armed
    s_disabled = _strategy("Disabled Armed")    # armed but DISABLED
    s_disabled.enabled = False
    for s in (s_active, s_unarmed, s_disabled):
        _entries_storage.save(s)
    tab = _make_tab(root)
    try:
        # Both s_active and s_disabled are "armed", but only s_active is
        # also enabled → Active shows only s_active.
        tab._evaluator.armed_strategies = lambda: {s_active.id, s_disabled.id}
        tab._filter_var.set("active")
        tab._on_filter_change()
        assert set(tab._tree.get_children("")) == {s_active.id}
        # Count reflects enabled AND armed = 1 (over the whole library).
        assert tab._filter_buttons["active"].cget("text") == "Active (1)"
    finally:
        tab.destroy()


def test_active_filter_empty_shows_hint(root):
    """No enabled+armed strategy → Active view is empty with a helpful hint."""
    s = _strategy("Enabled Unarmed")  # enabled but not armed
    _entries_storage.save(s)
    tab = _make_tab(root)
    try:
        tab._evaluator.armed_strategies = lambda: set()
        tab._filter_var.set("active")
        tab._on_filter_change()
        assert set(tab._tree.get_children("")) == set()
        assert "No enabled + armed" in tab._filter_empty_hint.cget("text")
    finally:
        tab.destroy()


# ---------------------------------------------------------------------------
# Intraday-interval arm guard (audit ``intraday-interval-guard``)
# ---------------------------------------------------------------------------


def _vwap_entry(
    name: str, *, cond_interval: str = "5m", trigger_interval: str = "5m",
) -> EntryStrategy:
    """An INDICATOR entry whose condition reads ``close > vwap``."""
    from tradinglab.scanner.model import OP_GT, Condition, FieldRef, Group

    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={"right": FieldRef(kind="indicator", id="vwap")},
                interval=cond_interval,
            ),
        ],
    )
    return EntryStrategy(
        name=name,
        direction=Direction.LONG,
        universe=Universe(symbols=("AAPL",)),
        trigger=EntryTrigger(
            kind=TriggerKind.INDICATOR, condition=cond,
            interval=trigger_interval,
        ),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=100.0),
    )


def test_arm_blocks_daily_vwap_strategy_live(root):
    """A VWAP strategy authored on a daily condition can never fire (VWAP
    is NaN on daily) — arming must be blocked with a popup, not armed."""
    s = _vwap_entry("Daily VWAP", cond_interval="1d", trigger_interval="1d")
    _entries_storage.save(s)
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev)
    try:
        tab._tree.selection_set(s.id)
        with patch("tradinglab.gui.entries_tab.messagebox.showerror") as err:
            tab._on_arm()
        assert err.called, "expected an error popup blocking the arm"
        assert not ev.is_armed(s.id), "strategy must NOT be armed"
    finally:
        tab.destroy()


def test_arm_allows_5m_vwap_strategy_live(root):
    """The same VWAP strategy on a 5m condition works live (5m bars are
    fetchable) — arming must proceed normally."""
    s = _vwap_entry("5m VWAP", cond_interval="5m", trigger_interval="5m")
    _entries_storage.save(s)
    ev = _make_evaluator()
    tab = _make_tab(root, evaluator=ev)
    try:
        tab._tree.selection_set(s.id)
        with patch("tradinglab.gui.entries_tab.messagebox.showerror") as err:
            tab._on_arm()
        assert not err.called, "no error popup expected for a 5m strategy"
        assert ev.is_armed(s.id), "5m strategy must arm normally"
    finally:
        tab.destroy()


def test_arm_blocks_5m_strategy_in_1d_sandbox(root):
    """In a 1d-only sandbox, a 5m strategy can't be fed its bars — arming
    must be blocked even though it's a perfectly valid live strategy."""
    s = _vwap_entry("5m VWAP", cond_interval="5m", trigger_interval="5m")
    _entries_storage.save(s)
    ev = _make_evaluator()
    tab = _make_tab(
        root, evaluator=ev,
        sandbox_intervals_provider=lambda: frozenset({"1d"}),
    )
    try:
        tab._tree.selection_set(s.id)
        with patch("tradinglab.gui.entries_tab.messagebox.showerror") as err:
            tab._on_arm()
        assert err.called, "expected an error popup in a 1d sandbox"
        assert not ev.is_armed(s.id)
    finally:
        tab.destroy()


def test_arm_allows_market_entry_in_1d_sandbox(root):
    """A MARKET entry has no condition tree and fires on the tick — it must
    arm fine even in a 1d-only sandbox (no false positive)."""
    s = _strategy("Market")
    _entries_storage.save(s)
    ev = _make_evaluator()
    tab = _make_tab(
        root, evaluator=ev,
        sandbox_intervals_provider=lambda: frozenset({"1d"}),
    )
    try:
        tab._tree.selection_set(s.id)
        with patch("tradinglab.gui.entries_tab.messagebox.showerror") as err:
            tab._on_arm()
        assert not err.called
        assert ev.is_armed(s.id)
    finally:
        tab.destroy()
