"""Engine tick phase 2.5 — corporate-action processing.

Covers the three production-realistic flows that the new phase has to
handle correctly to keep the locked decisions intact:

1. cash dividend on a long position credits ``Portfolio.cash`` at the
   ex-date tick and emits a ``CashAdjustment``.
2. stock split rescales ``Position.quantity`` AND ``Position.avg_cost``
   such that cost basis ``quantity * avg_cost`` is preserved, and emits
   a ``QuantityAdjustment``.
3. ``register_corporate_actions`` is idempotent on identical re-register
   and rejects different content for a symbol already registered.
"""
from __future__ import annotations

import numpy as np
import pytest

from tradinglab.backtest.actions import CorporateAction
from tradinglab.backtest.bars import BarSeries
from tradinglab.backtest.engine import SandboxEngine
from tradinglab.backtest.orders import Order, Side
from tradinglab.backtest.session import SessionSpec


def _flat_bars(symbol: str, ts: list[int], price: float = 100.0) -> BarSeries:
    n = len(ts)
    return BarSeries(
        symbol=symbol,
        timeframe="1d",
        ts=np.asarray(ts, dtype=np.int64),
        open=np.full(n, price, dtype=np.float64),
        high=np.full(n, price, dtype=np.float64),
        low=np.full(n, price, dtype=np.float64),
        close=np.full(n, price, dtype=np.float64),
        volume=np.full(n, 1000.0, dtype=np.float64),
    )


def _spec(symbol: str) -> SessionSpec:
    return SessionSpec(
        deck_seed=1,
        tickers=(symbol,),
        start_clock_iso="2024-01-01T00:00:00+00:00",
        slippage_bps=0.0,
        commission=0.0,
        setup_tags=(),
        starting_cash=100_000.0,
    )


def _new_engine(symbol: str, ts: list[int]) -> SandboxEngine:
    bars = _flat_bars(symbol, ts)
    return SandboxEngine(spec=_spec(symbol), bars_by_symbol={symbol: bars})


def _buy_and_settle(engine: SandboxEngine, symbol: str, qty: float) -> None:
    """Submit a market buy and tick twice: once to queue, once to fill."""
    engine.tick()  # ts[0]
    engine.pending_orders.append(Order(
        order_id="o1",
        symbol=symbol,
        side=Side.BUY,
        quantity=qty,
        submitted_ts=engine.clock.now_ts,
    ))
    engine.tick()  # ts[1] — fill at open


# --------------------------------------------------------------------- cash div


def test_cash_dividend_credits_cash_and_emits_record():
    sym = "AAA"
    timeline = [1, 2, 3, 4]
    eng = _new_engine(sym, timeline)
    _buy_and_settle(eng, sym, qty=100.0)
    cash_before_div = eng.portfolio.cash

    eng.register_corporate_actions(sym, [CorporateAction(
        ts=3, kind="cash_dividend",
        amount=0.50, ratio_num=1, ratio_den=1, source_ref="test",
    )])
    eng.tick()  # ts=3: ex-date

    assert eng.portfolio.cash == pytest.approx(cash_before_div + 0.50 * 100.0)
    assert len(eng.cash_adjustments) == 1
    adj = eng.cash_adjustments[0]
    assert adj.symbol == sym and adj.ts == 3
    assert adj.amount_per_share == pytest.approx(0.50)
    assert adj.quantity == pytest.approx(100.0)
    assert adj.reason == "cash_dividend"


def test_cash_dividend_skipped_when_flat():
    sym = "BBB"
    eng = _new_engine(sym, [1, 2, 3])
    eng.register_corporate_actions(sym, [CorporateAction(
        ts=2, kind="cash_dividend",
        amount=1.0, ratio_num=1, ratio_den=1, source_ref="t",
    )])
    cash0 = eng.portfolio.cash
    eng.tick(); eng.tick()  # spans ts=2

    assert eng.portfolio.cash == cash0
    assert eng.cash_adjustments == []


# --------------------------------------------------------------------- split


def test_stock_split_rescales_quantity_and_preserves_cost_basis():
    sym = "CCC"
    eng = _new_engine(sym, [1, 2, 3, 4])
    _buy_and_settle(eng, sym, qty=100.0)
    pos = eng.portfolio.positions[sym]
    pre_qty = pos.quantity
    pre_cost = pos.quantity * pos.avg_cost

    eng.register_corporate_actions(sym, [CorporateAction(
        ts=3, kind="stock_split",
        amount=0.0, ratio_num=2, ratio_den=1, source_ref="t",
    )])
    eng.tick()  # ex-split

    assert pos.quantity == pytest.approx(pre_qty * 2.0)
    assert pos.quantity * pos.avg_cost == pytest.approx(pre_cost)
    assert len(eng.quantity_adjustments) == 1
    qadj = eng.quantity_adjustments[0]
    assert qadj.ratio_num == 2 and qadj.ratio_den == 1
    assert qadj.pre_quantity == pytest.approx(pre_qty)
    assert qadj.reason == "stock_split"


def test_reverse_split_rescales_correctly():
    sym = "DDD"
    eng = _new_engine(sym, [1, 2, 3, 4])
    _buy_and_settle(eng, sym, qty=100.0)
    pos = eng.portfolio.positions[sym]
    pre_qty = pos.quantity
    pre_cost = pos.quantity * pos.avg_cost

    eng.register_corporate_actions(sym, [CorporateAction(
        ts=3, kind="stock_split",
        amount=0.0, ratio_num=1, ratio_den=5, source_ref="t",
    )])
    eng.tick()

    assert pos.quantity == pytest.approx(pre_qty / 5.0)
    assert pos.quantity * pos.avg_cost == pytest.approx(pre_cost)


# ----------------------------------------------------------------- idempotency


def test_register_corporate_actions_idempotent_on_identical_relist():
    sym = "EEE"
    eng = _new_engine(sym, [1, 2, 3])
    actions = [CorporateAction(
        ts=2, kind="cash_dividend",
        amount=0.25, ratio_num=1, ratio_den=1, source_ref="t",
    )]
    assert eng.register_corporate_actions(sym, actions) == 1
    assert eng.register_corporate_actions(sym, list(actions)) == 0


def test_register_corporate_actions_rejects_different_content():
    sym = "FFF"
    eng = _new_engine(sym, [1, 2, 3])
    eng.register_corporate_actions(sym, [CorporateAction(
        ts=2, kind="cash_dividend",
        amount=0.25, ratio_num=1, ratio_den=1, source_ref="t",
    )])
    with pytest.raises(ValueError):
        eng.register_corporate_actions(sym, [CorporateAction(
            ts=2, kind="cash_dividend",
            amount=0.50, ratio_num=1, ratio_den=1, source_ref="t",
        )])


# -------------------------------------------------------------- result export


def test_result_includes_adjustments():
    sym = "GGG"
    eng = _new_engine(sym, [1, 2, 3, 4])
    _buy_and_settle(eng, sym, qty=50.0)
    eng.register_corporate_actions(sym, [CorporateAction(
        ts=3, kind="cash_dividend",
        amount=0.10, ratio_num=1, ratio_den=1, source_ref="t",
    )])
    eng.tick()
    res = eng.result()
    assert len(res.cash_adjustments) == 1
    assert res.quantity_adjustments == []
