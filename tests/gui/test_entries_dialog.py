"""EntriesDialog widget tests.

Mirrors :mod:`tests.gui.test_exits_dialog` patterns. Each test gets a
fresh Toplevel via the ``root`` fixture in ``tests/conftest.py``.

Coverage (~17 cases):
- Construction with no strategy → fresh blank draft.
- Construction with existing strategy → deep-cloned into editor.
- Direction radio mutates draft.
- Universe radio swaps the per-mode entry widget.
- Symbols list parses comma-separated entries into uppercase tuple.
- Trigger kind dropdown swaps params (LIMIT shows price; STOP shows stop;
  STOP_LIMIT shows both; INDICATOR embeds BlockEditor; SCANNER_ALERT
  shows scanner_id).
- Sizing kind toggles which field is meaningful.
- on_fill_exit_ids: multi-select round-trip through checkboxes.
- Validate button surfaces errors via status label.
- Save calls on_save with the EntryStrategy AFTER passing validation.
- Save refused on validation errors (no on_save call, errors visible).
- Cancel does not call on_save; calls on_cancel.
- Lifecycle field roundtrips: cooldown_secs / arm_window_start.
- INDICATOR trigger embeds a BlockEditor whose root is the trigger
  condition.
- Universe XOR: switching radio resets the other modes.
- exit_strategy_ids_selected reflects checkbox state.
- Saved-draft preserves direction=SHORT after dialog close-style flow.
"""
from __future__ import annotations

import tkinter as tk
from typing import List

import pytest

from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    PositionAlreadyOpenPolicy,
    SizingKind,
    SizingRule,
    TriggerKind,
    Universe,
)
from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger
from tradinglab.exits.model import TriggerKind as ExitTriggerKind
from tradinglab.gui.entries_dialog import EntriesDialog


