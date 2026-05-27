"""Unit tests for the canonical :class:`tradinglab.core.side.Side`."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradinglab.backtest.orders import Side as OrderSide
from tradinglab.core.side import Side
from tradinglab.models import Candle

# ---------------------------------------------------------------------------
# from_str — every accepted vocabulary round-trips
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["long", "LONG", "Long", "buy", "BUY", " buy ", "l", "L", "+1", "1"],
)
def test_from_str_long_aliases(value: str) -> None:
    assert Side.from_str(value) is Side.LONG


@pytest.mark.parametrize(
    "value",
    ["short", "SHORT", "Short", "sell", "SELL", " sell ", "s", "S", "-1"],
)
def test_from_str_short_aliases(value: str) -> None:
    assert Side.from_str(value) is Side.SHORT


@pytest.mark.parametrize("value", ["", "flat", "neutral", "0", "longish", "x"])
def test_from_str_rejects_unknown(value: str) -> None:
    with pytest.raises(ValueError, match="Side.from_str"):
        Side.from_str(value)


def test_from_str_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="expected a string"):
        Side.from_str(1)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# from_order_side / from_sign
# ---------------------------------------------------------------------------


def test_from_order_side_buy_to_long() -> None:
    assert Side.from_order_side(OrderSide.BUY) is Side.LONG


def test_from_order_side_sell_to_short() -> None:
    assert Side.from_order_side(OrderSide.SELL) is Side.SHORT


@pytest.mark.parametrize("sign,expected", [(1, Side.LONG), (2.5, Side.LONG),
                                            (-1, Side.SHORT), (-0.1, Side.SHORT)])
def test_from_sign(sign: float, expected: Side) -> None:
    assert Side.from_sign(sign) is expected


def test_from_sign_zero_raises() -> None:
    with pytest.raises(ValueError, match="zero"):
        Side.from_sign(0)


# ---------------------------------------------------------------------------
# Adapters back to legacy vocabularies
# ---------------------------------------------------------------------------


def test_as_long_short() -> None:
    assert Side.LONG.as_long_short() == "long"
    assert Side.SHORT.as_long_short() == "short"


def test_as_buy_sell() -> None:
    assert Side.LONG.as_buy_sell() == "buy"
    assert Side.SHORT.as_buy_sell() == "sell"


def test_as_order_side() -> None:
    assert Side.LONG.as_order_side() is OrderSide.BUY
    assert Side.SHORT.as_order_side() is OrderSide.SELL


def test_round_trip_all_vocabularies() -> None:
    for s in (Side.LONG, Side.SHORT):
        assert Side.from_str(s.as_long_short()) is s
        assert Side.from_str(s.as_buy_sell()) is s
        assert Side.from_order_side(s.as_order_side()) is s


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------


def test_sign() -> None:
    assert Side.LONG.sign == 1
    assert Side.SHORT.sign == -1


def test_is_long_is_short() -> None:
    assert Side.LONG.is_long is True
    assert Side.LONG.is_short is False
    assert Side.SHORT.is_long is False
    assert Side.SHORT.is_short is True


def test_opposite() -> None:
    assert Side.LONG.opposite() is Side.SHORT
    assert Side.SHORT.opposite() is Side.LONG
    assert Side.LONG.opposite().opposite() is Side.LONG


# ---------------------------------------------------------------------------
# Bar-price helpers
# ---------------------------------------------------------------------------


def _bar() -> Candle:
    return Candle(
        date=datetime(2024, 6, 3, 13, 30, tzinfo=timezone.utc),
        open=100.0, high=105.0, low=95.0, close=102.0, volume=1_000,
    )


def test_favorable_price() -> None:
    bar = _bar()
    assert Side.LONG.favorable_price(bar) == 105.0  # high
    assert Side.SHORT.favorable_price(bar) == 95.0  # low


def test_unfavorable_price() -> None:
    bar = _bar()
    assert Side.LONG.unfavorable_price(bar) == 95.0  # low
    assert Side.SHORT.unfavorable_price(bar) == 105.0  # high


def test_mae_mfe_aliases_agree_with_underlying() -> None:
    bar = _bar()
    for side in (Side.LONG, Side.SHORT):
        assert side.adverse_excursion_price(bar) == side.unfavorable_price(bar)
        assert side.favorable_excursion_price(bar) == side.favorable_price(bar)


def test_price_helpers_accept_duck_typed_bar() -> None:
    """Any object with .high/.low works — the strategy_tester ``_BarTuple``
    isn't a Candle but the helpers should still apply."""

    class DuckBar:
        high = 50.0
        low = 40.0

    bar = DuckBar()
    assert Side.LONG.favorable_price(bar) == 50.0
    assert Side.SHORT.unfavorable_price(bar) == 50.0
    assert Side.LONG.unfavorable_price(bar) == 40.0


def test_price_helpers_return_float() -> None:
    """Numpy scalars / ints get coerced to plain float."""
    import numpy as np

    class NpBar:
        high = np.float64(105.0)
        low = np.float64(95.0)

    bar = NpBar()
    fav = Side.LONG.favorable_price(bar)
    assert isinstance(fav, float)
    assert fav == 105.0
