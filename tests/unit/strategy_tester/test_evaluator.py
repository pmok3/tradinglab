"""Unit tests for strategy_tester.evaluator."""

from __future__ import annotations

from datetime import datetime, timedelta

from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    ShareRounding,
    SizingKind,
    SizingRule,
)
from tradinglab.entries.model import (
    TriggerKind as EntryTriggerKind,
)
from tradinglab.entries.model import (
    Universe as EntryUniverse,
)
from tradinglab.exits.model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
)
from tradinglab.exits.model import (
    TriggerKind as ExitTriggerKind,
)
from tradinglab.models import Candle
from tradinglab.strategy_tester import (
    CostModel,
    UnsupportedTriggerKind,
    evaluate_symbol,
)


def _ramp_candles(n: int = 30, start: float = 100.0, step: float = 1.0) -> list[Candle]:
    """Linear ramp — close grows by ``step`` per bar so every trigger touches predictably."""
    out: list[Candle] = []
    t = datetime(2026, 1, 1, 9, 30)
    price = start
    for i in range(n):
        op = price
        cl = price + step
        hi = max(op, cl) + 0.2
        lo = min(op, cl) - 0.2
        out.append(Candle(date=t, open=op, high=hi, low=lo, close=cl,
                          volume=1000 + i, session="regular"))
        price = cl
        t = t + timedelta(minutes=5)
    return out


def _market_long_strategy() -> EntryStrategy:
    return EntryStrategy(
        id="entry-test",
        name="Market Long",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(kind=EntryTriggerKind.MARKET),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY, qty=5.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def _stop_5pct_exit() -> ExitStrategy:
    return ExitStrategy(
        id="exit-test",
        name="5% stop",
        legs=[
            ExitLeg(
                id="leg-stop",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.STOP,
                        offset_pct=5.0,
                        qty_pct=100.0,
                    ),
                ],
            ),
        ],
        eod_kill_switch=False,
    )


def test_market_entry_fires_on_first_eligible_bar() -> None:
    entry = _market_long_strategy()
    exit_strat = _stop_5pct_exit()
    candles = _ramp_candles(n=30)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0, commission_per_trade=0.0),
    )
    # At least one fill (the entry).
    assert len(result.fills) >= 1
    assert result.fills[0].side.value == "buy"
    assert result.fills[0].quantity == 5.0


def test_eod_kill_switch_flattens_at_end() -> None:
    entry = _market_long_strategy()
    exit_strat = _stop_5pct_exit()
    # Force kill-switch on
    exit_strat.eod_kill_switch = True
    candles = _ramp_candles(n=30)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(slippage_bps=0.0, commission_per_trade=0.0),
    )
    # Net position should be zero after the EOD sweep.
    pos = sum(
        (f.quantity if f.side.value == "buy" else -f.quantity)
        for f in result.fills
    )
    assert pos == 0.0


def test_empty_candles_returns_empty_session() -> None:
    entry = _market_long_strategy()
    exit_strat = _stop_5pct_exit()
    result = evaluate_symbol(
        symbol="TEST",
        candles=[],
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    assert result.fills == []
    assert result.equity_curve == []
    assert result.spec.tickers == ("TEST",)


def test_unsupported_entry_kind_raises() -> None:
    entry = _market_long_strategy()
    entry.trigger = EntryTrigger(kind=EntryTriggerKind.SCANNER_ALERT, scanner_id="x")
    exit_strat = _stop_5pct_exit()
    candles = _ramp_candles(n=5)

    try:
        evaluate_symbol(
            symbol="TEST",
            candles=candles,
            interval="5m",
            entry_strategy=entry,
            exit_strategy=exit_strat,
            starting_cash=100_000.0,
            cost_model=CostModel(),
        )
        raise AssertionError("expected UnsupportedTriggerKind")
    except UnsupportedTriggerKind as exc:
        assert exc.side == "entry"


def test_max_fires_per_symbol_one_only_one_entry() -> None:
    entry = _market_long_strategy()
    entry.max_fires_per_session_per_symbol = 1
    exit_strat = _stop_5pct_exit()
    exit_strat.eod_kill_switch = False
    candles = _ramp_candles(n=30)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    # Count BUY fills — should be exactly 1.
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert len(buys) == 1
