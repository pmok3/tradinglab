"""Tests for entries.signals (EntrySignal + EntryPaperSink + EntryManualSink)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tradinglab.core import thread_guard
from tradinglab.entries.audit import AuditLog
from tradinglab.entries.signals import (
    EntryManualSignalEvent,
    EntryManualSink,
    EntryOrderKind,
    EntryPaperSink,
    EntrySignal,
)
from tradinglab.exits.model import OrderSide
from tradinglab.exits.paper_engine import (
    OrderTargetKind,
    PaperBrokerEngine,
    PaperOrderKind,
)
from tradinglab.exits.spec import Bar
from tradinglab.positions.tracker import PositionTracker


@pytest.fixture(autouse=True)
def _no_tk():
    with thread_guard.tk_thread_check_disabled():
        yield


def _signal(**overrides):
    base = dict(
        strategy_id="strat-1",
        pending_position_id="pp-1",
        symbol="AAPL",
        trigger_id="trig-1",
        kind=EntryOrderKind.MARKET,
        side=OrderSide.BUY,
        position_side="long",
        qty=100.0,
        price=None,
        limit_price=None,
        on_fill_exit_ids=("exit-A",),
        label="entry",
    )
    base.update(overrides)
    return EntrySignal.new(**base)


def _bar(o, h, l, c, ts=None) -> Bar:
    return Bar(
        date=ts or datetime(2024, 1, 15, 9, 35, tzinfo=timezone.utc),
        open=float(o), high=float(h), low=float(l), close=float(c), volume=0,
    )


# ---------------------------------------------------------------------------
# EntrySignal
# ---------------------------------------------------------------------------


class TestEntrySignal:
    def test_new_assigns_id(self):
        s1 = _signal()
        s2 = _signal()
        assert s1.id and s2.id and s1.id != s2.id

    def test_immutable(self):
        s = _signal()
        with pytest.raises(Exception):
            s.qty = 200  # frozen=True

    def test_default_extra_is_empty(self):
        s = _signal()
        assert s.extra == {}

    def test_extra_independent_per_instance(self):
        s1 = _signal()
        s2 = _signal()
        # Two instances should have independent extra dicts.
        s1.extra["x"] = 1
        assert "x" not in s2.extra


# ---------------------------------------------------------------------------
# EntryPaperSink
# ---------------------------------------------------------------------------


class TestEntryPaperSink:
    @pytest.fixture
    def setup(self):
        tracker = PositionTracker()
        engine = PaperBrokerEngine(tracker)
        sink = EntryPaperSink(engine)
        return tracker, engine, sink

    def test_submit_translates_to_pending_paper_order(self, setup):
        tracker, engine, sink = setup
        signal = _signal()
        order_id = sink.submit(signal)
        assert isinstance(order_id, str) and order_id

        # Engine has it indexed by symbol.
        pending = engine.pending_orders_for_symbol("AAPL")
        assert len(pending) == 1
        order = pending[0]
        assert order.id == order_id
        assert order.target_kind == OrderTargetKind.PENDING_ENTRY
        assert order.symbol == "AAPL"
        assert order.pending_position_id == "pp-1"
        assert order.position_side == "long"
        assert order.strategy_id == "strat-1"
        assert order.kind == PaperOrderKind.MARKET
        assert order.side == OrderSide.BUY
        assert order.qty == 100.0
        assert order.on_fill_exit_ids == ("exit-A",)

    def test_submit_limit_carries_price(self, setup):
        tracker, engine, sink = setup
        sig = _signal(kind=EntryOrderKind.LIMIT, price=99.0)
        sink.submit(sig)
        order = engine.pending_orders_for_symbol("AAPL")[0]
        assert order.kind == PaperOrderKind.LIMIT
        assert order.price == 99.0

    def test_submit_stop_limit_carries_both(self, setup):
        tracker, engine, sink = setup
        sig = _signal(kind=EntryOrderKind.STOP_LIMIT, price=105.0, limit_price=106.0)
        sink.submit(sig)
        order = engine.pending_orders_for_symbol("AAPL")[0]
        assert order.kind == PaperOrderKind.STOP_LIMIT
        assert order.price == 105.0
        assert order.limit_price == 106.0

    def test_short_signal_uses_sell_side(self, setup):
        tracker, engine, sink = setup
        sig = _signal(side=OrderSide.SELL, position_side="short", symbol="TSLA",
                      pending_position_id="pp-s")
        sink.submit(sig)
        order = engine.pending_orders_for_symbol("TSLA")[0]
        assert order.side == OrderSide.SELL
        assert order.position_side == "short"

    def test_index_by_pending_position(self, setup):
        tracker, engine, sink = setup
        sig = _signal()
        order_id = sink.submit(sig)
        assert sink.working_order_ids_for_pending_position("pp-1") == [order_id]
        assert sink.working_order_ids_for_pending_position("pp-other") == []

    def test_index_by_symbol(self, setup):
        tracker, engine, sink = setup
        sig = _signal()
        order_id = sink.submit(sig)
        assert sink.working_order_ids_for_symbol("AAPL") == [order_id]
        # Case-insensitive lookup.
        assert sink.working_order_ids_for_symbol("aapl") == [order_id]

    def test_cancel_drops_indexes(self, setup):
        tracker, engine, sink = setup
        order_id = sink.submit(_signal())
        assert sink.cancel(order_id) is True
        assert sink.working_order_ids_for_pending_position("pp-1") == []
        assert sink.working_order_ids_for_symbol("AAPL") == []
        assert engine.pending_orders_for_symbol("AAPL") == []

    def test_cancel_unknown_returns_false(self, setup):
        tracker, engine, sink = setup
        assert sink.cancel("ghost") is False

    def test_cancel_all_pending_for_symbol(self, setup):
        tracker, engine, sink = setup
        sink.submit(_signal(pending_position_id="pp-1"))
        sink.submit(_signal(pending_position_id="pp-2"))
        sink.submit(_signal(symbol="MSFT", pending_position_id="pp-3"))
        n = sink.cancel_all_pending_for_symbol("AAPL")
        assert n == 2
        assert sink.working_order_ids_for_symbol("AAPL") == []
        assert sink.working_order_ids_for_symbol("MSFT") != []

    def test_on_fill_drops_local_index(self, setup):
        tracker, engine, sink = setup
        order_id = sink.submit(_signal())
        # Simulate engine filling the order; the engine purges its index.
        engine.on_bar_for_pending(
            "AAPL", _bar(150, 151, 149, 150.5), is_close=True,
        )
        # Sink doesn't auto-clean — caller invokes on_fill.
        sink.on_fill(order_id)
        assert sink.working_order_ids_for_pending_position("pp-1") == []
        assert sink.working_order_ids_for_symbol("AAPL") == []

    def test_returns_list_copy(self, setup):
        tracker, engine, sink = setup
        sink.submit(_signal())
        ids = sink.working_order_ids_for_pending_position("pp-1")
        ids.clear()
        # Internal state untouched.
        assert sink.working_order_ids_for_pending_position("pp-1") != []


# ---------------------------------------------------------------------------
# EntryManualSink
# ---------------------------------------------------------------------------


class TestEntryManualSink:
    def test_submit_returns_manual_id(self):
        sink = EntryManualSink()
        oid = sink.submit(_signal())
        assert oid.startswith("manual-entry-")

    def test_submit_emits_event(self):
        sink = EntryManualSink()
        events: list = []
        sink.subscribe(events.append)
        oid = sink.submit(_signal())
        assert len(events) == 1
        e = events[0]
        assert e.kind == "submitted"
        assert e.order_id == oid
        assert e.signal is not None

    def test_cancel_emits_event_and_drops_indexes(self):
        sink = EntryManualSink()
        events: list = []
        sink.subscribe(events.append)
        oid = sink.submit(_signal())
        events.clear()
        assert sink.cancel(oid) is True
        assert events[0].kind == "cancelled"
        assert sink.working_order_ids_for_pending_position("pp-1") == []
        assert sink.working_order_ids_for_symbol("AAPL") == []

    def test_acknowledge_fill_emits_ack_event(self):
        sink = EntryManualSink()
        events: list = []
        sink.subscribe(events.append)
        oid = sink.submit(_signal())
        events.clear()
        assert sink.acknowledge_fill(oid) is True
        assert events[0].kind == "ack-fill"
        assert sink.working_order_ids_for_pending_position("pp-1") == []

    def test_acknowledge_unknown_returns_false(self):
        sink = EntryManualSink()
        assert sink.acknowledge_fill("ghost") is False

    def test_unsubscribe_stops_delivery(self):
        sink = EntryManualSink()
        events: list = []
        unsub = sink.subscribe(events.append)
        unsub()
        sink.submit(_signal())
        assert events == []

    def test_subscriber_exception_does_not_break_emit(self):
        sink = EntryManualSink()

        def bad(_e):
            raise RuntimeError("boom")

        good_events: list = []
        sink.subscribe(bad)
        sink.subscribe(good_events.append)
        sink.submit(_signal())
        assert len(good_events) == 1

    def test_cancel_all_pending_for_symbol(self):
        sink = EntryManualSink()
        sink.submit(_signal(pending_position_id="pp-1"))
        sink.submit(_signal(pending_position_id="pp-2"))
        sink.submit(_signal(symbol="MSFT", pending_position_id="pp-3"))
        n = sink.cancel_all_pending_for_symbol("AAPL")
        assert n == 2
        assert sink.working_order_ids_for_symbol("AAPL") == []
        assert sink.working_order_ids_for_symbol("MSFT") != []

    def test_audit_writes_records(self, tmp_path):
        audit = AuditLog(tmp_path)
        sink = EntryManualSink(audit=audit)
        oid = sink.submit(_signal())
        sink.acknowledge_fill(oid)
        # Tail returns dict records ordered chronologically.
        recs = audit.tail(10)
        kinds = [r["kind"] for r in recs]
        assert "entry_submit" in kinds
        assert "entry_fill" in kinds

    def test_double_submit_independent(self):
        """Two submits with same pending_position_id index both."""
        sink = EntryManualSink()
        oid1 = sink.submit(_signal(pending_position_id="pp-X"))
        oid2 = sink.submit(_signal(pending_position_id="pp-X"))
        assert sink.working_order_ids_for_pending_position("pp-X") == [oid1, oid2]
