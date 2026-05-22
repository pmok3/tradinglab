"""Tests for ``preload.manifest.build_from_loaded`` interval union.

Pins the skeptic-found fix: when ``previous`` is provided,
per-symbol interval sets are unioned with the prior run's so a
re-run with a smaller interval set does NOT silently drop bars that
are still on disk.

Also exercises the union of top-level ``intervals`` and the
"prior-only symbols carried forward" behaviour.
"""

from __future__ import annotations

import time

from tradinglab.preload.manifest import (
    SymbolEntry,
    UniverseManifest,
    build_from_loaded,
)


def _manifest(symbols: dict, intervals: tuple = ("5m", "1d")) -> UniverseManifest:
    """Helper: construct a prior-run manifest from a ``{sym: intervals}`` dict."""
    entries = tuple(
        SymbolEntry(symbol=sym, intervals=tuple(itvs), last_fetched=time.time())
        for sym, itvs in sorted(symbols.items())
    )
    return UniverseManifest(
        id="universe-A",
        name="Universe A",
        kind="basket",
        source="yfinance",
        intervals=intervals,
        symbols=entries,
        prepared_at=time.time(),
    )


# ---------------------------------------------------------------------------
# Baseline: no previous -> behave as before
# ---------------------------------------------------------------------------

def test_build_without_previous_returns_only_provided_symbols() -> None:
    man = build_from_loaded(
        uid="u1", name="U1", kind="basket", source="yfinance",
        intervals=("5m",),
        per_symbol={"AAPL": ("5m",), "MSFT": ("5m",)},
    )
    assert {e.symbol for e in man.symbols} == {"AAPL", "MSFT"}
    for e in man.symbols:
        assert e.intervals == ("5m",)
    assert man.intervals == ("5m",)


def test_build_without_previous_drops_empty_intervals() -> None:
    man = build_from_loaded(
        uid="u1", name="U1", kind="basket", source="yfinance",
        intervals=("5m",),
        per_symbol={"AAPL": ("5m",), "GHOST": ()},
    )
    assert [e.symbol for e in man.symbols] == ["AAPL"]


# ---------------------------------------------------------------------------
# Union behaviour: previous + current
# ---------------------------------------------------------------------------

def test_union_adds_new_intervals_to_existing_symbol() -> None:
    """First run loads (5m, 1d) for AAPL. Second run loads (15m,) for
    AAPL. The new manifest must claim ALL THREE intervals for AAPL —
    the 5m/1d pickles are still on disk and the manifest is the only
    thing telling strict-offline gating about them.
    """
    prev = _manifest({"AAPL": ("5m", "1d")})
    man = build_from_loaded(
        uid="u1", name="U1", kind="basket", source="yfinance",
        intervals=("15m",),
        per_symbol={"AAPL": ("15m",)},
        previous=prev,
    )
    [aapl] = [e for e in man.symbols if e.symbol == "AAPL"]
    assert aapl.intervals == ("15m", "1d", "5m") or set(aapl.intervals) == {
        "5m", "15m", "1d"
    }
    # Manifest-level intervals also unioned.
    assert set(man.intervals) == {"5m", "15m", "1d"}


def test_union_carries_prior_only_symbols_forward() -> None:
    """Symbols that succeeded last time but weren't fetched this run
    must remain in the manifest (their disk bars are still valid)."""
    prev = _manifest({"AAPL": ("5m", "1d"), "MSFT": ("5m",)})
    man = build_from_loaded(
        uid="u1", name="U1", kind="basket", source="yfinance",
        intervals=("5m",),
        per_symbol={"AAPL": ("5m",)},  # MSFT absent this run
        previous=prev,
    )
    names = {e.symbol for e in man.symbols}
    assert names == {"AAPL", "MSFT"}
    [msft] = [e for e in man.symbols if e.symbol == "MSFT"]
    assert msft.intervals == ("5m",)


def test_union_adds_newly_seen_symbols() -> None:
    """If the prior manifest didn't have GOOG and this run loaded it,
    GOOG must join the union."""
    prev = _manifest({"AAPL": ("5m",)})
    man = build_from_loaded(
        uid="u1", name="U1", kind="basket", source="yfinance",
        intervals=("5m",),
        per_symbol={"AAPL": ("5m",), "GOOG": ("5m",)},
        previous=prev,
    )
    assert {e.symbol for e in man.symbols} == {"AAPL", "GOOG"}


def test_union_with_empty_previous_is_no_op() -> None:
    """``previous`` with zero symbols and empty intervals tuple must
    behave exactly like the no-previous path."""
    prev = UniverseManifest(
        id="u1", name="U1", kind="basket", source="yfinance",
        intervals=(), symbols=(), prepared_at=time.time(),
    )
    man = build_from_loaded(
        uid="u1", name="U1", kind="basket", source="yfinance",
        intervals=("5m",),
        per_symbol={"AAPL": ("5m",)},
        previous=prev,
    )
    assert [e.symbol for e in man.symbols] == ["AAPL"]


def test_symbols_sorted_alphabetically() -> None:
    """SymbolEntry ordering is stable & alphabetical (matters for
    manifest diff visibility)."""
    prev = _manifest({"ZZZ": ("5m",)})
    man = build_from_loaded(
        uid="u1", name="U1", kind="basket", source="yfinance",
        intervals=("5m",),
        per_symbol={"AAA": ("5m",), "MMM": ("5m",)},
        previous=prev,
    )
    syms = [e.symbol for e in man.symbols]
    assert syms == ["AAA", "MMM", "ZZZ"]


def test_union_drops_symbol_if_both_sides_empty() -> None:
    """If a symbol is in ``previous`` with empty intervals (shouldn't
    happen in practice but guard anyway) AND not in this run, the
    union should drop it — strict-offline gating must not see a
    symbol with zero intervals."""
    prev = UniverseManifest(
        id="u1", name="U1", kind="basket", source="yfinance",
        intervals=(),
        symbols=(SymbolEntry(
            symbol="GHOST", intervals=(), last_fetched=time.time()),),
        prepared_at=time.time(),
    )
    man = build_from_loaded(
        uid="u1", name="U1", kind="basket", source="yfinance",
        intervals=("5m",),
        per_symbol={"AAPL": ("5m",)},
        previous=prev,
    )
    assert {e.symbol for e in man.symbols} == {"AAPL"}


def test_union_preserves_intervals_tuple_sortedness() -> None:
    """``SymbolEntry.intervals`` should be returned sorted so manifest
    diffs are stable across runs."""
    prev = _manifest({"AAPL": ("60m", "1d")})
    man = build_from_loaded(
        uid="u1", name="U1", kind="basket", source="yfinance",
        intervals=("15m",),
        per_symbol={"AAPL": ("15m", "5m")},
        previous=prev,
    )
    [aapl] = [e for e in man.symbols if e.symbol == "AAPL"]
    assert aapl.intervals == tuple(sorted(aapl.intervals))
