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
    assert adapter.get("chartstack.binding.mode") == "HYBRID"
    assert adapter.is_enabled() is False


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


def test_binding_mode_passthrough_enum() -> None:
    _settings.clear()
    _settings.set("chartstack.binding.mode", BindingMode.SCANNER_TOP_N)
    assert adapter.binding_mode() is BindingMode.SCANNER_TOP_N


def test_binding_mode_falls_back_to_hybrid() -> None:
    _settings.clear()
    _settings.set("chartstack.binding.mode", "BOGUS")
    assert adapter.binding_mode() is BindingMode.HYBRID


def test_is_enabled_true_when_set() -> None:
    _settings.clear()
    _settings.set("chartstack.enabled", True)
    assert adapter.is_enabled() is True
