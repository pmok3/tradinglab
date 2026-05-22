"""Tests for PositionTracker.open_from_fill (entries-v1 addition)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Tuple

import pytest

from tradinglab.core import thread_guard
from tradinglab.positions.model import (
    Position,
    PositionEvent,
    PositionEventKind,
)
from tradinglab.positions.tracker import PositionTracker


@pytest.fixture(autouse=True)
def _no_tk():
    with thread_guard.tk_thread_check_disabled():
        yield


@pytest.fixture
def tracker() -> PositionTracker:
    return PositionTracker()


@pytest.fixture
def collector(tracker: PositionTracker) -> List[Tuple[PositionEvent, Position]]:
    events: List[Tuple[PositionEvent, Position]] = []
    tracker.subscribe(lambda ev, pos: events.append((ev, pos)))
    return events


class TestOpenFromFillBasics:
    def test_creates_position(self, tracker, collector):
        pos = tracker.open_from_fill(
            symbol="AAPL", side="long", qty=100, price=150.0,
            strategy_id="entry-1",
        )
        assert pos.id in tracker._positions  # noqa: SLF001
        assert pos.symbol == "AAPL"
        assert pos.side == "long"
        assert pos.qty_open == 100.0
        assert pos.qty_initial == 100.0
        assert pos.avg_entry_price == 150.0
        assert pos.last_price == 150.0
        assert pos.high_watermark == 150.0
        assert pos.low_watermark == 150.0
        assert pos.strategy_id == "entry-1"
        assert pos.source == "sandbox"

    def test_emits_open_event(self, tracker, collector):
        tracker.open_from_fill(
            symbol="MSFT", side="long", qty=50, price=200.0,
        )
        assert len(collector) == 1
        ev, pos = collector[0]
        assert ev.kind == PositionEventKind.OPEN
        assert ev.qty == 50
        assert ev.price == 200.0
        assert pos.symbol == "MSFT"

    def test_uses_provided_position_id(self, tracker):
        pos = tracker.open_from_fill(
            symbol="AAPL", side="long", qty=10, price=100.0,
            position_id="my-fixed-id",
        )
        assert pos.id == "my-fixed-id"

    def test_mints_uuid_when_no_id_provided(self, tracker):
        pos = tracker.open_from_fill(
            symbol="AAPL", side="long", qty=10, price=100.0,
        )
        assert pos.id  # non-empty
        # UUID4 hyphenated form is 36 chars
        assert len(pos.id) >= 32


class TestDuplicateIdHardError:
    def test_duplicate_position_id_raises(self, tracker):
        tracker.open_from_fill(
            symbol="AAPL", side="long", qty=10, price=100.0,
            position_id="dup",
        )
        with pytest.raises(ValueError, match="already exists"):
            tracker.open_from_fill(
                symbol="MSFT", side="long", qty=20, price=200.0,
                position_id="dup",
            )

    def test_duplicate_against_open_call_also_raises(self, tracker):
        # Pre-existing position from an `open()` call (e.g. manual sandbox).
        tracker.open(
            symbol="AAPL", side="long", qty=5, price=99.0,
            source="manual", position_id="shared",
        )
        with pytest.raises(ValueError, match="already exists"):
            tracker.open_from_fill(
                symbol="MSFT", side="long", qty=20, price=200.0,
                position_id="shared",
            )


class TestFillMetaPropagation:
    def test_fill_meta_in_open_event(self, tracker, collector):
        tracker.open_from_fill(
            symbol="AAPL", side="long", qty=10, price=100.0,
            fill_meta={"order_id": "ord-1", "trigger_id": "trg-1"},
        )
        ev, _ = collector[0]
        assert ev.meta["order_id"] == "ord-1"
        assert ev.meta["trigger_id"] == "trg-1"

    def test_side_and_source_win_over_fill_meta(self, tracker, collector):
        tracker.open_from_fill(
            symbol="AAPL", side="long", qty=10, price=100.0,
            fill_meta={"side": "tampered", "source": "tampered"},
        )
        ev, _ = collector[0]
        assert ev.meta["side"] == "long"
        assert ev.meta["source"] == "sandbox"

    def test_strategy_id_added_to_meta(self, tracker, collector):
        tracker.open_from_fill(
            symbol="AAPL", side="long", qty=10, price=100.0,
            strategy_id="strat-1",
        )
        ev, _ = collector[0]
        assert ev.meta["strategy_id"] == "strat-1"


class TestValidation:
    def test_zero_qty_raises(self, tracker):
        with pytest.raises(ValueError, match="qty"):
            tracker.open_from_fill(
                symbol="AAPL", side="long", qty=0, price=100.0,
            )

    def test_negative_qty_raises(self, tracker):
        with pytest.raises(ValueError):
            tracker.open_from_fill(
                symbol="AAPL", side="long", qty=-5, price=100.0,
            )

    def test_zero_price_raises(self, tracker):
        with pytest.raises(ValueError, match="price"):
            tracker.open_from_fill(
                symbol="AAPL", side="long", qty=10, price=0,
            )

    def test_empty_symbol_raises(self, tracker):
        with pytest.raises(ValueError, match="symbol"):
            tracker.open_from_fill(
                symbol="", side="long", qty=10, price=100.0,
            )


class TestTimestamping:
    def test_explicit_ts_used(self, tracker, collector):
        when = datetime(2024, 1, 15, 9, 35, tzinfo=timezone.utc)
        pos = tracker.open_from_fill(
            symbol="AAPL", side="long", qty=10, price=100.0, ts=when,
        )
        assert pos.entry_time == when
        ev, _ = collector[0]
        assert ev.ts == when


class TestShortSide:
    def test_short_side_creates_short_position(self, tracker):
        pos = tracker.open_from_fill(
            symbol="TSLA", side="short", qty=10, price=200.0,
        )
        assert pos.side == "short"
        # Shorts grow in value when price falls; signed_qty_open is negative.
        assert pos.signed_qty_open() == -10.0


class TestApplyFillCanCloseEntry:
    """Sanity check: an entry-fill-opened position can be closed by apply_fill."""

    def test_round_trip_open_then_close(self, tracker, collector):
        pos = tracker.open_from_fill(
            symbol="AAPL", side="long", qty=10, price=100.0,
        )
        tracker.apply_fill(
            position_id=pos.id, qty=10, price=110.0,
        )
        assert pos.qty_open == 0
        assert pos.realized_pnl == pytest.approx(100.0)  # (110-100)*10
        kinds = [e.kind for e, _ in collector]
        assert PositionEventKind.OPEN in kinds
        assert PositionEventKind.CLOSE in kinds
