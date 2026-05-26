"""FieldRef cross-symbol model tests (Phase 1 of cross-ticker support).

Pins :attr:`FieldRef.symbol` defaults / round-trip / factory behavior.
The symbol slot is the **only** new persisted state for Phase 1+2 —
every existing saved scan JSON (which lacks the key) MUST continue to
deserialize to an active-symbol ref so back-compat is preserved.
"""

from __future__ import annotations

from tradinglab.scanner.model import (
    FIELD_KIND_BUILTIN,
    FIELD_KIND_INDICATOR,
    FieldRef,
)


def test_fieldref_symbol_defaults_to_empty():
    """Every FieldRef built without ``symbol=`` defaults to ``""``."""
    assert FieldRef.builtin("close").symbol == ""
    assert FieldRef.indicator("ema", params={"length": 20}).symbol == ""
    assert FieldRef.literal(1.5).symbol == ""


def test_fieldref_is_cross_symbol_helper():
    assert FieldRef.builtin("close").is_cross_symbol() is False
    assert FieldRef.builtin("close", symbol="SPY").is_cross_symbol() is True
    assert FieldRef.indicator("ema", symbol="QQQ").is_cross_symbol() is True


def test_to_dict_omits_symbol_when_empty():
    """Legacy refs round-trip byte-identically (no spurious ``symbol`` key)."""
    d = FieldRef.builtin("close").to_dict()
    assert "symbol" not in d
    d = FieldRef.indicator("ema", params={"length": 20}).to_dict()
    assert "symbol" not in d


def test_to_dict_includes_symbol_when_set():
    d = FieldRef.builtin("close", symbol="SPY").to_dict()
    assert d["symbol"] == "SPY"
    d = FieldRef.indicator("ema", params={"length": 20}, symbol="QQQ").to_dict()
    assert d["symbol"] == "QQQ"


def test_from_dict_missing_symbol_back_compat():
    """Old saved JSON without ``symbol`` deserializes to ``symbol=""``."""
    ref = FieldRef.from_dict({"kind": "builtin", "id": "close"})
    assert ref.symbol == ""
    ref = FieldRef.from_dict({"kind": "indicator", "id": "ema",
                              "params": {"length": 9}})
    assert ref.symbol == ""


def test_from_dict_with_symbol_round_trips():
    src = FieldRef.indicator("ema", params={"length": 20}, symbol="SPY")
    ref = FieldRef.from_dict(src.to_dict())
    assert ref == src
    assert ref.symbol == "SPY"


def test_literal_round_trip_with_symbol():
    src = FieldRef(kind="literal", value=2.5, symbol="QQQ")
    d = src.to_dict()
    assert d.get("symbol") == "QQQ"
    ref = FieldRef.from_dict(d)
    assert ref == src


def test_indicator_factory_accepts_symbol():
    ref = FieldRef.indicator("rsi", params={"length": 14}, symbol="SPY")
    assert ref.kind == FIELD_KIND_INDICATOR
    assert ref.symbol == "SPY"
    assert ref.params == {"length": 14}


def test_builtin_factory_accepts_symbol():
    ref = FieldRef.builtin("close", symbol="SPY")
    assert ref.kind == FIELD_KIND_BUILTIN
    assert ref.symbol == "SPY"
