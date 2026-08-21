"""Unit tests for SCALED symbols — ``SYMBOL/<positive number>``.

A scaled symbol (``^VIX/15.87``, ``SPX/10``) shares the ``NUM/DEN`` syntax of
a quotient ratio but is a fundamentally different object: one real instrument
on a rescaled axis. These tests pin the properties that distinguish it —
exactness, no bar loss, preserved volume — and the grammar's rejections.

See `data/ratio_source.spec.md` and AGENTS.md §7.37.
"""
from __future__ import annotations

import datetime as dt

import pytest

from tradinglab.data.ratio_source import (
    base_symbol_of,
    compute_scaled_candles,
    fetch_ratio,
    is_numeric_leg,
    is_quotient_ratio,
    is_ratio_symbol,
    is_scaled_symbol,
    parse_scale_constant,
    ratio_display_label,
    scaled_symbol_parts,
)
from tradinglab.models import Candle


def _series(n=3, base=320.0):
    out = []
    t = dt.datetime(2026, 6, 15, 9, 30)
    for i in range(n):
        p = base + i
        out.append(Candle(date=t, open=p, high=p + 2.0, low=p - 1.0,
                          close=p + 0.5, volume=1000 + i, session="regular"))
        t += dt.timedelta(minutes=5)
    return out


# ------------------------------------------------------------- number grammar
@pytest.mark.parametrize("leg,expected", [
    ("16", 16.0),
    ("15.87", 15.87),
    ("0.5", 0.5),
    ("100", 100.0),
    (" 16 ", 16.0),
])
def test_parse_scale_constant_accepts_positive_decimals(leg, expected):
    assert parse_scale_constant(leg) == pytest.approx(expected)


@pytest.mark.parametrize("leg", [
    "0",        # divide-by-zero
    "0.0",      # ditto
    "-16",      # a sign flip would invert the candle
    "+16",      # ambiguous, no upside
    "1e3",      # scientific notation: lexer surface for no benefit
    "16,000",   # locale/thousands ambiguity
    "1.2.3",    # malformed
    "ABC", "", "  ",
])
def test_parse_scale_constant_rejects_everything_else(leg):
    assert parse_scale_constant(leg) is None


def test_is_numeric_leg_distinguishes_shape_from_validity():
    """``0`` LOOKS numeric but isn't usable — the distinction stops it being
    fetched as a vendor ticker literally named "0"."""
    assert is_numeric_leg("0") is True
    assert parse_scale_constant("0") is None
    assert is_numeric_leg("AAPL") is False


# ---------------------------------------------------------- shape classifying
@pytest.mark.parametrize("ticker,base,k", [
    ("^VIX/15.87", "^VIX", 15.87),
    ("SPX/10", "SPX", 10.0),
    ("AAPL/100", "AAPL", 100.0),
    ("spx / 10", "SPX", 10.0),        # case + whitespace tolerant
    ("BRK-B/2", "BRK-B", 2.0),        # hyphenated symbols still work
])
def test_scaled_symbol_parts(ticker, base, k):
    parts = scaled_symbol_parts(ticker)
    assert parts is not None
    assert parts[0] == base
    assert parts[1] == pytest.approx(k)
    assert is_scaled_symbol(ticker) is True
    assert is_quotient_ratio(ticker) is False
    assert is_ratio_symbol(ticker) is True   # still "has a /" for cache gating


@pytest.mark.parametrize("ticker", [
    "AMD/NVDA", "XLF/SPY", "RSP/SPY",
    "100/VIX",     # constant numerator — unsupported shape, not "scaled"
    "16/4",        # both constant
    "^VIX/0",      # invalid divisor
    "^VIX/-2",     # negative divisor
    "AAPL",        # not a ratio at all
])
def test_not_a_scaled_symbol(ticker):
    assert scaled_symbol_parts(ticker) is None
    assert is_scaled_symbol(ticker) is False


def test_quotient_and_scaled_are_mutually_exclusive():
    assert is_quotient_ratio("AMD/NVDA") is True
    assert is_scaled_symbol("AMD/NVDA") is False
    assert is_quotient_ratio("^VIX/15.87") is False
    assert is_scaled_symbol("^VIX/15.87") is True


