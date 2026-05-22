"""Pure-logic tests for ``gui.live_price_overlay`` helpers.

Covers ``format_price`` and ``resolve_price`` without any matplotlib /
Tk dependency. The renderer itself is tested in
``test_live_price_overlay_integration.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import pytest

from tradinglab.gui.live_price_overlay import (
    LIVE_PRICE_LINESTYLE,
    LIVE_PRICE_ZORDER,
    format_price,
    resolve_price,
)

# ---------------------------------------------------------------------------
# format_price
# ---------------------------------------------------------------------------


class TestFormatPrice:

    @pytest.mark.parametrize("p,expected", [
        (100.0, "100.00"),
        (1.5, "1.50"),
        (1234.5678, "1,234.57"),
        (1_000_000.0, "1,000,000.00"),
        (-1.0, "-1.00"),
        (-1234.56, "-1,234.56"),
    ])
    def test_dollar_plus_uses_two_decimals_with_thousands_sep(self, p, expected):
        assert format_price(p) == expected

    @pytest.mark.parametrize("p,expected", [
        (0.5, "0.500"),
        (0.075, "0.075"),
        (0.001, "0.001"),
        (-0.5, "-0.500"),
        (0.0, "0.000"),
    ])
    def test_sub_dollar_uses_three_decimals(self, p, expected):
        assert format_price(p) == expected

    @pytest.mark.parametrize("bad", [
        None,
        float("nan"),
        float("inf"),
        float("-inf"),
        "not-a-number",
        object(),
    ])
    def test_non_finite_or_garbage_returns_empty(self, bad):
        assert format_price(bad) == ""

    def test_string_numbers_are_coerced(self):
        # str(float) round-trips → string numerics are accepted.
        assert format_price("12.5") == "12.50"
        assert format_price("0.075") == "0.075"


# ---------------------------------------------------------------------------
# resolve_price
# ---------------------------------------------------------------------------


@dataclass
class _FakeCandle:
    close: float
    is_gap: bool = False


class TestResolvePrice:

    def test_stream_price_wins_over_candle(self):
        ps = {"candles": [_FakeCandle(close=100.0)]}
        price = resolve_price(
            "AAPL",
            last_stream_price={"AAPL": 200.0},
            panel_state_slot=ps,
        )
        assert price == 200.0

    def test_falls_back_to_last_candle_close_when_no_stream(self):
        ps = {"candles": [_FakeCandle(close=100.0), _FakeCandle(close=101.5)]}
        price = resolve_price(
            "AAPL",
            last_stream_price={},
            panel_state_slot=ps,
        )
        assert price == 101.5

    def test_skips_trailing_gap_candles(self):
        ps = {"candles": [
            _FakeCandle(close=99.0),
            _FakeCandle(close=100.0),
            _FakeCandle(close=float("nan"), is_gap=True),
        ]}
        price = resolve_price(
            "AAPL",
            last_stream_price={},
            panel_state_slot=ps,
        )
        assert price == 100.0

    def test_returns_none_when_all_candles_are_gaps(self):
        ps = {"candles": [
            _FakeCandle(close=float("nan"), is_gap=True),
            _FakeCandle(close=float("nan"), is_gap=True),
        ]}
        price = resolve_price(
            "AAPL",
            last_stream_price={},
            panel_state_slot=ps,
        )
        assert price is None

    def test_returns_none_when_panel_state_is_none(self):
        price = resolve_price(
            "AAPL",
            last_stream_price={},
            panel_state_slot=None,
        )
        assert price is None

    def test_returns_none_when_no_candles_and_no_stream(self):
        price = resolve_price(
            "AAPL",
            last_stream_price={},
            panel_state_slot={"candles": []},
        )
        assert price is None

    def test_symbol_is_normalised_to_upper_strip(self):
        ps = {"candles": []}
        price = resolve_price(
            " aapl ",
            last_stream_price={"AAPL": 150.0},
            panel_state_slot=ps,
        )
        assert price == 150.0

    def test_non_finite_stream_falls_through_to_candle(self):
        ps = {"candles": [_FakeCandle(close=100.0)]}
        price = resolve_price(
            "AAPL",
            last_stream_price={"AAPL": float("nan")},
            panel_state_slot=ps,
        )
        assert price == 100.0

    def test_non_finite_candle_close_skipped(self):
        ps = {"candles": [
            _FakeCandle(close=99.0),
            _FakeCandle(close=float("nan")),  # not gap, but NaN
        ]}
        price = resolve_price(
            "AAPL",
            last_stream_price={},
            panel_state_slot=ps,
        )
        # The walk skips the trailing NaN and returns the prior finite.
        assert price == 99.0

    def test_empty_symbol_falls_through_to_candle(self):
        ps = {"candles": [_FakeCandle(close=100.0)]}
        price = resolve_price(
            "",
            last_stream_price={"AAPL": 200.0},  # would match if symbol given
            panel_state_slot=ps,
        )
        # Empty symbol can't index the stream dict ⇒ candle fallback.
        assert price == 100.0


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_linestyle_is_dotted_tuple():
    """Pinned: line is dashed/dotted, not solid."""
    assert LIVE_PRICE_LINESTYLE == (0, (2, 3))


def test_zorder_below_overlays_above_grid():
    """Pinned: zorder=3 — below exits/entries overlays (z=4), above grid."""
    assert LIVE_PRICE_ZORDER == 3
