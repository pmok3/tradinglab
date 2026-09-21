"""Money-math invariants for the sandbox backtest engine.

Pins the deterministic cash / position / fill arithmetic across
``backtest.fills``, ``backtest.orders``, ``backtest.portfolio``,
``backtest.engine``, ``backtest.clock`` and ``backtest.bars``, plus the
strategy-tester sizing helper
``strategy_tester.evaluator._compute_quantity`` (with sizing rules from
``entries.model``).

All fixtures are synthetic and deterministic -- no network, no GUI, no
wall-clock dependence. Expectations are derived from the engine code
under test, not from memory: BUY pays ``open + slippage`` (worse-fill
direction), per-fill commission is ``flat + per_share * |qty|``, and
fills always take the full order quantity (never partial).

Tests only -- production code is untouched. Where a test pins
documented Phase-1 behaviour (market-only orders, no cash guard), it
says so explicitly rather than asserting an accident.
"""

from __future__ import annotations

import calendar
import dataclasses
from datetime import datetime

import numpy as np
import pytest

from tradinglab.backtest.bars import BarSeries, from_candles
from tradinglab.backtest.clock import Clock
from tradinglab.backtest.engine import SandboxEngine
from tradinglab.backtest.fills import apply_fills
from tradinglab.backtest.orders import Fill, Order, Side
from tradinglab.backtest.portfolio import Portfolio
from tradinglab.backtest.session import SessionSpec
from tradinglab.core.timezones import ET
from tradinglab.entries.model import (
    EntryStrategy,
    ShareRounding,
    SizingKind,
    SizingRule,
)
from tradinglab.models import Candle
from tradinglab.strategy_tester.evaluator import _compute_quantity

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

START_TS = 1_704_067_200  # 2024-01-02 00:00:00 UTC
DAY = 86_400


def _bars(
    symbol: str,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    start_ts: int = START_TS,
    step: int = DAY,
    ts: list[int] | None = None,
) -> BarSeries:
    """Build a BarSeries directly (no candle cache involved)."""
    n = len(opens)
    if ts is None:
        ts_arr = (np.arange(n, dtype=np.int64) * step + start_ts).astype(np.int64)
    else:
        ts_arr = np.asarray(ts, dtype=np.int64)
    return BarSeries(
        symbol=symbol,
        timeframe="1d",
        ts=ts_arr,
        open=np.asarray(opens, dtype=np.float64),
        high=np.asarray(highs, dtype=np.float64),
        low=np.asarray(lows, dtype=np.float64),
        close=np.asarray(closes, dtype=np.float64),
        volume=np.full(n, 1_000.0, dtype=np.float64),
    )


def _flat_bars(
    symbol: str, n: int, price: float, *, start_ts: int = START_TS, step: int = DAY
) -> BarSeries:
    px = [float(price)] * n
    return _bars(symbol, px, px, px, px, start_ts=start_ts, step=step)


def _spec(**kw) -> SessionSpec:
    base = dict(
        deck_seed=7,
        tickers=("A",),
        start_clock_iso="2024-01-02",
        slippage_bps=0.0,
        commission=0.0,
    )
    base.update(kw)
    return SessionSpec(**base)


def _engine(bars_by_symbol: dict[str, BarSeries], **spec_kw) -> SandboxEngine:
    return SandboxEngine(spec=_spec(**spec_kw), bars_by_symbol=dict(bars_by_symbol))


def _order(
    order_id: str,
    symbol: str = "A",
    side: Side = Side.BUY,
    quantity: float = 10.0,
    submitted_ts: int = 0,
) -> Order:
    return Order(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        submitted_ts=submitted_ts,
    )


def _fill(
    order_id: str = "f1",
    symbol: str = "A",
    side: Side = Side.BUY,
    quantity: float = 10.0,
    price: float = 100.0,
    ts: int = 1,
    commission: float = 0.0,
) -> Fill:
    return Fill(
        order_id=order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        fill_price=price,
        fill_ts=ts,
        slippage_bps=0.0,
        commission=commission,
    )


def _sizing_strategy(
    kind: SizingKind,
    *,
    qty: float | None = None,
    notional: float | None = None,
    rounding: ShareRounding = ShareRounding.DOWN,
) -> EntryStrategy:
    return EntryStrategy(
        sizing=SizingRule(kind=kind, qty=qty, notional=notional, share_rounding=rounding)
    )


# ---------------------------------------------------------------------------
# Fill logic (apply_fills)
# ---------------------------------------------------------------------------


