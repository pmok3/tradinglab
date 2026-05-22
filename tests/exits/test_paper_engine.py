"""Tests for ``tradinglab.exits.paper_engine.PaperBrokerEngine``.

The Tk-thread guard is bypassed via ``tk_thread_check_disabled()`` so
these tests can drive the engine directly from the pytest main thread
without spinning up a Tk root.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import List, Tuple

import pytest

from tradinglab.core.thread_guard import (
    TkThreadViolation,
    tk_thread_check_disabled,
)
from tradinglab.exits.model import OrderSide
from tradinglab.exits.paper_engine import (
    Fill,
    PaperBrokerEngine,
    PaperOrder,
    PaperOrderKind,
)
from tradinglab.exits.spec import Bar
from tradinglab.positions.model import Position
from tradinglab.positions.tracker import PositionTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts() -> datetime:
    return datetime(2026, 5, 4, 14, 30, tzinfo=timezone.utc)


def _setup(
    *,
    side: str = "long",
    qty: float = 100.0,
    entry: float = 100.0,
    slippage_bps: float = 0.0,
) -> Tuple[PaperBrokerEngine, PositionTracker, Position]:
    tracker = PositionTracker()
    with tk_thread_check_disabled():
        pos = tracker.open(
            symbol="AAPL", side=side, qty=qty, price=entry,
            source="manual", ts=_ts(),
        )
    engine = PaperBrokerEngine(tracker, slippage_bps=slippage_bps)
    return engine, tracker, pos


def _bar(
    *,
    o: float, h: float, l: float, c: float, v: float = 0.0,
) -> Bar:
    return Bar(open=o, high=h, low=l, close=c, volume=v, date=_ts())


def _mk_order(
    pos_id: str,
    *,
    kind: PaperOrderKind = PaperOrderKind.MARKET,
    side: OrderSide = OrderSide.SELL,
    qty: float = 100.0,
    price: float | None = None,
    limit_price: float | None = None,
    label: str = "",
    oid: str | None = None,
) -> PaperOrder:
    return PaperOrder(
        id=oid if oid is not None else uuid.uuid4().hex,
        position_id=pos_id,
        kind=kind,
        side=side,
        qty=qty,
        price=price,
        limit_price=limit_price,
        label=label,
    )


# ---------------------------------------------------------------------------
# Submit / cancel basics
# ---------------------------------------------------------------------------


def test_submit_accepts_market_and_returns_order_id():
    engine, _t, pos = _setup()
    with tk_thread_check_disabled():
        oid = engine.submit(_mk_order(pos.id))
    assert isinstance(oid, str) and oid
    working = engine.working_orders()
    assert len(working) == 1 and working[0].id == oid


def test_cancel_existing_order_returns_true_and_removes_from_working():
    engine, _t, pos = _setup()
    with tk_thread_check_disabled():
        oid = engine.submit(_mk_order(pos.id))
        assert engine.cancel(oid) is True
    assert engine.working_orders() == []
    assert engine.stats()["cancelled"] == 1


def test_cancel_unknown_order_returns_false_and_does_not_count():
    engine, _t, _pos = _setup()
    with tk_thread_check_disabled():
        assert engine.cancel("not-a-real-id") is False
    assert engine.stats()["cancelled"] == 0


def test_cancel_all_for_position_drops_only_matching_orders():
    engine, tracker, pos_a = _setup()
    with tk_thread_check_disabled():
        pos_b = tracker.open(
            symbol="MSFT", side="long", qty=10, price=400,
            source="manual", ts=_ts(),
        )
        engine.submit(_mk_order(pos_a.id, kind=PaperOrderKind.LIMIT, price=110.0))
        engine.submit(_mk_order(pos_a.id, kind=PaperOrderKind.STOP, price=95.0))
        engine.submit(_mk_order(pos_b.id, kind=PaperOrderKind.LIMIT, price=410.0))
        n = engine.cancel_all_for_position(pos_a.id)
    assert n == 2
    remaining = engine.working_orders()
    assert len(remaining) == 1 and remaining[0].position_id == pos_b.id
    assert engine.stats()["cancelled"] == 2


# ---------------------------------------------------------------------------
# Reject conditions
# ---------------------------------------------------------------------------


def test_submit_rejects_unknown_position():
    engine, _t, _pos = _setup()
    bad = _mk_order("does-not-exist")
    with tk_thread_check_disabled():
        with pytest.raises(ValueError, match="unknown position"):
            engine.submit(bad)
    assert engine.stats()["rejected"] == 1
    assert engine.stats()["working"] == 0


def test_submit_rejects_non_positive_qty():
    engine, _t, pos = _setup()
    bad = _mk_order(pos.id, qty=0.0)
    with tk_thread_check_disabled():
        with pytest.raises(ValueError, match="qty"):
            engine.submit(bad)
    assert engine.stats()["rejected"] == 1


def test_submit_rejects_limit_without_price():
    engine, _t, pos = _setup()
    bad = _mk_order(pos.id, kind=PaperOrderKind.LIMIT, price=None)
    with tk_thread_check_disabled():
        with pytest.raises(ValueError, match="requires price"):
            engine.submit(bad)
    bad_stop = _mk_order(pos.id, kind=PaperOrderKind.STOP, price=None)
    with tk_thread_check_disabled():
        with pytest.raises(ValueError, match="requires price"):
            engine.submit(bad_stop)
    assert engine.stats()["rejected"] == 2


def test_submit_rejects_stop_limit_without_limit_price():
    engine, _t, pos = _setup()
    # stop_limit needs both price (stop) AND limit_price; missing limit_price
    # is the targeted reject here.
    bad = _mk_order(
        pos.id, kind=PaperOrderKind.STOP_LIMIT, price=95.0, limit_price=None,
    )
    with tk_thread_check_disabled():
        with pytest.raises(ValueError, match="limit_price"):
            engine.submit(bad)
    assert engine.stats()["rejected"] == 1


# ---------------------------------------------------------------------------
# MARKET fill semantics
# ---------------------------------------------------------------------------


def test_market_fill_with_zero_slippage_uses_bar_close():
    engine, tracker, pos = _setup(slippage_bps=0.0)
    with tk_thread_check_disabled():
        engine.submit(_mk_order(pos.id, kind=PaperOrderKind.MARKET))
        fills = engine.on_bar(pos.id, _bar(o=101, h=103, l=99, c=102), is_close=True)
    assert len(fills) == 1
    assert fills[0].price == pytest.approx(102.0)
    assert fills[0].reason == "market"
    assert tracker.get(pos.id).qty_open == 0
    assert engine.working_orders() == []
    assert engine.stats()["filled"] == 1


def test_market_fill_with_slippage_moves_against_trader():
    engine_sell, _ts, pos_sell = _setup(slippage_bps=10.0)  # 10 bps = 0.10%
    with tk_thread_check_disabled():
        engine_sell.submit(_mk_order(pos_sell.id, kind=PaperOrderKind.MARKET, side=OrderSide.SELL))
        fills = engine_sell.on_bar(pos_sell.id, _bar(o=100, h=101, l=99, c=100), is_close=True)
    # SELL exit: fill is reduced by 10 bps.
    assert fills[0].price == pytest.approx(100.0 * (1 - 10.0 / 10000.0))
    assert fills[0].price < 100.0

    engine_buy, _t2, pos_short = _setup(side="short", slippage_bps=10.0)
    with tk_thread_check_disabled():
        engine_buy.submit(_mk_order(pos_short.id, kind=PaperOrderKind.MARKET, side=OrderSide.BUY))
        bfills = engine_buy.on_bar(pos_short.id, _bar(o=100, h=101, l=99, c=100), is_close=True)
    assert bfills[0].price == pytest.approx(100.0 * (1 + 10.0 / 10000.0))
    assert bfills[0].price > 100.0


# ---------------------------------------------------------------------------
# LIMIT touched semantics
# ---------------------------------------------------------------------------


def test_limit_sell_touched_fills_at_limit_price():
    engine, tracker, pos = _setup(slippage_bps=50.0)  # slippage ignored on limits
    with tk_thread_check_disabled():
        engine.submit(_mk_order(
            pos.id, kind=PaperOrderKind.LIMIT, side=OrderSide.SELL,
            price=110.0, label="profit-target",
        ))
        # Bar high reaches 111 -> SELL limit at 110 is touched.
        fills = engine.on_bar(pos.id, _bar(o=105, h=111, l=104, c=109), is_close=True)
    assert len(fills) == 1
    assert fills[0].price == pytest.approx(110.0)
    assert fills[0].reason == "limit-touched-up"
    assert fills[0].label == "profit-target"
    assert tracker.get(pos.id).qty_open == 0


def test_limit_sell_not_touched_stays_working():
    engine, _t, pos = _setup()
    with tk_thread_check_disabled():
        oid = engine.submit(_mk_order(
            pos.id, kind=PaperOrderKind.LIMIT, side=OrderSide.SELL, price=110.0,
        ))
        fills = engine.on_bar(pos.id, _bar(o=105, h=109, l=104, c=108), is_close=True)
    assert fills == []
    working = engine.working_orders()
    assert len(working) == 1 and working[0].id == oid


def test_limit_buy_touched_fills_at_limit_price():
    engine, tracker, pos = _setup(side="short")
    with tk_thread_check_disabled():
        engine.submit(_mk_order(
            pos.id, kind=PaperOrderKind.LIMIT, side=OrderSide.BUY, price=90.0,
        ))
        # Bar low dips to 89 -> BUY limit at 90 is touched (favorable cover).
        fills = engine.on_bar(pos.id, _bar(o=95, h=96, l=89, c=92), is_close=True)
    assert len(fills) == 1
    assert fills[0].price == pytest.approx(90.0)
    assert fills[0].reason == "limit-touched-down"
    assert tracker.get(pos.id).qty_open == 0


def test_limit_buy_not_touched_stays_working():
    engine, _t, pos = _setup(side="short")
    with tk_thread_check_disabled():
        engine.submit(_mk_order(
            pos.id, kind=PaperOrderKind.LIMIT, side=OrderSide.BUY, price=90.0,
        ))
        fills = engine.on_bar(pos.id, _bar(o=95, h=96, l=91, c=92), is_close=True)
    assert fills == []
    assert len(engine.working_orders()) == 1


# ---------------------------------------------------------------------------
# STOP touched + gap-through semantics
# ---------------------------------------------------------------------------


def test_stop_sell_touched_fills_at_stop_price():
    engine, tracker, pos = _setup()
    with tk_thread_check_disabled():
        engine.submit(_mk_order(
            pos.id, kind=PaperOrderKind.STOP, side=OrderSide.SELL,
            price=95.0, label="hard-stop",
        ))
        # Open above stop, bar dips to 94 -> stop touched at 95.
        fills = engine.on_bar(pos.id, _bar(o=100, h=101, l=94, c=96), is_close=True)
    assert len(fills) == 1
    assert fills[0].price == pytest.approx(95.0)  # no slippage configured
    assert fills[0].reason == "stop-touched-down"
    assert fills[0].label == "hard-stop"
    assert tracker.get(pos.id).qty_open == 0


def test_stop_sell_gap_through_fills_at_open_with_slippage():
    engine, tracker, pos = _setup(slippage_bps=20.0)  # 0.20%
    with tk_thread_check_disabled():
        engine.submit(_mk_order(
            pos.id, kind=PaperOrderKind.STOP, side=OrderSide.SELL, price=95.0,
        ))
        # Bar gaps DOWN: opens at 90 (already past 95). Fill base = min(95, 90) = 90.
        fills = engine.on_bar(pos.id, _bar(o=90, h=91, l=89, c=89.5), is_close=True)
    assert len(fills) == 1
    expected = 90.0 * (1 - 20.0 / 10000.0)
    assert fills[0].price == pytest.approx(expected)
    assert fills[0].reason == "stop-touched-down"
    assert tracker.get(pos.id).qty_open == 0


def test_stop_buy_touched_fills_at_stop_price():
    engine, tracker, pos = _setup(side="short")
    with tk_thread_check_disabled():
        engine.submit(_mk_order(
            pos.id, kind=PaperOrderKind.STOP, side=OrderSide.BUY, price=105.0,
        ))
        # Bar rallies to 106 -> BUY stop at 105 is touched.
        fills = engine.on_bar(pos.id, _bar(o=100, h=106, l=99, c=104), is_close=True)
    assert len(fills) == 1
    assert fills[0].price == pytest.approx(105.0)
    assert fills[0].reason == "stop-touched-up"
    assert tracker.get(pos.id).qty_open == 0


def test_stop_buy_gap_through_fills_at_open_with_slippage():
    engine, tracker, pos = _setup(side="short", slippage_bps=25.0)
    with tk_thread_check_disabled():
        engine.submit(_mk_order(
            pos.id, kind=PaperOrderKind.STOP, side=OrderSide.BUY, price=105.0,
        ))
        # Bar gaps UP: opens at 110 (past 105). Fill base = max(105, 110) = 110.
        fills = engine.on_bar(pos.id, _bar(o=110, h=112, l=109, c=111), is_close=True)
    assert len(fills) == 1
    expected = 110.0 * (1 + 25.0 / 10000.0)
    assert fills[0].price == pytest.approx(expected)
    assert fills[0].reason == "stop-touched-up"


# ---------------------------------------------------------------------------
# STOP_LIMIT semantics
# ---------------------------------------------------------------------------


def test_stop_limit_normal_fill_uses_limit_price():
    engine, tracker, pos = _setup(slippage_bps=15.0)  # ignored on limit body
    with tk_thread_check_disabled():
        engine.submit(_mk_order(
            pos.id, kind=PaperOrderKind.STOP_LIMIT, side=OrderSide.SELL,
            price=95.0, limit_price=94.5,
        ))
        # Stop touched (low <= 95) and bar high (101) >= limit (94.5): fillable.
        fills = engine.on_bar(pos.id, _bar(o=98, h=101, l=93, c=95), is_close=True)
    assert len(fills) == 1
    assert fills[0].price == pytest.approx(94.5)
    assert fills[0].reason == "stop-limit-filled-down"
    assert tracker.get(pos.id).qty_open == 0


def test_stop_limit_gap_below_limit_stays_working():
    engine, tracker, pos = _setup()
    with tk_thread_check_disabled():
        oid = engine.submit(_mk_order(
            pos.id, kind=PaperOrderKind.STOP_LIMIT, side=OrderSide.SELL,
            price=180.0, limit_price=179.5,
        ))
        # Bar gaps THROUGH the limit: stop=180 touched (low=177), but
        # bar.high=178 < limit=179.5 -> we cannot fill at 179.5.
        fills = engine.on_bar(
            pos.id, _bar(o=178, h=178.5, l=177, c=177.5), is_close=True,
        )
    assert fills == []
    working = engine.working_orders()
    assert len(working) == 1 and working[0].id == oid
    assert tracker.get(pos.id).qty_open == 100  # unchanged


def test_stop_limit_missing_limit_price_rejects():
    engine, _t, pos = _setup()
    bad = _mk_order(
        pos.id, kind=PaperOrderKind.STOP_LIMIT, price=95.0, limit_price=None,
    )
    with tk_thread_check_disabled():
        with pytest.raises(ValueError, match="limit_price"):
            engine.submit(bad)


# ---------------------------------------------------------------------------
# Working-orders snapshot
# ---------------------------------------------------------------------------


def test_working_orders_for_position_filters_by_position_id():
    engine, tracker, pos_a = _setup()
    with tk_thread_check_disabled():
        pos_b = tracker.open(
            symbol="MSFT", side="long", qty=10, price=400,
            source="manual", ts=_ts(),
        )
        engine.submit(_mk_order(pos_a.id, kind=PaperOrderKind.LIMIT, price=110.0))
        engine.submit(_mk_order(pos_b.id, kind=PaperOrderKind.LIMIT, price=410.0))
        engine.submit(_mk_order(pos_a.id, kind=PaperOrderKind.STOP, price=95.0))
    a_orders = engine.working_orders_for_position(pos_a.id)
    b_orders = engine.working_orders_for_position(pos_b.id)
    assert len(a_orders) == 2 and all(o.position_id == pos_a.id for o in a_orders)
    assert len(b_orders) == 1 and b_orders[0].position_id == pos_b.id


def test_working_orders_returns_all_in_fifo_order():
    engine, tracker, pos_a = _setup()
    with tk_thread_check_disabled():
        pos_b = tracker.open(
            symbol="MSFT", side="long", qty=10, price=400,
            source="manual", ts=_ts(),
        )
        oid_1 = engine.submit(_mk_order(pos_a.id, kind=PaperOrderKind.LIMIT, price=110.0))
        oid_2 = engine.submit(_mk_order(pos_b.id, kind=PaperOrderKind.LIMIT, price=410.0))
        oid_3 = engine.submit(_mk_order(pos_a.id, kind=PaperOrderKind.STOP, price=95.0))
    all_orders = engine.working_orders()
    assert [o.id for o in all_orders] == [oid_1, oid_2, oid_3]


# ---------------------------------------------------------------------------
# Stats counters
# ---------------------------------------------------------------------------


def test_stats_tracks_submitted_filled_cancelled_rejected():
    engine, _t, pos = _setup()
    with tk_thread_check_disabled():
        # 1 submitted + filled
        engine.submit(_mk_order(pos.id, kind=PaperOrderKind.MARKET))
        engine.on_bar(pos.id, _bar(o=100, h=101, l=99, c=100), is_close=True)
        # 1 submitted + cancelled
        oid = engine.submit(_mk_order(pos.id, kind=PaperOrderKind.LIMIT, price=200.0))
        engine.cancel(oid)
        # 1 rejected (unknown position)
        with pytest.raises(ValueError):
            engine.submit(_mk_order("nope", kind=PaperOrderKind.MARKET))
    snap = engine.stats()
    assert snap["submitted"] == 2
    assert snap["filled"] == 1
    assert snap["cancelled"] == 1
    assert snap["rejected"] == 1
    assert snap["working"] == 0


# ---------------------------------------------------------------------------
# Tk-thread guard
# ---------------------------------------------------------------------------


def test_submit_from_worker_thread_raises():
    engine, _t, pos = _setup()
    err: List[Exception] = []

    def worker() -> None:
        try:
            engine.submit(_mk_order(pos.id, kind=PaperOrderKind.MARKET))
        except Exception as e:  # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert err and isinstance(err[0], TkThreadViolation)


def test_on_bar_from_worker_thread_raises():
    engine, _t, pos = _setup()
    with tk_thread_check_disabled():
        engine.submit(_mk_order(pos.id, kind=PaperOrderKind.MARKET))
    err: List[Exception] = []

    def worker() -> None:
        try:
            engine.on_bar(pos.id, _bar(o=100, h=101, l=99, c=100), is_close=True)
        except Exception as e:  # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert err and isinstance(err[0], TkThreadViolation)


# ---------------------------------------------------------------------------
# Multi-order-on-same-bar: clamp-to-zero behavior (bonus, documented in spec)
# ---------------------------------------------------------------------------


def test_two_orders_same_bar_first_closes_position_second_is_clamped_no_op():
    engine, tracker, pos = _setup(qty=100)
    with tk_thread_check_disabled():
        # First order: full-qty market exit.
        engine.submit(_mk_order(
            pos.id, kind=PaperOrderKind.MARKET, side=OrderSide.SELL, qty=100,
            label="first",
        ))
        # Second order: another full-qty market exit (e.g., a sibling that
        # the upstream evaluator will OCO-cancel after the first fills,
        # but inside this single on_bar both are evaluated in FIFO order).
        engine.submit(_mk_order(
            pos.id, kind=PaperOrderKind.MARKET, side=OrderSide.SELL, qty=100,
            label="second",
        ))
        fills = engine.on_bar(pos.id, _bar(o=100, h=101, l=99, c=100), is_close=True)
    assert len(fills) == 2
    first, second = fills
    assert first.label == "first" and first.qty == pytest.approx(100.0)
    assert second.label == "second" and second.qty == pytest.approx(0.0)
    # Position fully closed after the first fill; second is a recorded no-op.
    assert tracker.get(pos.id).qty_open == 0
    assert engine.stats()["filled"] == 1  # only non-zero qty counts
