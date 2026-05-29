"""Strategy-tester cross-symbol dependency wiring tests."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
from tradinglab.exits.model import ExitStrategy
from tradinglab.models import Candle
from tradinglab.scanner.model import OP_GT, Condition, FieldRef, Group
from tradinglab.strategy_tester import CostModel
from tradinglab.strategy_tester.evaluator import evaluate_symbol

_ET = ZoneInfo("America/New_York")


def _rth_candles(closes: list[float], *, symbol_offset: float = 0.0) -> list[Candle]:
    start = datetime(2026, 1, 5, 9, 30, tzinfo=_ET)
    out: list[Candle] = []
    for i, close in enumerate(closes):
        t = start + timedelta(minutes=5 * i)
        op = close - 0.1 + symbol_offset
        out.append(Candle(
            date=t,
            open=op,
            high=max(op, close) + 0.2,
            low=min(op, close) - 0.2,
            close=close,
            volume=1000 + i,
            session="regular",
        ))
    return out


def _spy_gated_entry() -> EntryStrategy:
    condition = Group(combinator="and", children=[
        Condition(
            left=FieldRef.builtin("close", symbol="SPY"),
            op=OP_GT,
            params={"right": FieldRef.literal(100.0)},
        ),
    ])
    return EntryStrategy(
        id="e-cross",
        name="spy gated",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("AAPL",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.INDICATOR, condition=condition),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY,
            qty=10.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
        require_market_open=False,
    )


def test_evaluate_symbol_uses_dependency_candles_for_cross_symbol_conditions():
    aapl = _rth_candles([90.0, 91.0, 92.0, 93.0, 94.0])
    spy = _rth_candles([150.0, 151.0, 152.0, 153.0, 154.0])

    without_dependency = evaluate_symbol(
        symbol="AAPL",
        candles=aapl,
        interval="5m",
        entry_strategy=_spy_gated_entry(),
        exit_strategy=ExitStrategy(id="x", name="eod", legs=[], eod_kill_switch=True),
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0),
    )
    with_dependency = evaluate_symbol(
        symbol="AAPL",
        candles=aapl,
        interval="5m",
        entry_strategy=_spy_gated_entry(),
        exit_strategy=ExitStrategy(id="x", name="eod", legs=[], eod_kill_switch=True),
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0),
        dependency_candles={"SPY": spy},
    )

    assert len(without_dependency.fills) == 0
    assert len(with_dependency.fills) >= 2
    assert len(with_dependency.post_trades) == 1