def test_buy_fill_pays_open_plus_slippage() -> None:
    fills = apply_fills(
        [_order("o1", quantity=10.0)],
        {"A": 100.0},
        next_bar_ts=123,
        slippage_bps=10.0,
        commission=1.0,
        commission_per_share=0.01,
    )
    assert len(fills) == 1
    f = fills[0]
    # 10 bps worse-fill direction for BUY: open * (1 + 10/10000)
    assert f.fill_price == pytest.approx(100.1)
    assert f.fill_ts == 123
    assert f.commission == pytest.approx(1.0 + 0.01 * 10.0)


def test_sell_fill_receives_open_minus_slippage() -> None:
    fills = apply_fills(
        [_order("o1", side=Side.SELL, quantity=10.0)],
        {"A": 100.0},
        next_bar_ts=123,
        slippage_bps=10.0,
        commission=0.0,
    )
    assert len(fills) == 1
    assert fills[0].fill_price == pytest.approx(99.9)


def test_zero_slippage_fills_exactly_at_open() -> None:
    fills = apply_fills(
        [_order("o1")],
        {"A": 123.45},
        next_bar_ts=7,
        slippage_bps=0.0,
        commission=0.0,
    )
    assert fills[0].fill_price == 123.45


def test_commission_is_flat_plus_per_share() -> None:
    fills = apply_fills(
        [_order("o1", quantity=150.0)],
        {"A": 50.0},
        next_bar_ts=1,
        slippage_bps=0.0,
        commission=2.5,
        commission_per_share=0.01,
    )
    # flat 2.50 + 0.01 * 150 shares
    assert fills[0].commission == pytest.approx(4.0)


def test_order_for_absent_symbol_is_skipped() -> None:
    fills = apply_fills(
        [_order("o1", symbol="B")],
        {"A": 100.0},
        next_bar_ts=1,
        slippage_bps=0.0,
        commission=0.0,
    )
    assert fills == []


def test_apply_fills_is_deterministic_and_does_not_mutate_inputs() -> None:
    orders = [_order("o1", quantity=10.0), _order("o2", side=Side.SELL, quantity=5.0)]
    opens = {"A": 100.0}
    before = list(orders)
    first = apply_fills(orders, opens, 9, 10.0, 1.0, commission_per_share=0.01)
    second = apply_fills(orders, opens, 9, 10.0, 1.0, commission_per_share=0.01)
    assert first == second  # byte-identical output for identical inputs
    assert orders == before  # caller's order list untouched
    assert [o.order_id for o in orders] == ["o1", "o2"]


def test_fills_always_take_full_quantity_never_partial() -> None:
    fills = apply_fills(
        [_order("o1", quantity=37.5)],
        {"A": 100.0},
        next_bar_ts=1,
        slippage_bps=0.0,
        commission=0.0,
    )
    assert fills[0].quantity == 37.5


def test_limit_orders_unsupported_by_design() -> None:
    """Phase-1 decision (fills.spec.md): the engine is market-only.

    ``Order`` carries no limit price -- a limit order cannot even be
    expressed, so ``apply_fills`` has no limit branch to test.
    """
    field_names = {f.name for f in dataclasses.fields(Order)}
    assert "limit_price" not in field_names


def test_stop_orders_unsupported_by_design() -> None:
    """Phase-1 decision (fills.spec.md): stops are deferred to Phase 2."""
    field_names = {f.name for f in dataclasses.fields(Order)}
    assert "stop_price" not in field_names


# ---------------------------------------------------------------------------
# Engine fill timing
# ---------------------------------------------------------------------------


def test_fill_lands_at_next_bar_open_not_signal_bar_close() -> None:
    bars = _bars(
        "A",
        opens=[100.0, 110.0, 113.0],
        highs=[101.0, 111.0, 114.0],
        lows=[99.0, 109.0, 112.0],
        closes=[95.0, 112.0, 114.0],
    )
    eng = _engine({"A": bars})
    assert eng.tick()  # land on bar 0 (the "signal" bar)
    eng.submit_order(_order("o1", quantity=10.0, submitted_ts=START_TS))
    assert eng.tick()  # bar 1: the pending order fills here
    assert len(eng.fills) == 1
    f = eng.fills[0]
    assert f.fill_price == 110.0  # bar 1 open ...
    assert f.fill_price != 95.0  # ... not the signal bar's close
    assert f.fill_ts == START_TS + DAY


