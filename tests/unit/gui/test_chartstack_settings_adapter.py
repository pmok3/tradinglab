"""Unit tests for :mod:`tradinglab.gui.chartstack.settings_adapter`."""

from __future__ import annotations

import pytest

from tradinglab import settings as _settings
from tradinglab.gui.chartstack import settings_adapter as adapter
from tradinglab.gui.chartstack.binding import BindingMode


@pytest.fixture(autouse=True)
def _isolate_settings():
    # Snapshot + restore the settings store so adapter tests don't leak.
    snap = _settings.load()
    yield
    _settings.save(snap)


def test_defaults_returned_when_unset() -> None:
    _settings.clear()
    assert adapter.get("chartstack.cards.count") == 3
    assert adapter.get("chartstack.cards.min") == 3
    assert adapter.get("chartstack.cards.max") == 6
    # Default mode changed to FIXED_PRESET (audit
    # ``chartstack-fixed-preset``): out of the box the cards show
    # SPY / QQQ / VXX, not whatever HYBRID picks from the user's
    # watchlist + positions.
    assert adapter.get("chartstack.binding.mode") == "FIXED_PRESET"
    assert adapter.is_enabled() is False


def test_default_fixed_preset_symbols() -> None:
    """Default preset is SPY / QQQ / VXX per the
    ``chartstack-fixed-preset`` audit. Order matters: slot 0 = top
    of the stack = SPY."""
    _settings.clear()
    assert adapter.get("chartstack.fixed_preset_symbols") == ["SPY", "QQQ", "VXX"]


def test_fixed_preset_symbols_helper_returns_list_padded_to_card_count() -> None:
    """Helper returns a stable-length list (matches card_count) so
    callers don't have to write the pad/truncate dance themselves."""
    _settings.clear()
    _settings.set("chartstack.fixed_preset_symbols", ["SPY", "QQQ"])
    _settings.set("chartstack.cards.count", 3)
    out = adapter.fixed_preset_symbols()
    assert out == ["SPY", "QQQ", ""]


def test_fixed_preset_symbols_helper_truncates_to_card_count() -> None:
    _settings.clear()
    _settings.set("chartstack.fixed_preset_symbols",
                  ["SPY", "QQQ", "VXX", "DIA", "IWM"])
    _settings.set("chartstack.cards.count", 3)
    out = adapter.fixed_preset_symbols()
    assert out == ["SPY", "QQQ", "VXX"]


def test_fixed_preset_symbols_helper_uppercases_and_strips() -> None:
    """The popup writes user input verbatim; the helper normalises
    on read so the binding resolver sees a clean list."""
    _settings.clear()
    _settings.set("chartstack.fixed_preset_symbols",
                  ["  spy  ", "qqq", " vxx"])
    out = adapter.fixed_preset_symbols()
    assert out[0] == "SPY"
    assert out[1] == "QQQ"
    assert out[2] == "VXX"


def test_fixed_preset_symbols_helper_tolerates_garbage() -> None:
    """Garbage values (None, ints, non-list) degrade to the default
    rather than crashing the panel."""
    _settings.clear()
    _settings.set("chartstack.fixed_preset_symbols", "not-a-list")
    out = adapter.fixed_preset_symbols()
    # Falls back to defaults, padded/truncated to card_count.
    assert out == ["SPY", "QQQ", "VXX"]


def test_settings_override_default() -> None:
    _settings.clear()
    _settings.set("chartstack.cards.count", 4)
    assert adapter.get("chartstack.cards.count") == 4
    assert adapter.card_count() == 4


def test_card_count_clamped_high() -> None:
    _settings.clear()
    _settings.set("chartstack.cards.count", 99)
    assert adapter.card_count() == 6


def test_card_count_clamped_low() -> None:
    _settings.clear()
    _settings.set("chartstack.cards.count", 1)
    assert adapter.card_count() == 3


def test_card_count_handles_garbage() -> None:
    _settings.clear()
    _settings.set("chartstack.cards.count", "not-an-int")
    assert adapter.card_count() == 3  # default after parse failure


def test_binding_mode_parses_string() -> None:
    _settings.clear()
    _settings.set("chartstack.binding.mode", "PINNED_WATCHLIST")
    assert adapter.binding_mode() is BindingMode.PINNED_WATCHLIST


def test_binding_mode_parses_fixed_preset() -> None:
    _settings.clear()
    _settings.set("chartstack.binding.mode", "FIXED_PRESET")
    assert adapter.binding_mode() is BindingMode.FIXED_PRESET


def test_binding_mode_passthrough_enum() -> None:
    _settings.clear()
    _settings.set("chartstack.binding.mode", BindingMode.SCANNER_TOP_N)
    assert adapter.binding_mode() is BindingMode.SCANNER_TOP_N


def test_binding_mode_falls_back_to_fixed_preset() -> None:
    """Bogus binding-mode strings degrade to the new default
    (FIXED_PRESET), not HYBRID."""
    _settings.clear()
    _settings.set("chartstack.binding.mode", "BOGUS")
    assert adapter.binding_mode() is BindingMode.FIXED_PRESET


def test_is_enabled_true_when_set() -> None:
    _settings.clear()
    _settings.set("chartstack.enabled", True)
    assert adapter.is_enabled() is True
