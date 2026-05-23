"""Unit tests for strategy_tester.universe."""

from __future__ import annotations

import pytest

from tradinglab.strategy_tester.model import UniverseKind, UniverseSpec
from tradinglab.strategy_tester.universe import (
    PRESETS,
    PresetMissing,
    WatchlistMissing,
    list_presets,
    normalize_symbols,
    resolve,
    resolve_preset,
)


def test_normalize_symbols_basic() -> None:
    out = normalize_symbols(("aapl", "MSFT", "aapl", "", None, "  goog  "))
    assert out == ("AAPL", "MSFT", "GOOG")


def test_resolve_explicit_symbols() -> None:
    spec = UniverseSpec(kind=UniverseKind.SYMBOLS, symbols=("aapl", "msft"))
    r = resolve(spec)
    assert r.symbols == ("AAPL", "MSFT")
    assert r.provenance == "symbols:2"


def test_resolve_preset_megacaps() -> None:
    r = resolve_preset("megacaps")
    assert "AAPL" in r.symbols
    assert r.provenance == "preset:megacaps"


def test_resolve_preset_missing_raises() -> None:
    with pytest.raises(PresetMissing):
        resolve_preset("does-not-exist")


def test_list_presets_includes_megacaps() -> None:
    ids = {pid for pid, _ in list_presets()}
    assert "megacaps" in ids
    assert "sp500_seed" in ids


def test_presets_table_well_formed() -> None:
    for pid, (syms, label) in PRESETS.items():
        assert isinstance(syms, tuple) and syms, f"{pid} has empty symbol tuple"
        assert all(s == s.upper() for s in syms), f"{pid} has non-upper symbol"
        assert label, f"{pid} has empty label"


def test_resolve_watchlist_missing_raises(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    spec = UniverseSpec(kind=UniverseKind.WATCHLIST, watchlist_name="Nope")
    with pytest.raises(WatchlistMissing):
        resolve(spec)