def test_order_submitted_before_first_tick_fills_at_first_open() -> None:
    eng = _engine({"A": _flat_bars("A", 3, 100.0)})
    eng.submit_order(_order("o1", quantity=10.0, submitted_ts=0))
    assert eng.tick()
    assert len(eng.fills) == 1
    assert eng.fills[0].fill_price == 100.0
    assert eng.fills[0].fill_ts == START_TS
    assert eng.pending_orders == []


def test_order_submitted_after_final_tick_never_fills_and_stays_pending() -> None:
    eng = _engine({"A": _flat_bars("A", 1, 100.0)})
    assert eng.tick()
    eng.submit_order(_order("o1", quantity=10.0, submitted_ts=START_TS))
    assert eng.tick() is False  # clock exhausted
    assert eng.fills == []
    assert [o.order_id for o in eng.pending_orders] == ["o1"]


def test_pending_queue_drains_in_full_on_one_tick() -> None:
    eng = _engine({"A": _flat_bars("A", 2, 100.0), "B": _flat_bars("B", 2, 50.0)})
    eng.submit_order(_order("o1", symbol="A", quantity=1.0))
    eng.submit_order(_order("o2", symbol="B", quantity=2.0))
    eng.submit_order(_order("o3", symbol="A", quantity=3.0))
    assert eng.tick()
    assert len(eng.fills) == 3
    assert [f.quantity for f in eng.fills] == [1.0, 2.0, 3.0]
    assert eng.pending_orders == []


def test_missing_bar_symbol_requeues_then_fills_on_later_bar() -> None:
    t0, t1, t2 = START_TS, START_TS + DAY, START_TS + 2 * DAY
    bars_a = _bars("A", [100.0, 101.0, 102.0], [101.0, 102.0, 103.0],
                   [99.0, 100.0, 101.0], [100.5, 101.5, 102.5])
    # B has no bar at t1 (the "missing pre-market data" case).
    bars_b = _bars("B", [50.0, 52.0], [51.0, 53.0],
                   [49.0, 51.0], [50.5, 52.5], ts=[t0, t2])
    eng = _engine({"A": bars_a, "B": bars_b})
    assert eng.tick()  # t0
    eng.submit_order(_order("o1", symbol="B", quantity=5.0, submitted_ts=t0))
    assert eng.tick()  # t1: B has no bar -> order silently re-queued
    assert eng.fills == []
    assert [o.order_id for o in eng.pending_orders] == ["o1"]
    assert eng.tick()  # t2: B is back -> fills at B's t2 open
    assert len(eng.fills) == 1
    assert eng.fills[0].fill_ts == t2
    assert eng.fills[0].fill_price == 52.0
    assert eng.pending_orders == []
    assert len(eng.portfolio.equity_curve) == 3


# ---------------------------------------------------------------------------
# Portfolio accounting
# ---------------------------------------------------------------------------


def test_buy_reduces_cash_by_notional_plus_commission() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(quantity=10.0, price=100.0, commission=1.0))
    assert pf.cash == 100_000.0 - 10.0 * 100.0 - 1.0
    pos = pf.positions["A"]
    assert pos.quantity == 10.0
    assert pos.avg_cost == 100.0


def test_sell_increases_cash_by_notional_minus_commission() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(side=Side.BUY, quantity=10.0, price=100.0, commission=1.0))
    pf.apply_fill(_fill("f2", side=Side.SELL, quantity=10.0, price=110.0, commission=1.0))
    assert pf.cash == 100_000.0 - 1_000.0 - 1.0 + 1_100.0 - 1.0
    assert pf.positions["A"].quantity == 0.0


def test_add_to_long_uses_weighted_average_cost() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(quantity=10.0, price=100.0))
    pf.apply_fill(_fill("f2", quantity=10.0, price=120.0))
    pos = pf.positions["A"]
    assert pos.quantity == 20.0
    assert pos.avg_cost == pytest.approx(110.0)


def test_partial_reduce_realizes_pnl_and_keeps_avg_cost() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(quantity=10.0, price=100.0))
    pf.apply_fill(_fill("f2", side=Side.SELL, quantity=4.0, price=120.0))
    pos = pf.positions["A"]
    assert pos.quantity == 6.0
    assert pos.avg_cost == 100.0  # reducing never reprices the remainder
    assert pos.realized_pnl == pytest.approx((120.0 - 100.0) * 4.0)


