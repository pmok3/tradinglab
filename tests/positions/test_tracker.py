"""Tests for ``tradinglab.positions.tracker.PositionTracker``."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import List, Tuple

import pytest

from tradinglab.core.thread_guard import (
    TkThreadViolation,
    tk_thread_check_disabled,
)
from tradinglab.positions.model import (
    Position,
    PositionEvent,
    PositionEventKind,
)
from tradinglab.positions.tracker import PositionTracker


@pytest.fixture
def tracker() -> PositionTracker:
    return PositionTracker()


def _ts() -> datetime:
    return datetime(2026, 5, 4, 14, 30, tzinfo=timezone.utc)


# ---- open / list / get -----------------------------------------------------

def test_open_creates_position_with_qty_and_watermarks_seeded(tracker):
    p = tracker.open(
        symbol="aapl", side="long", qty=100, price=175.0,
        source="sandbox", ts=_ts(),
    )
    assert p.symbol == "AAPL"
    assert p.qty_open == 100
    assert p.qty_initial == 100
    assert p.avg_entry_price == 175.0
    assert p.high_watermark == 175.0 and p.low_watermark == 175.0 and p.last_price == 175.0
    assert tracker.get(p.id) is p


def test_open_rejects_zero_qty(tracker):
    with pytest.raises(ValueError):
        tracker.open(symbol="A", side="long", qty=0, price=100, source="manual")


def test_open_rejects_zero_price(tracker):
    with pytest.raises(ValueError):
        tracker.open(symbol="A", side="long", qty=1, price=0, source="manual")


def test_open_rejects_duplicate_id(tracker):
    p = tracker.open(symbol="A", side="long", qty=1, price=100, source="manual")
    with pytest.raises(ValueError, match="already exists"):
        tracker.open(
            symbol="B", side="long", qty=1, price=100, source="manual",
            position_id=p.id,
        )


def test_list_open_filters_closed(tracker):
    p1 = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    p2 = tracker.open(symbol="MSFT", side="long", qty=5, price=400, source="manual")
    tracker.apply_fill(position_id=p1.id, qty=10, price=180)
    open_now = tracker.list_open()
    assert {p.id for p in open_now} == {p2.id}


def test_list_open_for_symbol_and_side(tracker):
    p1 = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    p2 = tracker.open(symbol="AAPL", side="short", qty=10, price=180, source="manual")
    tracker.open(symbol="MSFT", side="long", qty=10, price=400, source="manual")
    longs = tracker.list_open_for("aapl", side="long")
    assert {p.id for p in longs} == {p1.id}
    all_aapl = tracker.list_open_for("AAPL")
    assert {p.id for p in all_aapl} == {p1.id, p2.id}


# ---- apply_fill ------------------------------------------------------------

def test_apply_fill_partial_long_records_realized_pnl(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=100, price=175, source="manual")
    tracker.apply_fill(position_id=p.id, qty=50, price=180.0)
    assert p.qty_open == 50
    assert p.realized_pnl == pytest.approx(250.0)  # (180-175) * 50


def test_apply_fill_partial_short_records_realized_pnl(tracker):
    p = tracker.open(symbol="AAPL", side="short", qty=100, price=180, source="manual")
    tracker.apply_fill(position_id=p.id, qty=40, price=175.0)
    assert p.qty_open == 60
    assert p.realized_pnl == pytest.approx(200.0)  # (180-175) * 40


def test_apply_fill_full_close_emits_strategy_unbind_when_attached(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=100, price=175, source="manual")
    tracker.bind_strategy(p.id, "s-1")
    events: list[PositionEvent] = []
    tracker.subscribe(lambda ev, pos: events.append(ev))
    tracker.apply_fill(position_id=p.id, qty=100, price=180)
    kinds = [e.kind for e in events]
    assert PositionEventKind.CLOSE in kinds
    assert PositionEventKind.STRATEGY_UNBIND in kinds
    assert p.strategy_id is None


def test_apply_fill_clamps_to_qty_open(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    tracker.apply_fill(position_id=p.id, qty=999, price=180)
    assert p.qty_open == 0
    assert p.realized_pnl == pytest.approx(50.0)  # only 10 shares filled


def test_apply_fill_zero_after_close_is_silent_noop(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    tracker.apply_fill(position_id=p.id, qty=10, price=180)
    pnl_before = p.realized_pnl
    tracker.apply_fill(position_id=p.id, qty=5, price=200)  # pos already flat
    assert p.realized_pnl == pnl_before


def test_apply_fill_unknown_position_raises(tracker):
    with pytest.raises(KeyError):
        tracker.apply_fill(position_id="nope", qty=1, price=100)


# ---- mark ------------------------------------------------------------------

def test_mark_updates_watermarks_for_open_positions_only(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    tracker.mark("AAPL", 180.0)
    assert p.last_price == 180.0
    assert p.high_watermark == 180.0
    tracker.mark("AAPL", 170.0)
    assert p.last_price == 170.0
    assert p.high_watermark == 180.0
    assert p.low_watermark == 170.0


def test_mark_uppercases_symbol(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    tracker.mark("aapl", 180.0)
    assert p.last_price == 180.0


def test_mark_increments_bars_held_when_bar_close(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    tracker.mark("AAPL", 176, bar_close=True)
    tracker.mark("AAPL", 177, bar_close=False)
    tracker.mark("AAPL", 178, bar_close=True)
    assert p.bars_held == 2


def test_mark_skips_closed_positions(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    tracker.apply_fill(position_id=p.id, qty=10, price=180)
    last_before = p.last_price
    tracker.mark("AAPL", 999.0)
    assert p.last_price == last_before


def test_mark_negative_price_silent_noop(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    affected = tracker.mark("AAPL", 0.0)
    assert affected == []
    assert p.last_price == 175.0


# ---- bind / unbind strategy -----------------------------------------------

def test_bind_strategy_sets_strategy_id_and_emits_event(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    events: list[PositionEvent] = []
    tracker.subscribe(lambda ev, pos: events.append(ev))
    tracker.bind_strategy(p.id, "s-1")
    assert p.strategy_id == "s-1"
    assert any(e.kind == PositionEventKind.STRATEGY_BIND for e in events)


def test_bind_strategy_idempotent_no_event(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    tracker.bind_strategy(p.id, "s-1")
    events: list[PositionEvent] = []
    tracker.subscribe(lambda ev, pos: events.append(ev))
    tracker.bind_strategy(p.id, "s-1")
    assert events == []


def test_unbind_strategy_clears_id_and_emits(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    tracker.bind_strategy(p.id, "s-1")
    events: list[PositionEvent] = []
    tracker.subscribe(lambda ev, pos: events.append(ev))
    tracker.unbind_strategy(p.id, reason="user")
    assert p.strategy_id is None
    assert any(e.kind == PositionEventKind.STRATEGY_UNBIND for e in events)


def test_bind_strategy_rejects_empty_id(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    with pytest.raises(ValueError):
        tracker.bind_strategy(p.id, "")


# ---- edit (manual only) ----------------------------------------------------

def test_edit_manual_position_updates_fields_and_emits(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    events: list[PositionEvent] = []
    tracker.subscribe(lambda ev, pos: events.append(ev))
    tracker.edit(p.id, qty_open=8, avg_entry_price=176.0)
    assert p.qty_open == 8
    assert p.avg_entry_price == 176.0
    assert any(e.kind == PositionEventKind.EDIT for e in events)


def test_edit_refuses_sandbox_position(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="sandbox")
    with pytest.raises(ValueError, match="manual"):
        tracker.edit(p.id, qty_open=5)


def test_edit_rejects_negative_qty(tracker):
    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    with pytest.raises(ValueError):
        tracker.edit(p.id, qty_open=-1)


# ---- subscriber re-entrancy ------------------------------------------------

def test_subscriber_modifying_subscriber_list_does_not_crash(tracker):
    """A subscriber removing itself mid-dispatch must not break iteration."""
    seen: list[str] = []

    def sub_a(ev: PositionEvent, pos: Position) -> None:
        seen.append("a")

    def sub_b(ev: PositionEvent, pos: Position) -> None:
        seen.append("b")
        unsub_a()

    unsub_a = tracker.subscribe(sub_a)
    tracker.subscribe(sub_b)
    tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    # Both should have been called in this dispatch (frozen-tuple snapshot).
    assert "a" in seen and "b" in seen


def test_nested_mutation_in_subscriber_is_re_entrancy_safe(tracker):
    """A subscriber that triggers another mutation queues the nested event."""
    nested_kinds: list[str] = []

    p = tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    tracker.bind_strategy(p.id, "s-1")

    def sub(ev: PositionEvent, pos: Position) -> None:
        nested_kinds.append(ev.kind.value)
        # If we observe the open event, fire a fill that closes the position
        # — which itself should trigger CLOSE + STRATEGY_UNBIND.
        if ev.kind == PositionEventKind.MARK and pos.last_price == 200.0:
            tracker.apply_fill(position_id=p.id, qty=10, price=200.0)

    tracker.subscribe(sub)
    tracker.mark("AAPL", 200.0)
    # Subscriber should have received the outer MARK plus the nested CLOSE
    # and STRATEGY_UNBIND in emit order. No infinite recursion / no crash.
    assert "mark" in nested_kinds
    assert "close" in nested_kinds
    assert "strategy_unbind" in nested_kinds
    assert p.qty_open == 0


def test_subscriber_exception_does_not_kill_other_subscribers(tracker):
    seen: list[str] = []

    def good(ev, pos):
        seen.append(ev.kind.value)

    def bad(ev, pos):
        raise RuntimeError("boom")

    tracker.subscribe(bad)
    tracker.subscribe(good)
    tracker.open(symbol="AAPL", side="long", qty=10, price=175, source="manual")
    assert seen == ["open"]


def test_unsubscribe_callable_returned_by_subscribe(tracker):
    seen: list[str] = []
    unsub = tracker.subscribe(lambda ev, pos: seen.append(ev.kind.value))
    tracker.open(symbol="A", side="long", qty=1, price=100, source="manual")
    assert seen == ["open"]
    unsub()
    tracker.open(symbol="B", side="long", qty=1, price=100, source="manual")
    assert seen == ["open"]  # subscriber gone


# ---- Tk-thread invariant ---------------------------------------------------

def test_open_from_worker_thread_raises(tracker):
    err: list[Exception] = []

    def worker() -> None:
        try:
            tracker.open(symbol="A", side="long", qty=1, price=100, source="manual")
        except Exception as e:  # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert err and isinstance(err[0], TkThreadViolation)


def test_mutation_under_check_disabled_works_off_thread(tracker):
    err: list[Exception] = []
    ok: list[Position] = []

    def worker() -> None:
        try:
            with tk_thread_check_disabled():
                p = tracker.open(symbol="A", side="long", qty=1, price=100, source="manual")
            ok.append(p)
        except Exception as e:  # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert not err
    assert ok and ok[0].symbol == "A"
