"""Tests for PaperBrokerEngine pending-entry extension (entries-v1)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradinglab.core import thread_guard
from tradinglab.exits.model import OrderSide
from tradinglab.exits.paper_engine import (
    OrderTargetKind,
    PaperBrokerEngine,
    PaperOrder,
    PaperOrderKind,
)
from tradinglab.exits.spec import Bar
from tradinglab.positions.tracker import PositionTracker


@pytest.fixture(autouse=True)
def _no_tk():
    with thread_guard.tk_thread_check_disabled():
        yield


@pytest.fixture
def tracker() -> PositionTracker:
    return PositionTracker()


@pytest.fixture
def engine(tracker) -> PaperBrokerEngine:
    return PaperBrokerEngine(tracker)


def _bar(o, h, l, c, ts=None) -> Bar:
    return Bar(
        date=ts or datetime(2024, 1, 15, 9, 35, tzinfo=timezone.utc),
        open=float(o), high=float(h), low=float(l), close=float(c), volume=0,
    )


def _pending_market(symbol: str, qty: float, *, side="long", oid="ord-1", pid="pend-1") -> PaperOrder:
    return PaperOrder(
        id=oid,
        position_id="",
        kind=PaperOrderKind.MARKET,
        side=OrderSide.BUY if side == "long" else OrderSide.SELL,
        qty=qty,
        target_kind=OrderTargetKind.PENDING_ENTRY,
        symbol=symbol,
        pending_position_id=pid,
        position_side=side,
        strategy_id="strat-A",
        on_fill_exit_ids=("exit-1",),
    )


# ---------- submit + validation ----------

class TestSubmitPendingEntry:
    def test_submit_pending_market_succeeds(self, engine):
        oid = engine.submit(_pending_market("AAPL", 100))
        assert oid == "ord-1"
        assert len(engine.pending_orders_for_symbol("AAPL")) == 1

    def test_pending_index_keyed_uppercase(self, engine):
        engine.submit(_pending_market("aapl", 10))
        # Lookup by mixed case still works.
        assert len(engine.pending_orders_for_symbol("AAPL")) == 1
        assert len(engine.pending_orders_for_symbol("aapl")) == 1

    def test_pending_without_symbol_rejected(self, engine):
        order = PaperOrder(
            id="x", position_id="", kind=PaperOrderKind.MARKET,
            side=OrderSide.BUY, qty=10,
            target_kind=OrderTargetKind.PENDING_ENTRY,
            pending_position_id="p", position_side="long",
        )
        with pytest.raises(ValueError, match="symbol"):
            engine.submit(order)

    def test_pending_without_pending_id_rejected(self, engine):
        order = PaperOrder(
            id="x", position_id="", kind=PaperOrderKind.MARKET,
            side=OrderSide.BUY, qty=10,
            target_kind=OrderTargetKind.PENDING_ENTRY,
            symbol="AAPL", position_side="long",
        )
        with pytest.raises(ValueError, match="pending_position_id"):
            engine.submit(order)

    def test_pending_without_position_side_rejected(self, engine):
        order = PaperOrder(
            id="x", position_id="", kind=PaperOrderKind.MARKET,
            side=OrderSide.BUY, qty=10,
            target_kind=OrderTargetKind.PENDING_ENTRY,
            symbol="AAPL", pending_position_id="p",
        )
        with pytest.raises(ValueError, match="position_side"):
            engine.submit(order)

    def test_pending_with_existing_id_rejected(self, engine, tracker):
        # Pre-create a position with the same id as our pending entry.
        tracker.open(
            symbol="AAPL", side="long", qty=1, price=100.0,
            source="manual", position_id="pend-collide",
        )
        order = PaperOrder(
            id="x", position_id="", kind=PaperOrderKind.MARKET,
            side=OrderSide.BUY, qty=10,
            target_kind=OrderTargetKind.PENDING_ENTRY,
            symbol="AAPL", pending_position_id="pend-collide",
            position_side="long",
        )
        with pytest.raises(ValueError, match="already exists"):
            engine.submit(order)


# ---------- on_bar_for_pending: fills ----------

class TestOnBarForPendingMarket:
    def test_market_fills_on_bar_close(self, engine, tracker):
        engine.submit(_pending_market("AAPL", 100))
        fills = engine.on_bar_for_pending(
            "AAPL", _bar(150, 151, 149, 150.5), is_close=True,
        )
        assert len(fills) == 1
        f = fills[0]
        assert f.qty == 100
        assert f.price == pytest.approx(150.5)
        assert f.position_id == "pend-1"
        # Position was minted in tracker.
        pos = tracker.get("pend-1")
        assert pos is not None
        assert pos.symbol == "AAPL"
        assert pos.side == "long"
        assert pos.qty_open == 100
        assert pos.avg_entry_price == pytest.approx(150.5)
        assert pos.strategy_id == "strat-A"

    def test_pending_index_cleared_after_fill(self, engine):
        engine.submit(_pending_market("AAPL", 100))
        engine.on_bar_for_pending("AAPL", _bar(150, 151, 149, 150.5), is_close=True)
        assert engine.pending_orders_for_symbol("AAPL") == []

    def test_short_entry_creates_short_position(self, engine, tracker):
        engine.submit(_pending_market("TSLA", 50, side="short", oid="ord-s", pid="ps-1"))
        fills = engine.on_bar_for_pending(
            "TSLA", _bar(200, 201, 199, 200.0), is_close=True,
        )
        assert len(fills) == 1
        pos = tracker.get("ps-1")
        assert pos.side == "short"
        assert pos.qty_open == 50

    def test_no_pending_for_symbol_returns_empty(self, engine):
        assert engine.on_bar_for_pending("AAPL", _bar(1, 1, 1, 1), is_close=True) == []

    def test_two_pending_orders_same_symbol_both_fill(self, engine, tracker):
        engine.submit(_pending_market("AAPL", 100, oid="o1", pid="p1"))
        engine.submit(_pending_market("AAPL", 50, oid="o2", pid="p2"))
        fills = engine.on_bar_for_pending(
            "AAPL", _bar(150, 151, 149, 150.5), is_close=True,
        )
        assert len(fills) == 2
        assert tracker.get("p1") is not None
        assert tracker.get("p2") is not None

    def test_on_fill_exit_ids_in_open_event_meta(self, engine, tracker):
        events = []
        tracker.subscribe(lambda ev, pos: events.append(ev))
        engine.submit(_pending_market("AAPL", 10))
        engine.on_bar_for_pending("AAPL", _bar(100, 100, 100, 100), is_close=True)
        open_event = next(e for e in events if e.kind.value == "open")
        assert open_event.meta["on_fill_exit_ids"] == ["exit-1"]
        assert open_event.meta["strategy_id"] == "strat-A"


class TestOnBarForPendingLimit:
    def test_long_limit_fills_when_low_touches(self, engine, tracker):
        order = PaperOrder(
            id="ord-l", position_id="", kind=PaperOrderKind.LIMIT,
            side=OrderSide.BUY, qty=100, price=99.0,
            target_kind=OrderTargetKind.PENDING_ENTRY,
            symbol="AAPL", pending_position_id="pl-1", position_side="long",
        )
        engine.submit(order)
        # Bar low touches 98 -> fill at limit price 99
        fills = engine.on_bar_for_pending(
            "AAPL", _bar(100, 101, 98, 99.5), is_close=True,
        )
        assert len(fills) == 1
        assert fills[0].price == 99.0
        assert tracker.get("pl-1").avg_entry_price == 99.0

    def test_long_limit_does_not_fill_when_low_above(self, engine, tracker):
        order = PaperOrder(
            id="ord-l", position_id="", kind=PaperOrderKind.LIMIT,
            side=OrderSide.BUY, qty=100, price=99.0,
            target_kind=OrderTargetKind.PENDING_ENTRY,
            symbol="AAPL", pending_position_id="pl-1", position_side="long",
        )
        engine.submit(order)
        fills = engine.on_bar_for_pending(
            "AAPL", _bar(100, 101, 99.5, 100.5), is_close=True,
        )
        assert fills == []
        # Order stays working.
        assert len(engine.pending_orders_for_symbol("AAPL")) == 1

    def test_short_limit_fills_when_high_touches(self, engine, tracker):
        order = PaperOrder(
            id="ord-s", position_id="", kind=PaperOrderKind.LIMIT,
            side=OrderSide.SELL, qty=50, price=101.0,
            target_kind=OrderTargetKind.PENDING_ENTRY,
            symbol="MSFT", pending_position_id="pl-2", position_side="short",
        )
        engine.submit(order)
        fills = engine.on_bar_for_pending(
            "MSFT", _bar(100, 101.5, 99, 100.5), is_close=True,
        )
        assert len(fills) == 1
        assert fills[0].price == 101.0
        assert tracker.get("pl-2").side == "short"


class TestOnBarForPendingStop:
    def test_long_stop_breakout(self, engine, tracker):
        order = PaperOrder(
            id="o", position_id="", kind=PaperOrderKind.STOP,
            side=OrderSide.BUY, qty=100, price=105.0,
            target_kind=OrderTargetKind.PENDING_ENTRY,
            symbol="AAPL", pending_position_id="ps-1", position_side="long",
        )
        engine.submit(order)
        fills = engine.on_bar_for_pending(
            "AAPL", _bar(100, 105.5, 99, 104), is_close=True,
        )
        assert len(fills) == 1
        # Stop fills at max(stop, open)=max(105, 100)=105.
        assert fills[0].price == 105.0
        assert tracker.get("ps-1") is not None


# ---------- existing-position orders unaffected ----------

class TestExistingPositionOrdersUnchanged:
    def test_exit_order_against_existing_position_still_works(self, engine, tracker):
        # Open a position via tracker.
        pos = tracker.open(
            symbol="AAPL", side="long", qty=100, price=100.0,
            source="manual",
        )
        # Submit a regular SELL stop exit (target_kind defaults to EXISTING_POSITION).
        order = PaperOrder(
            id="exit-1", position_id=pos.id, kind=PaperOrderKind.STOP,
            side=OrderSide.SELL, qty=100, price=95.0,
        )
        engine.submit(order)
        # Stop hit on the bar.
        fills = engine.on_bar(pos.id, _bar(100, 100, 94, 96), is_close=True)
        assert len(fills) == 1
        assert tracker.get(pos.id).qty_open == 0

    def test_on_bar_skips_pending_orders(self, engine, tracker):
        """on_bar(position_id) should not touch pending-entry orders for the symbol."""
        pos = tracker.open(
            symbol="AAPL", side="long", qty=100, price=100.0,
            source="manual",
        )
        engine.submit(_pending_market("AAPL", 50))  # different from pos's symbol/path
        # Exit-side STOP that *would* fill if it were checked.
        engine.submit(PaperOrder(
            id="exit-1", position_id=pos.id, kind=PaperOrderKind.STOP,
            side=OrderSide.SELL, qty=100, price=95.0,
        ))
        fills = engine.on_bar(pos.id, _bar(100, 100, 94, 96), is_close=True)
        # Only the exit fired; pending-entry MARKET stayed working.
        assert len(fills) == 1
        assert len(engine.pending_orders_for_symbol("AAPL")) == 1


# ---------- cancel ----------

class TestCancelPendingEntry:
    def test_cancel_pending_drops_index(self, engine):
        engine.submit(_pending_market("AAPL", 10))
        assert engine.cancel("ord-1") is True
        assert engine.pending_orders_for_symbol("AAPL") == []

    def test_cancel_all_pending_for_symbol(self, engine):
        engine.submit(_pending_market("AAPL", 10, oid="o1", pid="p1"))
        engine.submit(_pending_market("AAPL", 20, oid="o2", pid="p2"))
        engine.submit(_pending_market("MSFT", 30, oid="o3", pid="p3"))
        n = engine.cancel_all_pending_for_symbol("AAPL")
        assert n == 2
        assert engine.pending_orders_for_symbol("AAPL") == []
        assert len(engine.pending_orders_for_symbol("MSFT")) == 1

    def test_cancel_all_for_position_does_not_touch_pending(self, engine, tracker):
        pos = tracker.open(
            symbol="AAPL", side="long", qty=100, price=100.0, source="manual",
        )
        engine.submit(_pending_market("AAPL", 50))
        engine.submit(PaperOrder(
            id="exit-1", position_id=pos.id, kind=PaperOrderKind.STOP,
            side=OrderSide.SELL, qty=100, price=95.0,
        ))
        n = engine.cancel_all_for_position(pos.id)
        assert n == 1  # only the exit
        assert len(engine.pending_orders_for_symbol("AAPL")) == 1