def test_flip_through_zero_splits_close_and_new_open() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(quantity=10.0, price=100.0))
    pf.apply_fill(_fill("f2", side=Side.SELL, quantity=15.0, price=110.0))
    pos = pf.positions["A"]
    assert pos.quantity == -5.0
    # Close leg realises (110-100)*10; the new short leg opens at 110.
    assert pos.realized_pnl == pytest.approx(100.0)
    assert pos.avg_cost == 110.0


def test_close_to_zero_resets_avg_cost() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(quantity=10.0, price=100.0))
    pf.apply_fill(_fill("f2", side=Side.SELL, quantity=10.0, price=120.0))
    pos = pf.positions["A"]
    assert pos.quantity == 0.0
    assert pos.avg_cost == 0.0
    assert pos.realized_pnl == pytest.approx(200.0)


def test_short_cover_realizes_pnl() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(side=Side.SELL, quantity=10.0, price=100.0))
    assert pf.positions["A"].quantity == -10.0
    assert pf.cash == 100_000.0 + 1_000.0
    pf.apply_fill(_fill("f2", side=Side.BUY, quantity=5.0, price=90.0))
    pos = pf.positions["A"]
    assert pos.quantity == -5.0
    assert pos.avg_cost == 100.0
    assert pos.realized_pnl == pytest.approx((100.0 - 90.0) * 5.0)


def test_equity_equals_cash_plus_position_value_at_mtm() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(quantity=10.0, price=100.0))
    equity = pf.mark_to_market(5, {"A": 105.0})
    assert equity == pytest.approx(99_000.0 + 10.0 * 105.0)
    assert pf.equity_curve == [(5, pytest.approx(100_050.0))]


def test_mtm_falls_back_to_avg_cost_when_price_missing() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(quantity=10.0, price=100.0))
    equity = pf.mark_to_market(5, {})
    assert equity == pytest.approx(99_000.0 + 10.0 * 100.0)


def test_negative_cash_is_allowed_with_no_guard() -> None:
    """Pinned: the portfolio imposes no cash constraint -- buying more
    than the account holds just drives cash negative."""
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(quantity=2_000.0, price=100.0))
    assert pf.cash == -100_000.0
    assert pf.positions["A"].quantity == 2_000.0


def test_zero_quantity_fill_deducts_flat_commission_only() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(quantity=0.0, price=50.0, commission=2.5))
    assert pf.cash == 100_000.0 - 2.5
    pos = pf.positions["A"]
    assert pos.quantity == 0.0
    assert pos.avg_cost == 0.0


def test_add_to_short_uses_weighted_average_cost() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(side=Side.SELL, quantity=10.0, price=100.0))
    pf.apply_fill(_fill("f2", side=Side.SELL, quantity=5.0, price=110.0))
    pos = pf.positions["A"]
    assert pos.quantity == -15.0
    assert pos.avg_cost == pytest.approx((100.0 * 10.0 + 110.0 * 5.0) / 15.0)


# ---------------------------------------------------------------------------
# Fees and rounding
# ---------------------------------------------------------------------------


def test_engine_applies_flat_commission_per_fill() -> None:
    eng = _engine({"A": _flat_bars("A", 2, 100.0)}, commission=2.5)
    eng.submit_order(_order("o1", quantity=100.0))
    assert eng.tick()
    assert eng.fills[0].commission == 2.5
    assert eng.portfolio.cash == 100_000.0 - 100.0 * 100.0 - 2.5


def test_engine_applies_per_share_commission_to_cash() -> None:
    eng = _engine({"A": _flat_bars("A", 2, 100.0)}, commission_per_share=0.005)
    eng.submit_order(_order("o1", quantity=100.0))
    assert eng.tick()
    assert eng.fills[0].commission == pytest.approx(0.5)
    assert eng.portfolio.cash == pytest.approx(100_000.0 - 10_000.0 - 0.5)


def test_cash_math_uses_raw_float64_without_cent_rounding() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(quantity=7.0, price=33.333))
    # 7 * 33.333 is not cent-clean, so any rounding to cents would move cash.
    assert 7.0 * 33.333 != round(7.0 * 33.333, 2)
    assert pf.cash == 100_000.0 - 7.0 * 33.333


def test_half_cent_fill_price_is_not_tick_rounded() -> None:
    fills = apply_fills(
        [_order("o1")],
        {"A": 100.005},
        next_bar_ts=1,
        slippage_bps=0.0,
        commission=0.0,
    )
    assert fills[0].fill_price == 100.005


