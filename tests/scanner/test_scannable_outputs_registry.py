"""Tests pinning the per-class ``scannable_outputs`` migration.

Verifies that:

* Every previously-hand-curated indicator still appears in
  :func:`all_fields` after the migration to per-class
  :attr:`Indicator.scannable_outputs` ClassVars.
* The :func:`indicators_resetting_daily` helper still returns the
  original three session-anchored kind_ids (``vwap`` / ``rvol`` /
  ``rrvol``) by walking the registry.
* A custom indicator that declares ``scannable_outputs`` on its class
  is automatically picked up by :func:`all_fields` — no edit to a
  hand-curated allowlist required.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from tradinglab.indicators.base import (
    _BY_KIND_ID,
    INDICATORS,
    register_indicator,
)
from tradinglab.scanner.fields import (
    all_fields,
    get_field,
    indicators_resetting_daily,
    scannable_indicators,
)

# The exact set the old hand-curated SCANNABLE_INDICATORS dict exposed,
# so the migration is asserted byte-for-byte at the kind_id level.
_LEGACY_SCANNABLE_KIND_IDS: frozenset[str] = frozenset({
    "sma", "ema", "rsi", "bbands", "atr", "adx",
    "vwap", "avwap", "smi", "lrsi", "rvol", "rrvol",
})


_LEGACY_OUTPUT_KEYS_BY_KIND: dict[str, frozenset[str]] = {
    "sma":   frozenset({"sma"}),
    "ema":   frozenset({"ema"}),
    "rsi":   frozenset({"rsi"}),
    "bbands": frozenset({"middle", "upper", "lower"}),
    "atr":   frozenset({"atr"}),
    "adx":   frozenset({"adx", "+di", "-di"}),
    "vwap":  frozenset({"vwap"}),
    "avwap": frozenset({"avwap"}),
    "smi":   frozenset({"smi", "signal"}),
    "lrsi":  frozenset({"lrsi"}),
    "rvol":  frozenset({"rvol"}),
    "rrvol": frozenset({"rvol"}),
}


_LEGACY_RESETS_DAILY: frozenset[str] = frozenset({"vwap", "rvol", "rrvol"})


def test_every_legacy_scannable_indicator_appears_in_all_fields() -> None:
    """All 12 previously-in-SCANNABLE_INDICATORS kind_ids surface as FieldSpecs."""
    indicator_ids = {f.id for f in all_fields() if f.kind == "indicator"}
    for kind_id in _LEGACY_SCANNABLE_KIND_IDS:
        assert kind_id in indicator_ids, (
            f"indicator {kind_id!r} dropped out of all_fields() after the "
            "SCANNABLE_INDICATORS migration"
        )


@pytest.mark.parametrize("kind_id", sorted(_LEGACY_SCANNABLE_KIND_IDS))
def test_legacy_indicator_output_keys_preserved(kind_id: str) -> None:
    """Each migrated indicator still exposes the same output keys it used to."""
    spec = get_field(kind_id, kind="indicator")
    assert spec is not None, f"FieldSpec missing for {kind_id!r}"
    assert frozenset(spec.output_keys) == _LEGACY_OUTPUT_KEYS_BY_KIND[kind_id], (
        f"{kind_id!r} output keys changed: have {spec.output_keys}, "
        f"expected {_LEGACY_OUTPUT_KEYS_BY_KIND[kind_id]}"
    )


def test_indicators_resetting_daily_returns_legacy_set() -> None:
    """The registry-walking helper returns the original session-anchored 3."""
    assert frozenset(indicators_resetting_daily()) == _LEGACY_RESETS_DAILY


def test_scannable_indicators_back_compat_dict_shape() -> None:
    """``scannable_indicators()`` returns the same shape as the old constant."""
    out = scannable_indicators()
    for kind_id in _LEGACY_SCANNABLE_KIND_IDS:
        assert kind_id in out, f"{kind_id!r} missing from registry projection"
        outputs = out[kind_id]
        assert outputs, f"{kind_id!r} has empty scannable_outputs"
        for entry in outputs:
            assert len(entry) == 2, f"{kind_id!r} entry not (key, dtype): {entry}"
            key, dtype = entry
            assert isinstance(key, str)
            assert dtype in ("numeric", "bool"), f"unknown dtype {dtype!r}"


def test_module_level_back_compat_constants_still_resolve() -> None:
    """Tests / downstream code that imported the old constants keep working."""
    from tradinglab.scanner import fields as fields_mod

    legacy_dict = fields_mod.SCANNABLE_INDICATORS  # noqa: SLF001 — public-by-name
    legacy_tuple = fields_mod.INDICATORS_RESETTING_DAILY  # noqa: SLF001
    assert isinstance(legacy_dict, dict)
    assert isinstance(legacy_tuple, tuple)
    assert frozenset(legacy_dict.keys()) >= _LEGACY_SCANNABLE_KIND_IDS
    assert frozenset(legacy_tuple) == _LEGACY_RESETS_DAILY


# ---------------------------------------------------------------------------
# Custom-indicator opt-in
# ---------------------------------------------------------------------------


class _PluginScannable:
    """Fake plugin indicator that opts INTO the scanner via the ClassVar."""

    kind_id: ClassVar[str] = "test_plugin_scannable"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[tuple] = ()
    default_style: ClassVar[dict] = {}
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("value", "numeric"),
    )

    name = "TestPluginScannable"
    overlay = False

    def compute(self, candles):  # pragma: no cover — exercised elsewhere
        return {"value": []}


class _PluginNotScannable:
    """Fake plugin indicator that did NOT opt in — must stay hidden."""

    kind_id: ClassVar[str] = "test_plugin_hidden"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[tuple] = ()
    default_style: ClassVar[dict] = {}

    name = "TestPluginHidden"
    overlay = False

    def compute(self, candles):  # pragma: no cover
        return {"value": []}


class _PluginDailyReset:
    """Fake plugin indicator that opts in AND declares session-anchored."""

    kind_id: ClassVar[str] = "test_plugin_daily"
    kind_version: ClassVar[int] = 1
    params_schema: ClassVar[tuple] = ()
    default_style: ClassVar[dict] = {}
    scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = (
        ("value", "numeric"),
    )
    resets_daily: ClassVar[bool] = True

    name = "TestPluginDaily"
    overlay = False

    def compute(self, candles):  # pragma: no cover
        return {"value": []}


@pytest.fixture
def _isolated_registry():
    """Snapshot + restore the indicator registry so plugin registrations
    don't leak into other tests in the same session.
    """
    saved_ind = dict(INDICATORS)
    saved_by_kind = dict(_BY_KIND_ID)
    try:
        yield
    finally:
        INDICATORS.clear()
        INDICATORS.update(saved_ind)
        _BY_KIND_ID.clear()
        _BY_KIND_ID.update(saved_by_kind)


def test_custom_scannable_indicator_appears_in_registry(_isolated_registry) -> None:
    """A custom indicator that declares scannable_outputs surfaces immediately."""
    register_indicator("TestPluginScannable", _PluginScannable)
    indicator_ids = {f.id for f in all_fields() if f.kind == "indicator"}
    assert "test_plugin_scannable" in indicator_ids
    spec = get_field("test_plugin_scannable", kind="indicator")
    assert spec is not None
    assert spec.output_keys == ("value",)
    assert spec.default_output_key == "value"
    assert spec.resets_daily is False


def test_custom_non_scannable_indicator_stays_hidden(_isolated_registry) -> None:
    """An indicator with no scannable_outputs MUST NOT leak into the scanner."""
    register_indicator("TestPluginHidden", _PluginNotScannable)
    indicator_ids = {f.id for f in all_fields() if f.kind == "indicator"}
    assert "test_plugin_hidden" not in indicator_ids


def test_custom_daily_reset_indicator_picks_up_resets_daily(_isolated_registry) -> None:
    """resets_daily=True ClassVar must flow into the FieldSpec + helper."""
    register_indicator("TestPluginDaily", _PluginDailyReset)
    assert "test_plugin_daily" in indicators_resetting_daily()
    spec = get_field("test_plugin_daily", kind="indicator")
    assert spec is not None
    assert spec.resets_daily is True
