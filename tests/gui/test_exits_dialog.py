"""ExitsDialog widget tests.

Mirrors test_block_editor.py's session-Tk pattern (Windows ARM64
destroy/recreate is flaky). Each test gets a fresh Toplevel via the
``root`` fixture in ``tests/gui/conftest.py``.

Coverage (~14 cases):
- Singleton open / re-focus
- Empty editor disables add buttons
- + New seeds a draft, enables add buttons
- Bracket factory (pure helper) builds a 2-leg OCO
- Add leg / remove leg
- Add trigger / remove trigger
- Kind dropdown swaps per-kind param widgets (limit/stop/trailing/tod/indicator)
- Indicator trigger embeds BlockEditor with a default Group
- Add OCO group / toggle leg in group / cancel_on dropdown
- Disjoint validation flags duplicates
- Validate button surfaces errors / Save refused on errors
- Save → roundtrip via storage → library refreshes
- Library load + select loads strategy into editor
- on_library_changed callback fires on save
"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path

import pytest

from tradinglab.exits import storage as _exits_storage
from tradinglab.exits.model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
    OCOGroup,
    TrailBasis,
    TrailUnit,
    TriggerKind,
)
from tradinglab.gui.exits_dialog import (
    ExitsDialog,
    _BracketDialog,
    make_bracket_strategy,
    open_exits_dialog,
)
from tradinglab.gui.exits_dialog_widgets import (
    _LegFrame,
    _OCOGroupRow,
    _TriggerRow,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dialog(root: tk.Toplevel, **kwargs) -> ExitsDialog:
    dlg = ExitsDialog(root, **kwargs)
    dlg.withdraw()
    return dlg


def _save_strategy(name: str = "S1") -> ExitStrategy:
    """Persist a minimal valid strategy to the (sandboxed) storage."""
    s = ExitStrategy(
        name=name,
        legs=[ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])],
    )
    _exits_storage.save(s)
    return s


def _clear_storage() -> None:
    """Wipe the sandboxed exit_strategies dir between tests."""
    d = _exits_storage.exit_strategies_dir()
    for f in d.glob("*.json"):
        try:
            f.unlink()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Singleton entry point
# ---------------------------------------------------------------------------


def test_open_exits_dialog_singleton(root: tk.Toplevel) -> None:
    _clear_storage()
    a = open_exits_dialog(root)
    b = open_exits_dialog(root)
    assert a is b
    a.destroy()


def test_open_exits_dialog_recreates_after_close(root: tk.Toplevel) -> None:
    _clear_storage()
    a = open_exits_dialog(root)
    a.destroy()
    b = open_exits_dialog(root)
    assert b is not a
    assert b.winfo_exists()
    b.destroy()


# ---------------------------------------------------------------------------
# Empty editor / add buttons
# ---------------------------------------------------------------------------


def test_empty_editor_disables_add_buttons(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        assert dlg.get_draft() is None
        # add_leg button should be disabled (no draft loaded)
        assert "disabled" in dlg._add_leg_btn.state()
        assert "disabled" in dlg._add_oco_btn.state()
    finally:
        dlg.destroy()


def test_new_button_enables_editor(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        dlg._on_new()
        assert dlg.get_draft() is not None
        assert dlg.get_draft().name == "(new)"
        assert "disabled" not in dlg._add_leg_btn.state()
        assert "disabled" not in dlg._add_oco_btn.state()
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# Bracket factory (pure)
# ---------------------------------------------------------------------------


def test_make_bracket_strategy_percent() -> None:
    s = make_bracket_strategy(
        target_unit="percent", target_value=2.0,
        stop_unit="percent",   stop_value=1.0,
    )
    assert len(s.legs) == 2
    assert s.legs[0].label == "target"
    assert s.legs[0].triggers[0].kind == TriggerKind.LIMIT
    assert s.legs[0].triggers[0].offset_pct == 2.0
    assert s.legs[1].label == "stop"
    assert s.legs[1].triggers[0].kind == TriggerKind.STOP
    assert s.legs[1].triggers[0].offset_pct == 1.0
    assert len(s.oco_groups) == 1
    g = s.oco_groups[0]
    assert g.cancel_on == "full_closeout"
    assert set(g.leg_ids) == {s.legs[0].id, s.legs[1].id}


def test_make_bracket_strategy_dollar() -> None:
    s = make_bracket_strategy(
        target_unit="dollar", target_value=5.0,
        stop_unit="dollar",   stop_value=2.5,
    )
    assert s.legs[0].triggers[0].offset_dollar == 5.0
    assert s.legs[0].triggers[0].offset_pct is None
    assert s.legs[1].triggers[0].offset_dollar == 2.5


def test_make_bracket_strategy_invalid_unit() -> None:
    with pytest.raises(ValueError):
        make_bracket_strategy(
            target_unit="foo", target_value=1.0,
            stop_unit="percent", stop_value=1.0,
        )


# ---------------------------------------------------------------------------
# Leg + trigger CRUD
# ---------------------------------------------------------------------------


def test_add_leg_appends_and_rebuilds(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        dlg._on_new()
        n0 = len(dlg.get_draft().legs)
        dlg._on_add_leg()
        assert len(dlg.get_draft().legs) == n0 + 1
        new_leg_id = dlg.get_draft().legs[-1].id
        assert new_leg_id in dlg._leg_frames
    finally:
        dlg.destroy()


def test_remove_leg_drops_from_oco_groups(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        # Build a strategy with 2 legs in 1 OCO group
        leg_a = ExitLeg(label="A", triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        leg_b = ExitLeg(label="B", triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        s = ExitStrategy(
            name="x", legs=[leg_a, leg_b],
            oco_groups=[OCOGroup(leg_ids=(leg_a.id, leg_b.id))],
        )
        dlg.load_strategy_into_editor(s)
        dlg.remove_leg(leg_a.id)
        # OCO group with only 1 remaining leg should be dropped
        assert len(dlg.get_draft().oco_groups) == 0
        assert all(l.id != leg_a.id for l in dlg.get_draft().legs)
    finally:
        dlg.destroy()


def test_add_trigger_to_leg(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        leg = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        s = ExitStrategy(name="x", legs=[leg])
        dlg.load_strategy_into_editor(s)
        leg_frame = dlg._leg_frames[leg.id]
        leg_frame._on_add_trigger()
        assert len(dlg.get_draft().legs[0].triggers) == 2
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# Per-kind param rendering
# ---------------------------------------------------------------------------


def _trigger_row_for(dlg: ExitsDialog, leg_id: str, idx: int = 0) -> _TriggerRow:
    leg_frame = dlg._leg_frames[leg_id]
    rows = [w for w in leg_frame._triggers_holder.winfo_children()
            if isinstance(w, _TriggerRow)]
    return rows[idx]


def test_trigger_row_kind_limit_renders_price_offset_widgets(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        leg = ExitLeg(triggers=[ExitTrigger(
            kind=TriggerKind.LIMIT, price=200.0,
        )])
        s = ExitStrategy(name="x", legs=[leg])
        dlg.load_strategy_into_editor(s)
        row = _trigger_row_for(dlg, leg.id)
        assert "price" in row._param_vars
        assert "offset_pct" in row._param_vars
        assert "offset_dollar" in row._param_vars
        # Round-trip: changing the price var commits to the trigger
        row._param_vars["price"].set("250.5")
        assert row.trigger.price == 250.5
    finally:
        dlg.destroy()


def test_trigger_row_kind_trailing_renders_trail_widgets(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        leg = ExitLeg(triggers=[ExitTrigger(
            kind=TriggerKind.TRAILING_STOP,
            trail_unit=TrailUnit.PERCENT, trail_value=2.0,
        )])
        s = ExitStrategy(name="x", legs=[leg])
        dlg.load_strategy_into_editor(s)
        row = _trigger_row_for(dlg, leg.id)
        assert "trail_unit" in row._param_vars
        assert "trail_value" in row._param_vars
        assert "trail_basis" in row._param_vars
    finally:
        dlg.destroy()


def test_trigger_row_indicator_embeds_block_editor(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        leg = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.INDICATOR)])
        s = ExitStrategy(name="x", legs=[leg])
        dlg.load_strategy_into_editor(s)
        row = _trigger_row_for(dlg, leg.id)
        assert row.block_editor is not None
        # A default Group was synthesised
        assert row.trigger.condition is not None
        assert row.trigger.condition.combinator == "and"
    finally:
        dlg.destroy()


def test_trigger_kind_change_swaps_widgets(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        leg = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        s = ExitStrategy(name="x", legs=[leg])
        dlg.load_strategy_into_editor(s)
        row = _trigger_row_for(dlg, leg.id)
        # Switch to LIMIT
        row._kind_var.set("Limit")
        row._on_kind_changed()
        assert row.trigger.kind == TriggerKind.LIMIT
        assert "price" in row._param_vars
        # Switch to TIME_OF_DAY
        row._kind_var.set("Time of Day")
        row._on_kind_changed()
        assert row.trigger.kind == TriggerKind.TIME_OF_DAY
        assert "time_of_day" in row._param_vars
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# OCO groups
# ---------------------------------------------------------------------------


def test_add_oco_group_with_two_legs(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        a = ExitLeg(label="A", triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        b = ExitLeg(label="B", triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        s = ExitStrategy(name="x", legs=[a, b])
        dlg.load_strategy_into_editor(s)
        dlg._on_add_oco()
        assert len(dlg.get_draft().oco_groups) == 1
        g = dlg.get_draft().oco_groups[0]
        assert set(g.leg_ids) == {a.id, b.id}
        assert g.cancel_on == "full_closeout"
    finally:
        dlg.destroy()


def test_add_oco_group_needs_two_legs(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        leg = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        s = ExitStrategy(name="x", legs=[leg])
        dlg.load_strategy_into_editor(s)
        dlg._on_add_oco()
        assert len(dlg.get_draft().oco_groups) == 0
        assert "≥ 2 legs" in dlg._status_var.get() or "Need" in dlg._status_var.get()
    finally:
        dlg.destroy()


def test_oco_disjoint_validation_flags_duplicates(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        a = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        b = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        c = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        # B appears in both groups → duplicate
        s = ExitStrategy(
            name="x", legs=[a, b, c],
            oco_groups=[
                OCOGroup(leg_ids=(a.id, b.id)),
                OCOGroup(leg_ids=(b.id, c.id)),
            ],
        )
        dlg.load_strategy_into_editor(s)
        assert b.id in dlg.oco_duplicate_leg_ids
        assert a.id not in dlg.oco_duplicate_leg_ids
        assert c.id not in dlg.oco_duplicate_leg_ids
    finally:
        dlg.destroy()


def test_oco_toggle_leg_in_group(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        a = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        b = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        c = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        s = ExitStrategy(
            name="x", legs=[a, b, c],
            oco_groups=[OCOGroup(leg_ids=(a.id, b.id))],
        )
        dlg.load_strategy_into_editor(s)
        # Add c
        dlg.toggle_leg_in_group(0, c.id)
        assert c.id in dlg.get_draft().oco_groups[0].leg_ids
        # Remove a
        dlg.toggle_leg_in_group(0, a.id)
        assert a.id not in dlg.get_draft().oco_groups[0].leg_ids
    finally:
        dlg.destroy()


def test_oco_set_cancel_on(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        a = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        b = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])
        s = ExitStrategy(
            name="x", legs=[a, b],
            oco_groups=[OCOGroup(leg_ids=(a.id, b.id), cancel_on="full_closeout")],
        )
        dlg.load_strategy_into_editor(s)
        dlg.set_oco_cancel_on(0, "any_fire")
        assert dlg.get_draft().oco_groups[0].cancel_on == "any_fire"
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# Validate / save
# ---------------------------------------------------------------------------


def test_validate_surfaces_errors_for_empty_legs(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        # Empty name is a hard validation failure.
        dlg.load_strategy_into_editor(ExitStrategy(name="", legs=[]))
        errors = dlg._on_validate()
        assert errors  # at least one error
        assert any("name" in e.lower() for e in errors)
    finally:
        dlg.destroy()


def test_save_refused_on_invalid(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        dlg.load_strategy_into_editor(ExitStrategy(name="", legs=[]))
        dlg._on_save()
        assert "refused" in dlg._status_var.get().lower()
        # Nothing on disk
        loaded, _ = _exits_storage.load_all()
        assert loaded == []
    finally:
        dlg.destroy()


def test_save_persists_and_refreshes_library(root: tk.Toplevel) -> None:
    _clear_storage()
    fired = []
    dlg = _make_dialog(root, on_library_changed=lambda: fired.append(1))
    try:
        s = ExitStrategy(
            name="persist-me",
            legs=[ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])],
        )
        dlg.load_strategy_into_editor(s)
        dlg._on_save()
        loaded, _ = _exits_storage.load_all()
        assert any(x.name == "persist-me" for x in loaded)
        assert dlg._status_var.get().startswith("Saved")
        # callback fired once
        assert fired == [1]
        # library list contains it
        assert any(x.name == "persist-me" for x in dlg.library)
    finally:
        dlg.destroy()


def test_load_strategy_into_editor_clones(root: tk.Toplevel) -> None:
    _clear_storage()
    dlg = _make_dialog(root)
    try:
        s = ExitStrategy(
            name="orig",
            legs=[ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])],
        )
        dlg.load_strategy_into_editor(s)
        draft = dlg.get_draft()
        assert draft is not None
        assert draft is not s  # clone
        assert draft.id == s.id
        # mutating draft does not change original
        draft.name = "edited"
        assert s.name == "orig"
    finally:
        dlg.destroy()


def test_library_select_loads_strategy(root: tk.Toplevel) -> None:
    _clear_storage()
    s = _save_strategy("from-disk")
    dlg = _make_dialog(root)
    try:
        # Select listbox row 0
        idx = next((i for i, x in enumerate(dlg.library) if x.name == "from-disk"), None)
        assert idx is not None
        dlg._library_lb.selection_clear(0, "end")
        dlg._library_lb.selection_set(idx)
        dlg._on_library_select(None)
        assert dlg.get_draft() is not None
        assert dlg.get_draft().name == "from-disk"
    finally:
        dlg.destroy()


# ---------------------------------------------------------------------------
# Mine | Templates | All filter (audit ``template-filter``)
# ---------------------------------------------------------------------------


def _save_template(name: str, tmpl_id: str) -> ExitStrategy:
    """Persist a bundled-seed-style exit strategy (``tmpl-`` id marker)."""
    s = ExitStrategy(
        name=name,
        legs=[ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)])],
    )
    s.id = tmpl_id
    _exits_storage.save(s)
    return s


def test_exits_filter_defaults_to_mine(root: tk.Toplevel) -> None:
    _clear_storage()
    user = _save_strategy("My Exit")
    _save_template("Starter Trail", "tmpl-exit-trail")
    dlg = _make_dialog(root)
    try:
        assert dlg._filter_var.get() == "mine"
        assert [s.id for s in dlg._visible_library] == [user.id]
        labels = list(dlg._library_lb.get(0, "end"))
        assert "My Exit" in labels
        assert "Starter Trail" not in labels
    finally:
        dlg.destroy()


def test_exits_filter_templates_and_all(root: tk.Toplevel) -> None:
    _clear_storage()
    user = _save_strategy("My Exit")
    t1 = _save_template("Starter Trail", "tmpl-exit-trail")
    t2 = _save_template("Starter Stop", "tmpl-exit-stop")
    dlg = _make_dialog(root)
    try:
        dlg._filter_var.set("templates")
        dlg._populate_library_listbox()
        assert {s.id for s in dlg._visible_library} == {t1.id, t2.id}
        dlg._filter_var.set("all")
        dlg._populate_library_listbox()
        assert {s.id for s in dlg._visible_library} == {user.id, t1.id, t2.id}
    finally:
        dlg.destroy()


def test_exits_filter_selection_maps_to_filtered_view(root: tk.Toplevel) -> None:
    """Under "templates", listbox row 0 must load the FIRST TEMPLATE, not
    ``self._library[0]`` (which sorts to a user strategy here). Pins the
    index-map fix that keeps selection correct under any filter."""
    _clear_storage()
    _save_strategy("AAA My Exit")  # sorts first by name in the full library
    t1 = _save_template("ZZZ Starter", "tmpl-exit-z")
    dlg = _make_dialog(root)
    try:
        dlg._filter_var.set("templates")
        dlg._populate_library_listbox()
        dlg._library_lb.selection_clear(0, "end")
        dlg._library_lb.selection_set(0)
        dlg._on_library_select(None)
        assert dlg.get_draft() is not None
        assert dlg.get_draft().id == t1.id
    finally:
        dlg.destroy()


def test_exits_is_template_static() -> None:
    seed = ExitStrategy(name="s")
    seed.id = "tmpl-exit-x"
    user = ExitStrategy(name="u")
    assert ExitsDialog._is_template(seed) is True
    assert ExitsDialog._is_template(user) is False