def test_tiny_fractional_quantities_flow_through() -> None:
    pf = Portfolio(cash=100_000.0)
    pf.apply_fill(_fill(quantity=0.001, price=100.0))
    pos = pf.positions["A"]
    assert pos.quantity == 0.001
    assert pos.avg_cost == 100.0
    assert pf.cash == 100_000.0 - 0.001 * 100.0


def test_compute_quantity_fixed_notional_rounds_down() -> None:
    strat = _sizing_strategy(SizingKind.FIXED_NOTIONAL, notional=1_000.0)
    # 1000 / 33.333 = 30.0003... -> DOWN truncates to 30 shares.
    assert _compute_quantity(
        strategy=strat, decision_price=33.333, starting_cash=100_000.0
    ) == 30.0


def test_compute_quantity_nearest_uses_bankers_rounding() -> None:
    strat = _sizing_strategy(
        SizingKind.FIXED_NOTIONAL, notional=250.0, rounding=ShareRounding.NEAREST
    )
    # 250 / 100 = 2.5 -> Python round() is banker's: 2.5 -> 2, not 3.
    assert _compute_quantity(
        strategy=strat, decision_price=100.0, starting_cash=100_000.0
    ) == 2.0


def test_compute_quantity_caps_notional_at_starting_cash_and_rejects_degenerate_inputs() -> None:
    capped = _sizing_strategy(SizingKind.FIXED_NOTIONAL, notional=1_000_000.0)
    assert _compute_quantity(
        strategy=capped, decision_price=100.0, starting_cash=10_000.0
    ) == 100.0

    # Degenerate inputs all mean "skip this fire".
    zero_notional = _sizing_strategy(SizingKind.FIXED_NOTIONAL, notional=0.0)
    assert _compute_quantity(
        strategy=zero_notional, decision_price=100.0, starting_cash=100_000.0
    ) == 0.0
    assert _compute_quantity(
        strategy=capped, decision_price=0.0, starting_cash=100_000.0
    ) == 0.0
    assert _compute_quantity(
        strategy=capped, decision_price=-5.0, starting_cash=100_000.0
    ) == 0.0
    zero_qty = _sizing_strategy(SizingKind.FIXED_QTY, qty=0.0)
    assert _compute_quantity(
        strategy=zero_qty, decision_price=100.0, starting_cash=100_000.0
    ) == 0.0
    none_qty = _sizing_strategy(SizingKind.FIXED_QTY, qty=None)
    assert _compute_quantity(
        strategy=none_qty, decision_price=100.0, starting_cash=100_000.0
    ) == 0.0


def test_compute_quantity_fractional_fixed_qty_share_rounding() -> None:
    down = _sizing_strategy(SizingKind.FIXED_QTY, qty=7.5, rounding=ShareRounding.DOWN)
    nearest = _sizing_strategy(
        SizingKind.FIXED_QTY, qty=7.5, rounding=ShareRounding.NEAREST
    )
    assert _compute_quantity(
        strategy=down, decision_price=100.0, starting_cash=100_000.0
    ) == 7.0
    # round(7.5) is banker's too: 7.5 -> 8 (8 is even).
    assert _compute_quantity(
        strategy=nearest, decision_price=100.0, starting_cash=100_000.0
    ) == 8.0


# ---------------------------------------------------------------------------
# Clock and time handling
# ---------------------------------------------------------------------------


def test_five_day_hold_keeps_equity_invariant_with_empty_corporate_actions() -> None:
    eng = _engine({"A": _flat_bars("A", 5, 100.0)})
    eng.submit_order(_order("o1", quantity=10.0, submitted_ts=0))
    result = eng.run_to_completion()
    assert len(result.equity_curve) == 5
    # Flat price, no fees: equity is pinned at starting cash every day.
    for _ts, equity in result.equity_curve:
        assert equity == 100_000.0
    # Corporate-action queues were never registered: both lists are empty.
    assert result.cash_adjustments == []
    assert result.quantity_adjustments == []
    # Flattening at the same price closes the round trip with zero P&L.
    fills = eng.flatten_all_at_close(START_TS + 4 * DAY, {"A": 100.0})
    assert len(fills) == 1
    assert eng.portfolio.cash == 100_000.0
    assert len(eng.post_trades) == 1
    assert eng.post_trades[0].pnl == 0.0