def test_base_symbol_of():
    assert base_symbol_of("^VIX/15.87") == "^VIX"
    assert base_symbol_of("AAPL/100") == "AAPL"
    assert base_symbol_of("AMD/NVDA") == "AMD/NVDA"   # quotient: no single base
    assert base_symbol_of("AAPL") == "AAPL"


def test_display_label_shows_the_divisor():
    """Decision: a scaled chart renders exactly like a quotient (``A / B``).
    The divisor must be visible so a scaled chart is never misread as raw."""
    assert ratio_display_label("^VIX/15.87") == "^VIX / 15.87"
    assert ratio_display_label("SPX/10") == "SPX / 10"


# ------------------------------------------------------------------- compute
def test_scaled_compute_is_exact_and_lossless():
    src = _series(4)
    out = compute_scaled_candles(src, 16.0)
    assert len(out) == len(src)          # a constant has no calendar -> no join
    for got, want in zip(out, src, strict=True):
        assert got.open == want.open / 16.0
        assert got.high == want.high / 16.0
        assert got.low == want.low / 16.0
        assert got.close == want.close / 16.0
        assert got.date == want.date


def test_scaled_compute_preserves_volume():
    """Unlike a quotient (volume forced to 0), a scaled symbol is ONE real
    instrument — its volume, and every volume-weighted study over it, stays
    meaningful."""
    src = _series(3)
    out = compute_scaled_candles(src, 10.0)
    assert [c.volume for c in out] == [c.volume for c in src]
    assert all(c.volume > 0 for c in out)


def test_scaled_compute_does_not_widen_high_low():
    """Dividing by k>0 is order-preserving, so H/k IS the true high — no
    envelope approximation (the quotient path's max/min repair) is applied."""
    src = _series(3)
    out = compute_scaled_candles(src, 4.0)
    for got, want in zip(out, src, strict=True):
        assert got.high == want.high / 4.0     # exactly, not max(o,h,l,c)
        assert got.low == want.low / 4.0
        assert got.high >= max(got.open, got.close)
        assert got.low <= min(got.open, got.close)


def test_scaled_compute_carries_session():
    out = compute_scaled_candles(_series(2), 2.0)
    assert all(c.session == "regular" for c in out)


def test_scaled_compute_guards():
    assert compute_scaled_candles([], 2.0) == []
    assert compute_scaled_candles(_series(2), 0) == []
    assert compute_scaled_candles(_series(2), -3) == []


# --------------------------------------------------------------- fetch routing
def _legs_recorder(known):
    seen: list[str] = []

    def fetcher(ticker, interval):
        seen.append(ticker)
        return _series(3) if ticker in known else None

    return fetcher, seen


def test_fetch_scaled_fetches_only_the_real_leg():
    fetcher, seen = _legs_recorder({"^VIX"})
    out = fetch_ratio("^VIX/16", "5m", leg_fetcher=fetcher)
    assert out is not None and len(out) == 3
    assert seen == ["^VIX"]          # the constant is never fetched
    assert out[0].close == pytest.approx(_series(3)[0].close / 16.0)


def test_fetch_scaled_propagates_leg_failure():
    fetcher, seen = _legs_recorder(set())
    assert fetch_ratio("^VIX/16", "5m", leg_fetcher=fetcher) is None
    assert seen == ["^VIX"]


@pytest.mark.parametrize("ticker", ["100/^VIX", "16/4", "^VIX/0", "0/^VIX"])
def test_fetch_rejects_unsupported_numeric_shapes_without_fetching(ticker):
    """A numeric-looking leg is never a vendor ticker: these must fail at the
    parser, not by asking the source for a symbol named "0" or "100"."""
    fetcher, seen = _legs_recorder({"^VIX", "0", "100", "16", "4"})
    assert fetch_ratio(ticker, "5m", leg_fetcher=fetcher) is None
    assert seen == []


def test_fetch_quotient_path_still_works():
    """The two-symbol path is untouched by the scaled fast-path."""
    fetcher, seen = _legs_recorder({"AMD", "NVDA"})
    out = fetch_ratio("AMD/NVDA", "5m", leg_fetcher=fetcher)
    assert out is not None and len(out) == 3
    assert seen == ["AMD", "NVDA"]
    assert all(c.volume == 0 for c in out)   # quotient volume stays 0
