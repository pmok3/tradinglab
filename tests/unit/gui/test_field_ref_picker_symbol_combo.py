"""Symbol combo tests for ``_FieldRefPicker`` (Phase 2 cross-ticker UI).

Pins:

- Symbol combo only appears in Indicator / Builtin branches (not Number).
- Default value is the ``(active)`` sentinel; commit yields ``ref.symbol=""``.
- A typed/picked ticker commits as ``ref.symbol``.
- Recent-cross-symbols persistence: a picked ticker survives re-mount
  via the module-level LRU (settings-backed in production).
"""

from __future__ import annotations

import tradinglab.indicators  # noqa: F401  -- registers indicators
from tradinglab.gui.scanner_block_editor import (
    _ACTIVE_SYMBOL_SENTINEL,
    _FieldRefPicker,
    _load_recent_cross_symbols,
    _remember_cross_symbol,
    _reset_recent_cross_symbols_for_tests,
)
from tradinglab.scanner.model import FieldRef

# --- Symbol combo presence / absence --------------------------------------


def test_symbol_combo_absent_for_literal(root):
    picker = _FieldRefPicker(root, ref=FieldRef.literal(1.0))
    assert picker._symbol_combo is None
    picker.destroy()


def test_symbol_combo_present_for_builtin(root):
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    assert picker._symbol_combo is not None
    # Default = active-symbol sentinel.
    assert picker._symbol_var.get() == _ACTIVE_SYMBOL_SENTINEL
    picker.destroy()


def test_symbol_combo_present_for_indicator(root):
    picker = _FieldRefPicker(
        root, ref=FieldRef.indicator("ema", params={"length": 20})
    )
    assert picker._symbol_combo is not None
    assert picker._symbol_var.get() == _ACTIVE_SYMBOL_SENTINEL
    picker.destroy()


# --- Symbol commit round-trip --------------------------------------------


def test_typing_symbol_commits_to_ref(root):
    _reset_recent_cross_symbols_for_tests()
    picker = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    picker._symbol_var.set("QQQ")
    picker._commit_symbol()
    assert picker.get().symbol == "QQQ"
    picker.destroy()


def test_reset_to_active_clears_symbol(root):
    _reset_recent_cross_symbols_for_tests()
    picker = _FieldRefPicker(
        root, ref=FieldRef.builtin("close", symbol="SPY")
    )
    # The picker should seed the combo with the pinned symbol.
    assert picker._symbol_var.get() == "SPY"
    picker._symbol_var.set(_ACTIVE_SYMBOL_SENTINEL)
    picker._commit_symbol()
    assert picker.get().symbol == ""
    picker.destroy()


def test_recent_picks_persisted_via_lru(root, monkeypatch):
    """A picked ticker shows up in a freshly-mounted picker's combo values."""
    _reset_recent_cross_symbols_for_tests()
    # Patch settings.set so we don't write to the user's real config.
    stored: dict = {}

    def _fake_set(key, value):
        stored[key] = value

    def _fake_get(key, default=None):
        return stored.get(key, default)

    import tradinglab.settings as _settings
    monkeypatch.setattr(_settings, "set", _fake_set)
    monkeypatch.setattr(_settings, "get", _fake_get)

    p1 = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    p1._symbol_var.set("NVDA")
    p1._commit_symbol()
    p1.destroy()

    # The LRU should now have NVDA at the front. A re-mounted picker
    # should include it in the combo values list.
    _reset_recent_cross_symbols_for_tests()  # force re-read from settings stub
    assert "NVDA" in _load_recent_cross_symbols()
    p2 = _FieldRefPicker(root, ref=FieldRef.builtin("close"))
    assert "NVDA" in tuple(p2._symbol_combo.cget("values"))
    p2.destroy()


def test_remember_cross_symbol_uppercases_and_dedupes(monkeypatch):
    _reset_recent_cross_symbols_for_tests()
    stored: dict = {}
    import tradinglab.settings as _settings
    monkeypatch.setattr(_settings, "set", lambda k, v: stored.__setitem__(k, v))
    monkeypatch.setattr(_settings, "get",
                        lambda k, default=None: stored.get(k, default))
    _remember_cross_symbol("spy")
    _remember_cross_symbol("QQQ")
    _remember_cross_symbol("SPY")  # dup, should move to front
    recent = _load_recent_cross_symbols()
    assert recent[0] == "SPY"
    assert recent.count("SPY") == 1
    assert "QQQ" in recent


def test_symbol_preserved_across_type_toggle(root):
    """Toggling Builtin↔Indicator must keep the user's cross-symbol pin."""
    _reset_recent_cross_symbols_for_tests()
    picker = _FieldRefPicker(
        root, ref=FieldRef.builtin("close", symbol="SPY")
    )
    # Programmatically flip the type to Indicator (mirrors a combo pick).
    picker._type_var.set("Indicator")
    picker._on_type_change()
    assert picker.get().symbol == "SPY"
    picker.destroy()