def test_dst_day_boundary_ticks_as_consecutive_days() -> None:
    if ET is None:
        pytest.skip("zoneinfo America/New_York unavailable")
    # 2024-03-10 is the US spring-forward day: ET midnights are 23h apart.
    candles = [
        Candle(
            date=datetime(2024, 3, 10, 0, 0, tzinfo=ET),
            open=100.0, high=101.0, low=99.0, close=100.0, volume=1_000,
        ),
        Candle(
            date=datetime(2024, 3, 11, 0, 0, tzinfo=ET),
            open=100.0, high=101.0, low=99.0, close=100.0, volume=1_000,
        ),
    ]
    bars = from_candles("DST", "1d", candles)
    assert bars.ts[1] - bars.ts[0] == 23 * 3_600
    eng = _engine({"DST": bars}, tickers=("DST",))
    eng.submit_order(_order("o1", symbol="DST", quantity=10.0, submitted_ts=0))
    result = eng.run_to_completion()
    assert len(result.equity_curve) == 2  # both days tick, consecutively
    assert eng.fills[0].fill_ts == int(bars.ts[0])


def test_from_candles_treats_naive_datetimes_as_utc() -> None:
    dt = datetime(2024, 1, 2, 9, 30)  # naive -> interpreted as UTC
    bars = from_candles(
        "NAIVE",
        "1d",
        [Candle(date=dt, open=1.0, high=1.0, low=1.0, close=1.0, volume=1)],
    )
    assert bars.ts[0] == calendar.timegm(dt.timetuple())


def test_from_candles_converts_tz_aware_to_utc_epoch() -> None:
    if ET is None:
        pytest.skip("zoneinfo America/New_York unavailable")
    dt = datetime(2024, 1, 2, 9, 30, tzinfo=ET)  # 14:30 UTC (EST = UTC-5)
    bars = from_candles(
        "AWARE",
        "1d",
        [Candle(date=dt, open=1.0, high=1.0, low=1.0, close=1.0, volume=1)],
    )
    assert bars.ts[0] == int(dt.timestamp())
    assert bars.ts[0] == calendar.timegm((2024, 1, 2, 14, 30, 0, 0, 0, 0))


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_clock_timeline_never_ticks() -> None:
    clock = Clock(timeline=np.empty(0, dtype=np.int64))
    assert clock.is_exhausted
    assert clock.tick() is False
    assert clock.now_ts == -1


def test_engine_with_no_symbols_returns_empty_result() -> None:
    eng = _engine({}, tickers=())
    result = eng.run_to_completion()
    assert result.fills == []
    assert result.equity_curve == []
    assert result.post_trades == []
    assert result.final_cash == 100_000.0


def test_empty_barseries_has_no_tradable_price() -> None:
    empty = BarSeries(
        symbol="A",
        timeframe="1d",
        ts=np.empty(0, dtype=np.int64),
        open=np.empty(0, dtype=np.float64),
        high=np.empty(0, dtype=np.float64),
        low=np.empty(0, dtype=np.float64),
        close=np.empty(0, dtype=np.float64),
        volume=np.empty(0, dtype=np.float64),
    )
    assert len(empty) == 0
    assert empty.index_for_ts(START_TS) is None
    eng = _engine({"A": empty})
    eng.submit_order(_order("o1", quantity=10.0))
    result = eng.run_to_completion()
    assert result.fills == []
    assert result.equity_curve == []


def test_single_bar_series_fills_then_exhausts() -> None:
    eng = _engine({"A": _flat_bars("A", 1, 100.0)})
    eng.submit_order(_order("o1", quantity=10.0))
    assert eng.tick() is True
    assert len(eng.fills) == 1
    assert eng.tick() is False


def test_flatten_all_at_close_settles_cash_with_no_fees_and_emits_post_trade() -> None:
    eng = _engine({"A": _flat_bars("A", 3, 100.0)})
    eng.submit_order(_order("o1", quantity=10.0, submitted_ts=0))
    assert eng.tick()  # fills at bar 0 open = 100
    # A pending order for an unregistered symbol is dropped, not filled.
    eng.submit_order(_order("o2", symbol="B", quantity=5.0, submitted_ts=START_TS))
    fills = eng.flatten_all_at_close(START_TS + 2 * DAY, {"A": 110.0})
    assert len(fills) == 1
    f = fills[0]
    assert f.side is Side.SELL
    assert f.fill_price == 110.0
    assert f.slippage_bps == 0.0
    assert f.commission == 0.0
    assert eng.pending_orders == []
    assert eng.portfolio.cash == 100_000.0 - 1_000.0 + 1_100.0
    assert eng.portfolio.positions["A"].quantity == 0.0
    assert len(eng.post_trades) == 1
    assert eng.post_trades[0].pnl == pytest.approx(100.0)
