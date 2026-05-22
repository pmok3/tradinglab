"""ScannerTab widget tests.

Mirrors the test_block_editor.py session-scoped Tk pattern (Windows ARM64
destroy/recreate is flaky). Each test gets a fresh Toplevel so widget
state is isolated.

Coverage:
- Construction (empty / with library)
- add_scan / delete_scan + callbacks fire
- Library reorder by name
- Rename mutates definition + tab text
- Import-rename on collision (mocked simpledialog)
- Export round-trip (writes JSON parseable by from_dict)
- set_results updates the active sub-tab tree
- View toggle (New vs Active) filters rows
- Selection preserved across set_results ticks
- Sort reverses on header click
"""
from __future__ import annotations

import json
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.gui.scanner_tab import (
    ScannerTab,
    _default_new_scan,
    _ScanSubTab,
)
from tradinglab.scanner.model import (
    OP_GT,
    Condition,
    FieldRef,
    Group,
    ScanDefinition,
)
from tradinglab.scanner.runner import MatchRow, ScanResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scan(name: str = "S1") -> ScanDefinition:
    return ScanDefinition(
        name=name,
        root=Group(combinator="and", children=[
            Condition(left=FieldRef.builtin("close"), op=OP_GT,
                      params={"right": FieldRef.literal(100.0)},
                      interval="5m"),
        ]),
        primary_interval="5m",
        rank_by=FieldRef.builtin("volume"),
    )


def _make_result(
    scan_id: str,
    rows: list[tuple[str, bool, float | None, bool]],
    *,
    tick_id: int = 1,
) -> ScanResult:
    match_rows = [
        MatchRow(symbol=sym, matched=matched, values={},
                 rank_value=rank, is_new=is_new)
        for sym, matched, rank, is_new in rows
    ]
    new_rows = [r for r in match_rows if r.is_new and r.matched is True]
    return ScanResult(
        scan_id=scan_id, tick_id=tick_id,
        timestamp=datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc),
        interval="5m",
        rows=match_rows, new_rows=new_rows,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_empty_construction(root):
    tab = ScannerTab(root)
    assert tab.get_library() == {}
    assert tab.get_active_scan_definitions() == []


def test_seeded_library_opens_only_one_subtab_by_default(root):
    """At startup with N library scans, only 1 sub-tab opens."""
    s1 = _make_scan("Alpha")
    s2 = _make_scan("Bravo")
    tab = ScannerTab(root, library={s1.id: s1, s2.id: s2})
    assert len(tab.get_active_scan_definitions()) == 1
    assert sum(1 for sid in (s1.id, s2.id) if sid in tab._sub_tabs) == 1
    assert tab.get_library() == {s1.id: s1, s2.id: s2}


def test_initial_open_ids_overrides_default(root):
    """Tests can opt into multi-tab construction explicitly."""
    s1 = _make_scan("Alpha")
    s2 = _make_scan("Bravo")
    tab = ScannerTab(
        root, library={s1.id: s1, s2.id: s2},
        initial_open_ids=[s1.id, s2.id],
    )
    assert len(tab.get_active_scan_definitions()) == 2
    assert s1.id in tab._sub_tabs
    assert s2.id in tab._sub_tabs


def test_default_open_picks_most_recently_updated(root):
    s_old = _make_scan("Old")
    s_old.updated_at = "2020-01-01T00:00:00Z"
    s_new = _make_scan("New")
    s_new.updated_at = "2026-01-01T00:00:00Z"
    tab = ScannerTab(root, library={s_old.id: s_old, s_new.id: s_new})
    assert s_new.id in tab._sub_tabs
    assert s_old.id not in tab._sub_tabs


def test_open_scan_loads_from_library(root):
    s1 = _make_scan("Alpha")
    s2 = _make_scan("Bravo")
    tab = ScannerTab(
        root, library={s1.id: s1, s2.id: s2},
        initial_open_ids=[s1.id],
    )
    assert s2.id not in tab._sub_tabs
    assert tab.open_scan(s2.id) is True
    assert s2.id in tab._sub_tabs
    # Re-opening already-open returns False.
    assert tab.open_scan(s2.id) is False
    # Unknown id returns False.
    assert tab.open_scan("nope") is False


