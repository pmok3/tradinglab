"""Unit tests for source-aware index-symbol aliases.

Index symbols are spelled differently by every vendor (``^VIX`` on yfinance,
``$VIX`` on Schwab, ``I:VIX`` on Polygon). These tests pin the resolver's
three load-bearing properties — idempotence, cross-vendor canonicalisation,
and the curated allowlist that stops a real equity being mistaken for an
index — plus the registry integration.

See `data/index_aliases.spec.md` and AGENTS.md §7.37.
"""
from __future__ import annotations

import datetime as dt

import pytest

from tradinglab.data.index_aliases import (
    INDEX_ALIASES,
    NEVER_ALIAS,
    canonical_index_name,
    resolve_symbol,
)
from tradinglab.models import Candle


def _series(n=2, base=20.0):
    t = dt.datetime(2026, 6, 15, 9, 30)
    out = []
    for i in range(n):
        p = base + i
        out.append(Candle(date=t, open=p, high=p + 1, low=p - 1, close=p,
                          volume=100, session="regular"))
        t += dt.timedelta(minutes=5)
    return out


# ------------------------------------------------------------ forward resolve
@pytest.mark.parametrize("shorthand,source,expected", [
    ("VIX", "yfinance", "^VIX"),
    ("VIX", "polygon", "I:VIX"),
    ("VIX", "schwab", "$VIX"),
    ("NDX", "yfinance", "^NDX"),
    ("DJI", "yfinance", "^DJI"),
    ("RUT", "yfinance", "^RUT"),
    ("TNX", "yfinance", "^TNX"),
])
def test_resolves_shorthand_to_vendor_form(shorthand, source, expected):
    assert resolve_symbol(shorthand, source) == expected


def test_sp500_is_not_a_prefix_rule():
    """Yahoo says ^GSPC while Schwab/Polygon say SPX. A "prefix the canonical
    name" shortcut would emit ^SPX — which is why the table is explicit."""
    assert resolve_symbol("SPX", "yfinance") == "^GSPC"
    assert resolve_symbol("SPX", "schwab") == "$SPX"
    assert resolve_symbol("SPX", "polygon") == "I:SPX"


def test_nasdaq_composite_keyed_as_ixic_not_comp():
    """COMP is Compass Inc. The Nasdaq Composite is keyed IXIC precisely so
    the shorthand a trader might guess can never resolve to an index."""
    assert resolve_symbol("IXIC", "yfinance") == "^IXIC"
    assert resolve_symbol("COMP", "yfinance") == "COMP"


# ------------------------------------------------- the allowlist is the point
@pytest.mark.parametrize("symbol", sorted(NEVER_ALIAS))
def test_never_alias_symbols_pass_through_on_every_source(symbol):
    """These are REAL listed equities, verified against the live quote API.
    Aliasing them would silently chart a different instrument — the same class
    of money-losing misread as mislabelling a scaled chart."""
    assert canonical_index_name(symbol) is None
    for source in ("yfinance", "schwab", "polygon", "alpaca"):
        assert resolve_symbol(symbol, source) == symbol


@pytest.mark.parametrize("symbol", ["AAPL", "SPY", "QQQ", "BRK-B", "BTC-USD", "XYZZY"])
def test_unknown_symbols_pass_through_untouched(symbol):
    assert resolve_symbol(symbol, "yfinance") == symbol


def test_never_alias_and_alias_table_are_disjoint():
    """A future contributor must not be able to add a canonical key that is
    also on the never-alias list."""
    assert NEVER_ALIAS.isdisjoint(INDEX_ALIASES.keys())


# ----------------------------------------------------- idempotence + reversal
@pytest.mark.parametrize("source", ["yfinance", "schwab", "polygon"])
def test_resolution_is_idempotent(source):
    """Resolving an already-resolved symbol must be a no-op, or the ticker box
    would churn on every reload."""
    once = resolve_symbol("VIX", source)
    assert resolve_symbol(once, source) == once


@pytest.mark.parametrize("other_form", ["^VIX", "$VIX", "I:VIX", "VIX"])
def test_any_vendor_form_canonicalises(other_form):
    """This is what makes re-resolution across a source switch work: the
    resolver accepts ANY vendor's spelling, not just the bare shorthand."""
    assert canonical_index_name(other_form) == "VIX"
    assert resolve_symbol(other_form, "polygon") == "I:VIX"
    assert resolve_symbol(other_form, "yfinance") == "^VIX"


def test_composite_sources_borrow_the_yfinance_column():
    """Auto / yfinance+alpaca resolve history through a yfinance leg."""
    assert resolve_symbol("VIX", "Auto") == "^VIX"
    assert resolve_symbol("VIX", "yfinance+alpaca") == "^VIX"


def test_source_without_an_alias_column_passes_through():
    """Alpaca has no index feed; failing honestly beats inventing a symbol."""
    assert resolve_symbol("VIX", "alpaca") == "VIX"
    assert resolve_symbol("VIX", "local") == "VIX"


# ------------------------------------------------------------------- ratios
def test_ratio_legs_resolve_independently():
    assert resolve_symbol("VIX/SPY", "yfinance") == "^VIX/SPY"
    assert resolve_symbol("SPY/VIX", "yfinance") == "SPY/^VIX"
    assert resolve_symbol("VIX/SPX", "yfinance") == "^VIX/^GSPC"


def test_scale_constant_is_never_aliased():
    assert resolve_symbol("VIX/15.87", "yfinance") == "^VIX/15.87"
    assert resolve_symbol("VIX/16", "polygon") == "I:VIX/16"


def test_empty_and_case_handling():
    assert resolve_symbol("", "yfinance") == ""
    assert resolve_symbol("vix", "yfinance") == "^VIX"
    assert resolve_symbol("  vix  ", "yfinance") == "^VIX"


# -------------------------------------------------------- registry integration
def test_registered_source_applies_aliases_and_scaling_end_to_end():
    """The wrapper installed at ``register_source`` is the single chokepoint,
    so a bare shorthand works on every fetch surface — including watchlist and
    scanner rows that never pass through the ticker box."""
    from tradinglab.data.base import DATA_SOURCES, register_source

    seen: list[str] = []

    def vendor(ticker, interval):
        seen.append(ticker)
        return _series(2, 320.0) if ticker == "^VIX" else None

    prev = DATA_SOURCES.get("yfinance")
    try:
        register_source("yfinance", vendor)
        wrapped = DATA_SOURCES["yfinance"]
        # The documented __wrapped__ invariant must survive alias support.
        assert wrapped.__wrapped__ is vendor

        assert len(wrapped("VIX", "5m") or []) == 2
        assert seen == ["^VIX"]

        seen.clear()
        out = wrapped("VIX/16", "5m")
        assert out is not None and len(out) == 2
        assert seen == ["^VIX"]
        assert out[0].close == pytest.approx(320.0 / 16.0)
    finally:
        if prev is not None:
            DATA_SOURCES["yfinance"] = prev
        else:  # pragma: no cover - only if yfinance wasn't registered
            DATA_SOURCES.pop("yfinance", None)
