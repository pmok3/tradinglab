"""Pin that the strategy_tester evaluator's Side-pilot migration didn't
change observable behavior.

Audit #10 — ``core/side.py`` adoption in
``strategy_tester/evaluator.py``. The migrated call sites all live
behind the existing public ``evaluate_symbol`` surface; this file
exercises the paths most likely to regress on a sign / favorable-price
flip while explicitly checking that the persisted ``PostTradeReview.side``
buy/sell string vocabulary stays intact (NB: PostTradeReview.side is
``"buy" / "sell"`` per ``backtest/journal.py`` — the ``"long" / "short"``
vocabulary used by ``exits/spec.py`` is a separate persisted shape).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tradinglab.core.side import Side
from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    ShareRounding,
    SizingKind,
    SizingRule,
)
from tradinglab.entries.model import TriggerKind as EntryTriggerKind
from tradinglab.entries.model import Universe as EntryUniverse
from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger
from tradinglab.exits.model import TriggerKind as ExitTriggerKind
from tradinglab.models import Candle
from tradinglab.strategy_tester import CostModel, evaluate_symbol

_ET = ZoneInfo("America/New_York")


def _bar(t: datetime, o: float, h: float, low: float, c: float) -> Candle:
    return Candle(date=t, open=o, high=h, low=low, close=c, volume=1_000,
                  session="regular")


def _ramp(n: int, start: float, step: float) -> list[Candle]:
    out: list[Candle] = []
    t = datetime(2026, 1, 5, 9, 35, tzinfo=_ET)  # Monday RTH start
    price = start
    for _ in range(n):
        op = price
        cl = price + step
        hi = max(op, cl) + 0.2
        lo = min(op, cl) - 0.2
        out.append(_bar(t, op, hi, lo, cl))
        price = cl
        t += timedelta(minutes=5)
    return out


def _entry(direction: Direction) -> EntryStrategy:
    return EntryStrategy(
        id="e",
        name="e",
        direction=direction,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=5.0,
                          share_rounding=ShareRounding.DOWN),
        max_fires_per_session_per_symbol=1,
    )


def _exit_stop_pct(pct: float) -> ExitStrategy:
    return ExitStrategy(
        id="x",
        name="x",
        legs=[ExitLeg(id="leg", triggers=[ExitTrigger(
            kind=ExitTriggerKind.STOP, offset_pct=pct, qty_pct=100.0)])],
        eod_kill_switch=False,
    )


def _exit_limit_pct(pct: float) -> ExitStrategy:
    return ExitStrategy(
        id="x",
        name="x",
        legs=[ExitLeg(id="leg", triggers=[ExitTrigger(
            kind=ExitTriggerKind.LIMIT, offset_pct=pct, qty_pct=100.0)])],
        eod_kill_switch=False,
    )


def _run(candles, entry, exit_):
    return evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_,
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0, commission_per_trade=0.0),
    )


def test_long_stop_exit_persists_long_string() -> None:
    """LONG entry + STOP exit pct-offset uses the migrated
    ``_exit_stop`` Side branch (adverse = low for long). The persisted
    ``post.side`` MUST stay the legacy ``"buy"`` string."""
    candles = _ramp(n=10, start=100.0, step=1.0)
    # Dip bar — should take out a 5% stop on the long entry.
    candles.append(_bar(candles[-1].date + timedelta(minutes=5),
                        candles[-1].close, candles[-1].close + 0.1,
                        85.0, 86.0))
    # Trailing bar so the exit order has a next-bar open to fill on.
    candles.append(_bar(candles[-1].date + timedelta(minutes=5),
                        86.0, 87.0, 85.5, 86.5))
    res = _run(candles, _entry(Direction.LONG), _exit_stop_pct(5.0))
    assert len(res.post_trades) == 1
    assert res.post_trades[0].side == Side.LONG.as_buy_sell() == "buy"


def test_short_stop_exit_persists_short_string() -> None:
    """SHORT entry + STOP exit uses the OTHER branch of the migrated
    Side check (adverse = high for short)."""
    candles = _ramp(n=10, start=100.0, step=-1.0)
    # Rip bar — should take out a 5% stop on the short entry.
    candles.append(_bar(candles[-1].date + timedelta(minutes=5),
                        candles[-1].close, 115.0,
                        candles[-1].close - 0.1, 114.0))
    candles.append(_bar(candles[-1].date + timedelta(minutes=5),
                        114.0, 114.5, 113.5, 114.0))
    res = _run(candles, _entry(Direction.SHORT), _exit_stop_pct(5.0))
    assert len(res.post_trades) == 1
    assert res.post_trades[0].side == Side.SHORT.as_buy_sell() == "sell"


def test_long_limit_exit_favorable_high_takes_profit() -> None:
    """LONG entry + LIMIT exit uses the migrated ``_exit_limit``
    favorable-price branch (favorable = high for long). Should
    take-profit on the ramp."""
    candles = _ramp(n=10, start=100.0, step=1.0)
    res = _run(candles, _entry(Direction.LONG), _exit_limit_pct(1.0))
    assert len(res.post_trades) == 1
    post = res.post_trades[0]
    assert post.side == "buy"
    assert post.exit_price >= post.entry_price


@pytest.mark.parametrize("direction,expected_side", [
    (Direction.LONG, "buy"),
    (Direction.SHORT, "sell"),
])
def test_eod_kill_switch_uses_side_opposite(direction, expected_side) -> None:
    """End-of-run EOD kill flatten uses
    ``Side.from_str(ctx.position_side).opposite().as_order_side()``
    to pick the synthetic exit-fill side."""
    candles = _ramp(n=10, start=100.0,
                    step=1.0 if direction is Direction.LONG else -1.0)
    exit_ = ExitStrategy(id="x", name="x", legs=[], eod_kill_switch=True)
    res = _run(candles, _entry(direction), exit_)
    assert len(res.post_trades) == 1, "EOD kill should close exactly one trade"
    assert res.post_trades[0].side == expected_side
    # entry fill + EOD flatten fill.
    assert len(res.fills) == 2


def test_sync_position_state_round_trips_through_from_sign() -> None:
    """``ctx.position_side`` is assigned via
    ``Side.from_sign(qty).as_buy_sell()`` after every engine sync.
    Pin that the resulting Fill.side string round-trips through Side."""
    candles = _ramp(n=10, start=100.0, step=1.0)
    candles.append(_bar(candles[-1].date + timedelta(minutes=5),
                        candles[-1].close, candles[-1].close + 0.1,
                        85.0, 86.0))
    candles.append(_bar(candles[-1].date + timedelta(minutes=5),
                        86.0, 87.0, 85.5, 86.5))
    res = _run(candles, _entry(Direction.LONG), _exit_stop_pct(5.0))
    assert len(res.fills) == 2
    # Entry side as plumbed through Side.LONG.as_buy_sell().
    assert res.fills[0].side.value == Side.LONG.as_buy_sell() == "buy"
    # Exit side via Side.LONG.opposite().as_order_side().
    assert (res.fills[1].side.value
            == Side.LONG.opposite().as_buy_sell()
            == "sell")