def _valid_strategy(**overrides) -> EntryStrategy:
    base = EntryStrategy(
        name="long-AAPL",
        direction=Direction.LONG,
        universe=Universe(symbols=("AAPL",)),
        trigger=EntryTrigger(kind=TriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=100.0),
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _make_dialog(root: tk.Toplevel, **kwargs) -> EntriesDialog:
    dlg = EntriesDialog(root, **kwargs)
    dlg.withdraw()
    return dlg


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction_new_strategy(root):
    dlg = _make_dialog(root)
    assert dlg.is_new is True
    assert isinstance(dlg.draft, EntryStrategy)
    assert dlg.draft.name == "(new entry)"
    dlg.destroy()


def test_construction_existing_strategy_clones_draft(root):
    s = _valid_strategy()
    dlg = _make_dialog(root, strategy=s)
    assert dlg.is_new is False
    # Clone — modifying draft must not bleed back.
    dlg.draft.name = "modified"
    assert s.name == "long-AAPL"
    dlg.destroy()


# ---------------------------------------------------------------------------
# Identity / Direction
# ---------------------------------------------------------------------------


def test_direction_radio_mutates_draft(root):
    dlg = _make_dialog(root, strategy=_valid_strategy())
    dlg._direction_var.set(Direction.SHORT.value)
    dlg._on_direction_changed()
    assert dlg.draft.direction == Direction.SHORT
    dlg.destroy()


def test_enabled_checkbox_mutates_draft(root):
    dlg = _make_dialog(root, strategy=_valid_strategy(enabled=True))
    dlg._enabled_var.set(False)
    dlg._on_enabled_changed()
    assert dlg.draft.enabled is False
    dlg.destroy()


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------


def test_universe_symbols_round_trip(root):
    dlg = _make_dialog(root, strategy=_valid_strategy())
    # Switch radio to symbols (default) and re-render.
    dlg._universe_radio_var.set("symbols")
    dlg._on_universe_radio_changed()
    v = dlg._universe_vars.get("symbols")
    assert v is not None
    v.set("aapl, msft, googl")
    dlg._on_universe_field_changed()
    assert dlg.draft.universe.symbols == ("AAPL", "MSFT", "GOOGL")
    dlg.destroy()


def test_universe_radio_xor_reset(root):
    s = _valid_strategy()
    s.universe = Universe(symbols=("AAPL",))
    dlg = _make_dialog(root, strategy=s)
    dlg._universe_radio_var.set("from_attached_chart")
    dlg._on_universe_radio_changed()
    assert dlg.draft.universe.from_attached_chart is True
    assert dlg.draft.universe.symbols == ()
    assert dlg.draft.universe.scanner_id is None
    dlg.destroy()


def test_universe_scanner_id_field(root):
    dlg = _make_dialog(root, strategy=_valid_strategy())
    dlg._universe_radio_var.set("scanner_id")
    dlg._on_universe_radio_changed()
    v = dlg._universe_vars["scanner_id"]
    v.set("scan-1")
    dlg._on_universe_field_changed()
    assert dlg.draft.universe.scanner_id == "scan-1"
    dlg.destroy()


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------


def test_trigger_kind_swap_to_limit(root):
    dlg = _make_dialog(root, strategy=_valid_strategy())
    dlg._trigger_kind_var.set("Limit")
    dlg._on_trigger_kind_changed()
    assert dlg.draft.trigger.kind == TriggerKind.LIMIT
    assert "price" in dlg._trigger_param_vars
    dlg._trigger_param_vars["price"].set("123.45")
    dlg._on_trigger_price_changed("price")
    assert dlg.draft.trigger.price == pytest.approx(123.45)
    dlg.destroy()


def test_trigger_kind_swap_to_stop(root):
    dlg = _make_dialog(root, strategy=_valid_strategy())
    dlg._trigger_kind_var.set("Stop")
    dlg._on_trigger_kind_changed()
    assert dlg.draft.trigger.kind == TriggerKind.STOP
    assert "stop_price" in dlg._trigger_param_vars
    dlg._trigger_param_vars["stop_price"].set("99.50")
    dlg._on_trigger_price_changed("stop_price")
    assert dlg.draft.trigger.stop_price == pytest.approx(99.50)
    dlg.destroy()


def test_trigger_kind_swap_to_stop_limit(root):
    dlg = _make_dialog(root, strategy=_valid_strategy())
    dlg._trigger_kind_var.set("Stop-Limit")
    dlg._on_trigger_kind_changed()
    assert dlg.draft.trigger.kind == TriggerKind.STOP_LIMIT
    assert "price" in dlg._trigger_param_vars
    assert "stop_price" in dlg._trigger_param_vars
    dlg.destroy()


def test_trigger_kind_indicator_embeds_block_editor(root):
    dlg = _make_dialog(root, strategy=_valid_strategy())
    dlg._trigger_kind_var.set("Indicator")
    dlg._on_trigger_kind_changed()
    assert dlg.draft.trigger.kind == TriggerKind.INDICATOR
    assert dlg.block_editor is not None
    # The condition should be the BlockEditor's root.
    root_group = dlg.block_editor.get_root()
    assert root_group is dlg.draft.trigger.condition or \
        root_group.to_dict() == dlg.draft.trigger.condition.to_dict()
    dlg.destroy()


def test_trigger_kind_scanner_alert(root):
    dlg = _make_dialog(root, strategy=_valid_strategy())
    dlg._trigger_kind_var.set("Scanner Alert")
    dlg._on_trigger_kind_changed()
    assert dlg.draft.trigger.kind == TriggerKind.SCANNER_ALERT
    v = dlg._trigger_param_vars["scanner_id"]
    v.set("my-scan")
    dlg._on_trigger_scanner_id_changed()
    assert dlg.draft.trigger.scanner_id == "my-scan"
    dlg.destroy()


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def test_sizing_kind_changes(root):
    dlg = _make_dialog(root, strategy=_valid_strategy())
    dlg._sizing_kind_var.set("Fixed Notional ($)")
    dlg._on_sizing_kind_changed()
    assert dlg.draft.sizing.kind == SizingKind.FIXED_NOTIONAL
    dlg._sizing_notional_var.set("5000")
    dlg._on_sizing_field_changed("notional")
    assert dlg.draft.sizing.notional == pytest.approx(5000.0)
    dlg.destroy()


# ---------------------------------------------------------------------------
# On-fill exits
# ---------------------------------------------------------------------------


def test_exit_strategy_ids_round_trip(root):
    e1 = ExitStrategy(name="stop-only", legs=[
        ExitLeg(triggers=[ExitTrigger(kind=ExitTriggerKind.STOP, price=95.0)])
    ])
    e2 = ExitStrategy(name="target-only", legs=[
        ExitLeg(triggers=[ExitTrigger(kind=ExitTriggerKind.LIMIT, price=110.0)])
    ])
    dlg = _make_dialog(root, strategy=_valid_strategy(),
                       exit_strategies=[e1, e2])
    # Initially nothing selected.
    assert dlg.exit_strategy_ids_selected == ()
    # Toggle the first one.
    dlg._exit_id_vars[e1.id].set(True)
    dlg._on_exit_ids_changed()
    assert dlg.exit_strategy_ids_selected == (e1.id,)
    assert dlg.draft.on_fill_exit_ids == (e1.id,)
    dlg.destroy()


# ---------------------------------------------------------------------------
# Validate / Save / Cancel
# ---------------------------------------------------------------------------


def test_validate_button_surfaces_errors(root):
    s = _valid_strategy()
    s.name = ""  # Trips name-empty validation
    dlg = _make_dialog(root, strategy=s)
    errors = dlg._on_validate()
    assert errors
    assert "Errors" in dlg._status_var.get()
    dlg.destroy()


def test_save_refused_on_validation_errors(root):
    captured: List[EntryStrategy] = []
    s = _valid_strategy()
    s.name = ""
    dlg = _make_dialog(root, strategy=s, on_save=captured.append)
    dlg._on_save_clicked(close=False)
    assert captured == []
    assert "Save refused" in dlg._status_var.get()
    dlg.destroy()


def test_save_calls_on_save_when_valid(root):
    captured: List[EntryStrategy] = []
    dlg = _make_dialog(root, strategy=_valid_strategy(),
                       on_save=captured.append)
    dlg._on_save_clicked(close=False)
    assert len(captured) == 1
    assert captured[0].name == "long-AAPL"
    dlg.destroy()


def test_cancel_does_not_call_on_save(root):
    captured: List[EntryStrategy] = []
    cancelled: List[bool] = []
    dlg = _make_dialog(
        root,
        strategy=_valid_strategy(),
        on_save=captured.append,
        on_cancel=lambda: cancelled.append(True),
    )
    dlg._on_cancel_clicked()
    assert captured == []
    assert cancelled == [True]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def test_lifecycle_cooldown_round_trip(root):
    dlg = _make_dialog(root, strategy=_valid_strategy())
    dlg._lifecycle_vars["cooldown_secs"].set("60")
    dlg._on_lifecycle_changed("cooldown_secs")
    assert dlg.draft.cooldown_secs == 60
    dlg.destroy()


def test_lifecycle_arm_window_round_trip(root):
    dlg = _make_dialog(root, strategy=_valid_strategy())
    dlg._lifecycle_vars["arm_window_start"].set("10:00")
    dlg._on_lifecycle_changed("arm_window_start")
    assert dlg.draft.arm_window_start == "10:00"
    dlg.destroy()


def test_position_already_open_policy_stack(root):
    dlg = _make_dialog(root, strategy=_valid_strategy())
    dlg._policy_var.set("Stack")
    dlg._on_policy_changed()
    assert dlg.draft.position_already_open_policy == \
        PositionAlreadyOpenPolicy.STACK
    dlg.destroy()
