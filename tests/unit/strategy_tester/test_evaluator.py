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


# ---------------------------------------------------------------------------
# INDICATOR trigger support (PR-1 / strategy_tester evaluator follow-up)
# ---------------------------------------------------------------------------


def _close_gt_threshold(threshold: float, *, interval: str = "5m") -> object:
    """Build a one-leaf scanner.Group: ``close > <threshold>``."""
    from tradinglab.scanner.model import (
        OP_GT,
        Condition,
        FieldRef,
        Group,
    )

    return Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef(kind="builtin", id="close"),
                op=OP_GT,
                params={
                    "right": FieldRef(kind="literal", value=float(threshold)),
                },
                interval=interval,
            ),
        ],
    )


def _indicator_long_strategy(condition: object) -> EntryStrategy:
    return EntryStrategy(
        id="entry-ind",
        name="Indicator Long",
        direction=Direction.LONG,
        universe=EntryUniverse(symbols=("TEST",)),
        trigger=EntryTrigger(
            kind=EntryTriggerKind.INDICATOR,
            condition=condition,  # type: ignore[arg-type]
            interval="5m",
        ),
        sizing=SizingRule(
            kind=SizingKind.FIXED_QTY, qty=5.0,
            share_rounding=ShareRounding.DOWN,
        ),
        max_fires_per_session_per_symbol=1,
    )


def test_indicator_entry_fires_when_condition_becomes_true() -> None:
    """Ramp from 100 stepping +1; 'close > 105' fires once close crosses 105."""
    condition = _close_gt_threshold(105.0)
    entry = _indicator_long_strategy(condition)
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
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert len(buys) == 1, f"expected exactly 1 BUY fill, got {len(buys)}"
    # Decision at bar close → fill at next bar's open. The fill price must
    # be at or above the bar where the condition first became true.
    # (Threshold=105; ramp closes pass 105 around bar 4 → fill on bar 5+.)
    assert buys[0].fill_price >= 105.0


def test_indicator_entry_fires_when_condition_authored_at_different_interval() -> None:
    """Regression: 0-trade bug reported on real $SPY runs.

    User authored an entry strategy in the GUI; the scanner.Condition
    default ``interval="5m"`` was preserved on save. They then ran
    the strategy tester at the default ``"1d"`` interval. Without
    interval normalization, the cross-interval gate in
    scanner.engine.evaluate_condition silently returns ``None`` (no
    BarsRegistry is wired in the headless path) -> zero fires across
    the entire universe.

    The fix normalizes all per-Condition / per-FieldRef intervals in
    the strategy's condition tree to the test's outer interval at
    evaluate_symbol time. This test exercises that path: the
    condition is created with interval="5m" but the test runs at
    interval="1d", and the strategy must still produce a fill.
    """
    # Condition's interval is "5m" but we'll evaluate at "1d".
    condition = _close_gt_threshold(105.0, interval="5m")
    entry = _indicator_long_strategy(condition)
    exit_strat = _stop_5pct_exit()
    exit_strat.eod_kill_switch = False
    candles = _ramp_candles(n=30)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="1d",   # different from the condition's authored interval
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert len(buys) == 1, (
        f"expected exactly 1 BUY fill after interval normalization, "
        f"got {len(buys)} -- this is the 0-trade regression"
    )


def test_indicator_entry_no_fire_when_condition_never_true() -> None:
    """Threshold above any close → no fills, no errors."""
    condition = _close_gt_threshold(99999.0)
    entry = _indicator_long_strategy(condition)
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
    buys = [f for f in result.fills if f.side.value == "buy"]
    assert buys == []


def test_indicator_entry_with_none_condition_silently_no_fire() -> None:
    """Trigger with kind=INDICATOR but condition=None should not error and not fire."""
    entry = _indicator_long_strategy(condition=None)  # type: ignore[arg-type]
    exit_strat = _stop_5pct_exit()
    candles = _ramp_candles(n=10)

    result = evaluate_symbol(
        symbol="TEST",
        candles=candles,
        interval="5m",
        entry_strategy=entry,
        exit_strategy=exit_strat,
        starting_cash=100_000.0,
        cost_model=CostModel(),
    )
    # No fires, no fills.
    assert [f for f in result.fills if f.side.value == "buy"] == []


def test_indicator_exit_fires_when_condition_true() -> None:
    """Open a position via MARKET entry, then exit via INDICATOR
    ``close > 110`` — ramp from 100 stepping +1 will trigger that around bar 10.
    """
    from tradinglab.exits.model import ExitLeg, ExitStrategy, ExitTrigger
    from tradinglab.exits.model import TriggerKind as ExitTriggerKind  # noqa: F811

    entry = _market_long_strategy()
    entry.max_fires_per_session_per_symbol = 1
    condition = _close_gt_threshold(110.0)
    exit_strat = ExitStrategy(
        id="exit-ind",
        name="Indicator Exit",
        legs=[
            ExitLeg(
                id="leg-ind",
                triggers=[
                    ExitTrigger(
                        kind=ExitTriggerKind.INDICATOR,
                        condition=condition,  # type: ignore[arg-type]
                        interval="5m",
                        qty_pct=100.0,
                    ),
                ],
            ),
        ],
        eod_kill_switch=False,
    )
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
    buys = [f for f in result.fills if f.side.value == "buy"]
    sells = [f for f in result.fills if f.side.value == "sell"]
    assert len(buys) == 1
    assert len(sells) == 1, f"expected exactly 1 SELL fill from INDICATOR exit, got {len(sells)}"
