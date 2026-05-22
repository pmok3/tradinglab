"""Tests for tradinglab.entries.sizing."""

from __future__ import annotations

import pytest

from tradinglab.entries.model import ShareRounding, SizingKind, SizingRule
from tradinglab.entries.sizing import InvalidSizing, compute_qty


class TestFixedQty:
    def test_returns_qty(self):
        rule = SizingRule(kind=SizingKind.FIXED_QTY, qty=100)
        assert compute_qty(rule, ref_price=50.0) == 100.0

    def test_ignores_ref_price(self):
        rule = SizingRule(kind=SizingKind.FIXED_QTY, qty=42)
        assert compute_qty(rule, ref_price=0.01) == 42.0
        assert compute_qty(rule, ref_price=100_000.0) == 42.0

    def test_zero_qty_raises(self):
        rule = SizingRule(kind=SizingKind.FIXED_QTY, qty=0)
        with pytest.raises(InvalidSizing, match="qty"):
            compute_qty(rule, ref_price=100.0)

    def test_none_qty_raises(self):
        rule = SizingRule(kind=SizingKind.FIXED_QTY, qty=None)
        with pytest.raises(InvalidSizing):
            compute_qty(rule, ref_price=100.0)


class TestFixedNotional:
    def test_round_down_basic(self):
        rule = SizingRule(
            kind=SizingKind.FIXED_NOTIONAL, notional=10_000,
            share_rounding=ShareRounding.DOWN,
        )
        # 10000/103 = 97.087 -> floor -> 97
        assert compute_qty(rule, ref_price=103.0) == 97.0

    def test_round_nearest(self):
        rule = SizingRule(
            kind=SizingKind.FIXED_NOTIONAL, notional=10_000,
            share_rounding=ShareRounding.NEAREST,
        )
        # 10000/103 = 97.087 -> round -> 97
        assert compute_qty(rule, ref_price=103.0) == 97.0
        # 10000/100.6 = 99.40 -> round -> 99
        assert compute_qty(rule, ref_price=100.6) == 99.0
        # 10000/100.5 = 99.50 -> round-half-to-even -> 100
        assert compute_qty(rule, ref_price=100.5) == 100.0

    def test_notional_too_small_raises(self):
        rule = SizingRule(
            kind=SizingKind.FIXED_NOTIONAL, notional=50,
            share_rounding=ShareRounding.DOWN,
        )
        with pytest.raises(InvalidSizing, match="too small"):
            compute_qty(rule, ref_price=100.0)

    def test_zero_ref_price_raises(self):
        rule = SizingRule(
            kind=SizingKind.FIXED_NOTIONAL, notional=10_000,
        )
        with pytest.raises(InvalidSizing, match="ref_price"):
            compute_qty(rule, ref_price=0.0)

    def test_zero_notional_raises(self):
        rule = SizingRule(
            kind=SizingKind.FIXED_NOTIONAL, notional=0,
        )
        with pytest.raises(InvalidSizing, match="notional"):
            compute_qty(rule, ref_price=100.0)