def test_close_scan_unloads_keeps_in_library(root):
    deleted = []
    s = _make_scan("Keep")
    tab = ScannerTab(
        root, library={s.id: s},
        on_scan_deleted=lambda sid: deleted.append(sid),
    )
    assert s.id in tab._sub_tabs
    assert tab.close_scan(s.id) is True
    assert s.id not in tab._sub_tabs
    # Still in the library, still on disk-callback-wise: delete NOT fired.
    assert s.id in tab.get_library()
    assert deleted == []
    # Re-closing is a noop returning False.
    assert tab.close_scan(s.id) is False
    # Re-open works.
    assert tab.open_scan(s.id) is True
    assert s.id in tab._sub_tabs


def test_library_tabs_preserve_open_order(root):
    s_b = _make_scan("Bravo")
    s_a = _make_scan("Alpha")
    s_c = _make_scan("Charlie")
    tab = ScannerTab(
        root, library={s_b.id: s_b, s_a.id: s_a, s_c.id: s_c},
        initial_open_ids=[s_b.id, s_a.id, s_c.id],
    )
    # Open order is preserved (Bravo first, the ID of the user's pick).
    tabs = tab._notebook.tabs()
    labels = [tab._notebook.tab(t, "text") for t in tabs]
    assert labels == ["Bravo", "Alpha", "Charlie"]


# ---------------------------------------------------------------------------
# add_scan / delete_scan + callbacks
# ---------------------------------------------------------------------------


def test_add_scan_fires_save_callback(root):
    saved = []
    tab = ScannerTab(root, on_scan_saved=lambda s: saved.append(s.id))
    s = _make_scan("New")
    tab.add_scan(s)
    assert s.id in tab._sub_tabs
    assert saved == [s.id]


def test_delete_scan_fires_delete_callback(root):
    deleted = []
    s = _make_scan("Doomed")
    tab = ScannerTab(root, library={s.id: s},
                     on_scan_deleted=lambda sid: deleted.append(sid))
    tab.delete_scan(s.id)
    assert tab.get_library() == {}
    assert deleted == [s.id]


def test_delete_unknown_id_is_noop(root):
    deleted = []
    tab = ScannerTab(root, on_scan_deleted=lambda sid: deleted.append(sid))
    tab.delete_scan("does-not-exist")
    assert deleted == []


# ---------------------------------------------------------------------------
# set_library
# ---------------------------------------------------------------------------


def test_set_library_replaces_all(root):
    s_old = _make_scan("Old")
    tab = ScannerTab(root, library={s_old.id: s_old})
    s_new = _make_scan("New")
    tab.set_library({s_new.id: s_new})
    assert s_old.id not in tab._sub_tabs
    assert s_new.id in tab._sub_tabs


# ---------------------------------------------------------------------------
# set_results / tree
# ---------------------------------------------------------------------------


