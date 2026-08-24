"""Invariants for the Quant catalog (`quant/catalog.py`).

The catalog is hand-maintained data that the GUI trusts blindly: the Quant
tab renders whatever is here and hands the symbol straight to the fetch
layer. A typo therefore surfaces as a silently-empty chart rather than an
error, which is exactly the class of bug these tests exist to catch.

The interesting cases are the ratio rows. A *scaled symbol* (`VIX/15.87`) is
an exact rescale and must have a positive divisor; a *quotient* (`RSP/SPY`)
inner-joins two real legs. Both must parse under `data.ratio_source`, and
index shorthand must survive `data.index_aliases.resolve_symbol` with its
scale constant untouched (AGENTS.md §7.37).

See `quant/catalog.spec.md`.
"""
from __future__ import annotations

import pytest

from tradinglab.data.index_aliases import NEVER_ALIAS, resolve_symbol
from tradinglab.data.ratio_source import (
    is_quotient_ratio,
    is_scaled_symbol,
    parse_ratio_symbol,
    parse_scale_constant,
    scaled_symbol_parts,
)
from tradinglab.quant.catalog import (
    QUANT_CATALOG,
    QuantRow,
    available_rows,
    available_symbols,
    iter_rows,
    row_for_key,
)

ALL_ROWS = list(iter_rows())


def test_catalog_is_not_empty():
    assert QUANT_CATALOG
    assert len(ALL_ROWS) >= 25, "the shipped catalog should be substantive"


def test_group_keys_and_names_are_unique():
    keys = [g.key for g in QUANT_CATALOG]
    names = [g.name for g in QUANT_CATALOG]
    assert len(keys) == len(set(keys))
    assert len(names) == len(set(names))
    assert all(g.rows for g in QUANT_CATALOG), "no empty groups"


def test_row_keys_are_unique_across_groups():
    keys = [r.key for r in ALL_ROWS]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("row", ALL_ROWS, ids=lambda r: r.key)
def test_row_has_name_and_description(row: QuantRow):
    assert row.name.strip()
    assert row.description.strip()


@pytest.mark.parametrize("row", ALL_ROWS, ids=lambda r: r.key)
def test_symbol_is_empty_iff_unavailable(row: QuantRow):
    """The tab keys its disabled rendering off exactly this pairing."""
    assert bool(row.symbol) is bool(row.available)
    assert bool(row.unavailable_reason) is not bool(row.available)


@pytest.mark.parametrize("row", available_rows(), ids=lambda r: r.key)
def test_available_symbol_is_clean(row: QuantRow):
    assert row.symbol == row.symbol.strip()
    assert " " not in row.symbol
    assert row.symbol == row.symbol.upper()


@pytest.mark.parametrize("row", available_rows(), ids=lambda r: r.key)
def test_ratio_rows_parse(row: QuantRow):
    """Every `A/B` row must be a legal ratio, and exactly one kind of one."""
    if "/" not in row.symbol:
        assert parse_ratio_symbol(row.symbol) is None
        return
    legs = parse_ratio_symbol(row.symbol)
    assert legs is not None, f"{row.symbol} does not parse as a ratio"
    assert is_scaled_symbol(row.symbol) ^ is_quotient_ratio(row.symbol)


@pytest.mark.parametrize(
    "row",
    [r for r in available_rows() if is_scaled_symbol(r.symbol)],
    ids=lambda r: r.key,
)
def test_scaled_rows_have_a_positive_divisor(row: QuantRow):
    """A zero or negative divisor is not order-preserving (§7.37)."""
    parts = scaled_symbol_parts(row.symbol)
    assert parts is not None
    _base, divisor = parts
    assert divisor > 0


@pytest.mark.parametrize(
    "row",
    [r for r in available_rows() if is_quotient_ratio(r.symbol)],
    ids=lambda r: r.key,
)
def test_quotient_legs_are_real_symbols(row: QuantRow):
    """Neither leg of a quotient may be numeric — that shape is unsupported."""
    legs = parse_ratio_symbol(row.symbol)
    assert legs is not None
    num, den = legs
    assert parse_scale_constant(num) is None
    assert parse_scale_constant(den) is None


@pytest.mark.parametrize("row", available_rows(), ids=lambda r: r.key)
def test_symbols_survive_source_resolution(row: QuantRow):
    """Resolution must be total, idempotent, and never eat a scale constant."""
    once = resolve_symbol(row.symbol, "yfinance")
    assert once
    assert resolve_symbol(once, "yfinance") == once
    if is_scaled_symbol(row.symbol):
        assert scaled_symbol_parts(once) is not None
        assert scaled_symbol_parts(once)[1] == scaled_symbol_parts(row.symbol)[1]


@pytest.mark.parametrize("row", available_rows(), ids=lambda r: r.key)
def test_no_row_uses_a_never_alias_shorthand(row: QuantRow):
    """`MOVE` is a listed equity; the index is only reachable as `^MOVE`.

    A bare `MOVE` row would chart Compass-style: the wrong instrument, with
    no error. See `data/index_aliases.spec.md`.
    """
    legs = parse_ratio_symbol(row.symbol)
    parts = list(legs) if legs else [row.symbol]
    for leg in parts:
        assert leg.upper() not in NEVER_ALIAS, (
            f"{row.key} uses bare {leg!r}, which is a real equity"
        )


def test_move_row_uses_the_explicit_index_form():
    row = row_for_key("move")
    assert row is not None
    assert row.symbol == "^MOVE"


def test_expected_move_divisors_are_sqrt_periods():
    """The three SPY expected-move rows encode √252 / √52 / √12."""
    import math

    expected = {
        "spy_em_1d": math.sqrt(252),
        "spy_em_1w": math.sqrt(52),
        "spy_em_1m": math.sqrt(12),
        "qqq_em_1d": math.sqrt(252),
    }
    for key, want in expected.items():
        row = row_for_key(key)
        assert row is not None, key
        parts = scaled_symbol_parts(row.symbol)
        assert parts is not None, key
        assert parts[1] == pytest.approx(want, abs=0.01), key


def test_available_symbols_is_ordered_and_deduped():
    syms = available_symbols()
    upper = [s.upper() for s in syms]
    assert len(upper) == len(set(upper))
    # Order matches first appearance in the catalog.
    first_seen: list[str] = []
    seen: set[str] = set()
    for row in available_rows():
        if row.symbol.upper() not in seen:
            seen.add(row.symbol.upper())
            first_seen.append(row.symbol)
    assert syms == first_seen


def test_gex_and_dix_ship_but_are_disabled():
    """They belong in a market-internals panel; the gap must be visible."""
    for key in ("gex", "dix"):
        row = row_for_key(key)
        assert row is not None, key
        assert row.available is False
        assert row.symbol == ""
        assert row.unavailable_reason


def test_the_users_original_eight_are_present():
    """Regression guard on the set the feature was requested with."""
    wanted = {"VIX", "VIX/15.87", "RSP", "RSP/SPY", "HYG", "TLT"}
    have = {s.upper() for s in available_symbols()}
    assert wanted <= have
    assert row_for_key("gex") is not None
    assert row_for_key("dix") is not None


def test_row_for_key_returns_none_for_unknown():
    assert row_for_key("no-such-row") is None
