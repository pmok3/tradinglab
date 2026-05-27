"""Symbol entry tests for ``_FieldRefPicker`` (cross-ticker UI).

Pins:

- Symbol entry only appears in Indicator / Builtin branches (not Number).
- Default visual: empty value pinned to `(active)` placeholder text.
- Typing any ticker commits as ``ref.symbol`` (uppercased).
- Clearing the entry (empty) commits ``ref.symbol = ""`` and restores
  the placeholder.
- Symbol pin survives Builtin ↔ Indicator type-combo toggles.
- No history / LRU / suggestions — the entry is a plain free-form
  text input. Users specify ANY ticker on demand.
"""

from __future__ import annotations

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.gui.scanner_block_editor import (
    _ACTIVE_SYMBOL_SENTINEL,
    _SYMBOL_PLACEHOLDER,
    _FieldRefPicker,
)
from tradinglab.scanner.model import FieldRef

# --- Symbol entry presence / absence --------------------------------------


def test_symbol_entry_absent_for_literal(root):
    picker = _FieldRefPicker(root, ref=FieldRef.literal(1.0))
    assert picker._symbol_combo is None
    picker.destroy()


def test_symbol_entry_present_for_builtin(root):
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    assert picker._symbol_combo is not None
    # Default shows placeholder text (visually grey) — empty pin.
    assert picker._symbol_var.get() == _SYMBOL_PLACEHOLDER
    assert picker._symbol_is_placeholder is True
    assert picker.get().symbol == ""
    picker.destroy()


def test_symbol_entry_present_for_indicator(root):
    picker = _FieldRefPicker(
        root, ref=FieldRef.indicator("ema", params={"length": 20})
    )
    assert picker._symbol_combo is not None
    assert picker._symbol_var.get() == _SYMBOL_PLACEHOLDER
    assert picker._symbol_is_placeholder is True
    picker.destroy()


# --- Symbol commit round-trip --------------------------------------------


def test_typing_symbol_commits_to_ref(root):
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    picker._symbol_var.set("QQQ")
    picker._commit_symbol()
    assert picker.get().symbol == "QQQ"
    picker.destroy()


def test_typing_lowercase_is_uppercased(root):
    """Tickers are conventionally uppercase; commit normalises."""
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    picker._symbol_var.set("nvda")
    picker._commit_symbol()
    assert picker.get().symbol == "NVDA"
    picker.destroy()


def test_pinned_symbol_displays_real_value_not_placeholder(root):
    """A pre-set ref.symbol shows the actual ticker (not placeholder)."""
    picker = _FieldRefPicker(
        root, ref=FieldRef.builtin("close", symbol="SPY")
    )
    assert picker._symbol_var.get() == "SPY"
    assert picker._symbol_is_placeholder is False
    picker.destroy()


def test_clearing_entry_reverts_to_active(root):
    """Clearing the typed ticker drops the cross-symbol pin."""
    picker = _FieldRefPicker(
        root, ref=FieldRef.builtin("close", symbol="SPY")
    )
    # Simulate the user selecting all and deleting.
    picker._symbol_var.set("")
    picker._commit_symbol()
    assert picker.get().symbol == ""
    picker.destroy()


def test_placeholder_text_treated_as_empty_on_commit(root):
    """Setting the var to the literal placeholder text doesn't pin."""
    picker = _FieldRefPicker(
        root, ref=FieldRef.builtin("close", symbol="SPY")
    )
    picker._symbol_var.set(_ACTIVE_SYMBOL_SENTINEL)
    picker._commit_symbol()
    assert picker.get().symbol == ""
    picker.destroy()


def test_focus_in_clears_placeholder(root):
    """FocusIn on a placeholder-state entry clears it for typing."""
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    assert picker._symbol_is_placeholder is True
    picker._on_symbol_focus_in()
    assert picker._symbol_var.get() == ""
    assert picker._symbol_is_placeholder is False
    picker.destroy()


def test_focus_out_restores_placeholder_when_empty(root):
    """FocusOut on an empty entry restores the placeholder text."""
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    picker._on_symbol_focus_in()  # clear placeholder
    assert picker._symbol_var.get() == ""
    picker._on_symbol_focus_out()
    assert picker._symbol_var.get() == _SYMBOL_PLACEHOLDER
    assert picker._symbol_is_placeholder is True
    picker.destroy()


def test_focus_out_keeps_typed_value(root):
    """FocusOut on a typed ticker preserves it (no placeholder revert)."""
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    picker._on_symbol_focus_in()
    picker._symbol_var.set("AAPL")
    picker._on_symbol_focus_out()
    assert picker._symbol_var.get() == "AAPL"
    assert picker._symbol_is_placeholder is False
    assert picker.get().symbol == "AAPL"
    picker.destroy()


def test_symbol_preserved_across_type_toggle(root):
    """Toggling Builtin↔Indicator must keep the user's cross-symbol pin."""
    picker = _FieldRefPicker(
        root, ref=FieldRef.builtin("close", symbol="SPY")
    )
    # Programmatically flip the type to Indicator (mirrors a combo pick).
    picker._type_var.set("Indicator")
    picker._on_type_change()
    assert picker.get().symbol == "SPY"
    picker.destroy()


# --- No history / no suggestions ------------------------------------------


def test_no_dropdown_no_history_no_suggestions(root):
    """The entry has NO dropdown — it's a plain text input.

    The whole point of cross-symbol pinning is letting the user specify
    ANY ticker on demand. Pre-baked suggestions would mislead first-time
    users into thinking only certain tickers are valid; a history /
    LRU would clutter the dropdown with arbitrary past picks. Plain
    text input is the right primitive.
    """
    import tkinter.ttk as ttk
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    try:
        # The symbol widget must be a plain Entry, not a Combobox.
        assert isinstance(picker._symbol_combo, ttk.Entry)
        # And NOT a Combobox subclass (which would have a `values` opt).
        assert not isinstance(picker._symbol_combo, ttk.Combobox)
    finally:
        picker.destroy()


def test_symbol_pin_round_trips_through_ref(root):
    """End-to-end: type ticker → commit → ref carries symbol."""
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    picker._on_symbol_focus_in()
    picker._symbol_var.set("TSLA")
    picker._on_symbol_focus_out()
    ref = picker.get()
    assert ref.symbol == "TSLA"
    assert ref.kind == "builtin"
    assert ref.id == "close"
    picker.destroy()