def test_set_results_populates_tree_active_view(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    sub._view_var.set("active")
    result = _make_result(s.id, [
        ("AAPL", True, 1.5, False),
        ("MSFT", True, 2.7, True),
        ("GOOG", False, None, False),
    ])
    tab.set_results({s.id: result})
    iids = sub._tree.get_children("")
    symbols = {sub._tree.set(iid, "symbol") for iid in iids}
    assert symbols == {"AAPL", "MSFT"}


def test_view_toggle_new_only_shows_new_rows(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    sub._view_var.set("new")
    result = _make_result(s.id, [
        ("AAPL", True, 1.5, False),
        ("MSFT", True, 2.7, True),
        ("NVDA", True, 5.0, True),
    ])
    tab.set_results({s.id: result})
    iids = sub._tree.get_children("")
    symbols = {sub._tree.set(iid, "symbol") for iid in iids}
    assert symbols == {"MSFT", "NVDA"}


def test_show_insufficient_toggle(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    sub._view_var.set("active")
    result = _make_result(s.id, [
        ("AAPL", True, 1.5, False),
        ("MSFT", None, None, False),
    ])
    tab.set_results({s.id: result})
    assert {sub._tree.set(iid, "symbol")
            for iid in sub._tree.get_children("")} == {"AAPL"}
    sub._show_insuf_var.set(True)
    sub._refresh_tree()
    assert {sub._tree.set(iid, "symbol")
            for iid in sub._tree.get_children("")} == {"AAPL", "MSFT"}


def test_diff_update_preserves_selection_by_symbol(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    sub._view_var.set("active")
    tab.set_results({s.id: _make_result(s.id, [
        ("AAPL", True, 1.5, False),
        ("MSFT", True, 2.7, False),
    ])})
    sub._tree.selection_set("AAPL")
    # Next tick: AAPL still present, value changes; MSFT drops out.
    tab.set_results({s.id: _make_result(s.id, [
        ("AAPL", True, 9.9, False),
        ("NVDA", True, 3.3, True),
    ], tick_id=2)})
    assert sub._tree.selection() == ("AAPL",)
    assert sub._tree.set("AAPL", "rank") == "9.90"


def test_unknown_scan_id_in_results_is_ignored(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    # Should not raise.
    tab.set_results({"bogus-id": _make_result("bogus-id", [
        ("X", True, 1.0, True),
    ])})


# ---------------------------------------------------------------------------
# Sort
# ---------------------------------------------------------------------------


def test_sort_by_rank_descending_default(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    sub._view_var.set("active")
    tab.set_results({s.id: _make_result(s.id, [
        ("A", True, 1.0, False),
        ("B", True, 5.0, False),
        ("C", True, 3.0, False),
    ])})
    iids = list(sub._tree.get_children(""))
    order = [sub._tree.set(iid, "symbol") for iid in iids]
    assert order == ["B", "C", "A"]


def test_sort_click_toggles_direction(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    sub._view_var.set("active")
    tab.set_results({s.id: _make_result(s.id, [
        ("A", True, 1.0, False),
        ("B", True, 5.0, False),
    ])})
    sub._on_sort_click("rank")  # toggle to ascending
    iids = list(sub._tree.get_children(""))
    assert [sub._tree.set(iid, "symbol") for iid in iids] == ["A", "B"]


def test_sort_by_symbol_alpha(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    sub._view_var.set("active")
    tab.set_results({s.id: _make_result(s.id, [
        ("Z", True, 1.0, False),
        ("A", True, 1.0, False),
        ("M", True, 1.0, False),
    ])})
    sub._on_sort_click("symbol")  # ascending alpha
    iids = list(sub._tree.get_children(""))
    assert [sub._tree.set(iid, "symbol") for iid in iids] == ["A", "M", "Z"]


# ---------------------------------------------------------------------------
# Rank/interval/dir wiring
# ---------------------------------------------------------------------------


def test_interval_change_propagates_to_scan_and_editor(root):
    saved = []
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s},
                     on_scan_saved=lambda sc: saved.append(sc.primary_interval))
    sub = tab._sub_tabs[s.id]
    sub._interval_var.set("15m")
    sub._on_interval_change()
    assert s.primary_interval == "15m"
    assert sub._editor._default_interval == "15m"


def test_rank_dir_change_mutates_scan(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    sub._rank_dir_var.set("asc")
    sub._on_rank_dir_change()
    assert s.rank_dir == "asc"


# ---------------------------------------------------------------------------
# Conditions popup window
# ---------------------------------------------------------------------------


def test_conditions_window_starts_hidden(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    win = sub._cond_window
    assert win is not None
    # Toplevel.state() returns "withdrawn" until deiconify is called.
    assert win.state() == "withdrawn"


def test_open_conditions_window_shows_toplevel(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    sub._open_conditions_window()
    sub._cond_window.update_idletasks()
    assert sub._cond_window.state() in ("normal", "zoomed")


def test_hide_conditions_window_preserves_editor(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    editor_before = sub._editor
    sub._open_conditions_window()
    sub._hide_conditions_window()
    assert sub._cond_window.state() == "withdrawn"
    # Editor instance must survive a hide so subsequent edits keep state.
    assert sub._editor is editor_before


def test_conditions_summary_reflects_leaf_count(root):
    s = _make_scan("S")  # default: 1 leaf condition
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    assert sub._cond_summary_var.get() == "1 condition"
    # Add a sibling condition through the model and refresh.
    s.root.children.append(
        Condition(left=FieldRef.builtin("volume"), op=OP_GT,
                  params={"right": FieldRef.literal(0.0)}, interval="5m")
    )
    sub._refresh_cond_summary()
    assert sub._cond_summary_var.get() == "2 conditions"


def test_subtab_destroy_tears_down_popup(root):
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s})
    sub = tab._sub_tabs[s.id]
    win = sub._cond_window
    assert win is not None
    sub.destroy()
    # Toplevel should no longer be a live Tk widget.
    assert not bool(win.winfo_exists())


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------


def test_export_writes_round_trippable_json(root, tmp_path, monkeypatch):
    s = _make_scan("Exporter")
    tab = ScannerTab(root, library={s.id: s})
    out = tmp_path / "exported.json"
    monkeypatch.setattr(
        "tradinglab.gui.scanner_tab.filedialog.asksaveasfilename",
        lambda **kwargs: str(out),
    )
    # Select the scan tab so current_scan_id() returns it.
    tab._notebook.select(tab._sub_tabs[s.id])
    tab._on_export()
    assert out.exists()
    data = json.loads(out.read_text())
    s2 = ScanDefinition.from_dict(data)
    assert s2.name == "Exporter"
    assert s2.primary_interval == "5m"
    assert len(s2.root.children) == 1


def test_import_rename_on_name_collision(root, tmp_path, monkeypatch):
    # Pre-existing scan named "Dup".
    existing = _make_scan("Dup")
    tab = ScannerTab(root, library={existing.id: existing})
    # File with a different scan also named "Dup".
    incoming = _make_scan("Dup")
    src = tmp_path / "in.json"
    src.write_text(json.dumps(incoming.to_dict()))
    monkeypatch.setattr(
        "tradinglab.gui.scanner_tab.filedialog.askopenfilename",
        lambda **kwargs: str(src),
    )
    monkeypatch.setattr(
        "tradinglab.gui.scanner_tab.simpledialog.askstring",
        lambda *args, **kwargs: "Dup (renamed)",
    )
    tab._on_import()
    names = sorted(s.name for s in tab.get_library().values())
    assert names == ["Dup", "Dup (renamed)"]
    assert len(tab._sub_tabs) == 2


def test_import_invalid_json_shows_error(root, tmp_path, monkeypatch):
    src = tmp_path / "bad.json"
    src.write_text("{not valid json")
    tab = ScannerTab(root)
    monkeypatch.setattr(
        "tradinglab.gui.scanner_tab.filedialog.askopenfilename",
        lambda **kwargs: str(src),
    )
    captured = []
    monkeypatch.setattr(
        "tradinglab.gui.scanner_tab.messagebox.showerror",
        lambda *a, **k: captured.append((a, k)),
    )
    tab._on_import()
    assert len(captured) == 1
    assert tab.get_library() == {}


# ---------------------------------------------------------------------------
# Row actions
# ---------------------------------------------------------------------------


def test_row_action_callback_via_double_click_handler(root):
    fired = []
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s},
                     on_row_action=lambda sym, kind: fired.append((sym, kind)))
    sub = tab._sub_tabs[s.id]
    sub._view_var.set("active")
    tab.set_results({s.id: _make_result(s.id, [
        ("AAPL", True, 1.0, False),
    ])})
    sub._tree.selection_set("AAPL")
    # Build a fake event with a y that misses any row → falls back to selection.
    class FakeEvent:
        x = y = x_root = y_root = 0
    sub._on_double_click(FakeEvent())
    assert fired == [("AAPL", "primary")]


def test_fire_action_invokes_callback(root):
    fired = []
    s = _make_scan("S")
    tab = ScannerTab(root, library={s.id: s},
                     on_row_action=lambda sym, kind: fired.append((sym, kind)))
    sub = tab._sub_tabs[s.id]
    sub._fire_action("MSFT", "compare")
    assert fired == [("MSFT", "compare")]


# ---------------------------------------------------------------------------
# Default factory
# ---------------------------------------------------------------------------


def test_default_factory_creates_valid_blank_scan():
    s = _default_new_scan("Brand New")
    assert s.name == "Brand New"
    assert s.primary_interval == "5m"
    assert s.root.combinator == "and"
    assert s.root.children == []
