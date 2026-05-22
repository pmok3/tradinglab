"""Tests for tradinglab.core.risk_gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timezone
from typing import Any, Dict, List, Optional

import pytest

from tradinglab.core import thread_guard
from tradinglab.core.risk_gate import (
    AllowAllRiskGate,
    DefaultRiskGate,
    RiskBlock,
)
from tradinglab.positions.tracker import PositionTracker


# ---- minimal fake EntrySignal --------------------------------------------

@dataclass
class FakeSignal:
    symbol: str = "AAPL"
    qty: float = 100.0
    side: str = "buy"
    price: Optional[float] = None
    stop_price: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@pytest.fixture(autouse=True)
def _no_tk():
    with thread_guard.tk_thread_check_disabled():
        yield


@pytest.fixture
def tracker() -> PositionTracker:
    return PositionTracker()


def _open(tracker: PositionTracker, symbol: str, side: str, qty: float, price: float):
    tracker.open(
        symbol=symbol, side=side, qty=qty, price=price,
        source="manual",
    )


def _clock(now: datetime):
    return lambda: now


class TestAllowAll:
    def test_never_blocks(self, tracker):
        gate = AllowAllRiskGate()
        sig = FakeSignal(price=100.0)
        assert gate.check(sig, tracker=tracker, clock=_clock(datetime.now(timezone.utc))) is None


class TestDailyLossLimit:
    def test_no_block_when_unset(self, tracker):
        gate = DefaultRiskGate()
        assert gate.check(FakeSignal(price=100.0), tracker=tracker, clock=_clock(datetime.now(timezone.utc))) is None

    def test_blocks_when_total_loss_at_limit(self, tracker):
        # Open a loser: long entry at 100, mark down to 80.
        _open(tracker, "AAPL", "long", 100, 100.0)
        tracker.mark("AAPL", 80.0)
        # P&L = (80-100)*100 = -2000.
        gate = DefaultRiskGate(daily_loss_limit=-1000.0)
        block = gate.check(
            FakeSignal(price=50.0),
            tracker=tracker,
            clock=_clock(datetime.now(timezone.utc)),
        )
        assert isinstance(block, RiskBlock)
        assert block.gate == "daily_loss_limit"
        assert block.meta["current"] == -2000.0
        assert block.meta["limit"] == -1000.0

    def test_does_not_block_when_within_budget(self, tracker):
        _open(tracker, "AAPL", "long", 100, 100.0)
        tracker.mark("AAPL", 95.0)  # -500 P&L
        gate = DefaultRiskGate(daily_loss_limit=-1000.0)
        assert gate.check(FakeSignal(price=50.0), tracker=tracker, clock=_clock(datetime.now(timezone.utc))) is None


class TestMaxConcurrent:
    def test_blocks_at_limit(self, tracker):
        _open(tracker, "AAPL", "long", 100, 100.0)
        _open(tracker, "MSFT", "long", 50, 200.0)
        gate = DefaultRiskGate(max_concurrent=2)
        block = gate.check(
            FakeSignal(symbol="GOOG", price=100.0),
            tracker=tracker,
            clock=_clock(datetime.now(timezone.utc)),
        )
        assert isinstance(block, RiskBlock)
        assert block.gate == "max_concurrent"
        assert block.meta["current"] == 2
        assert block.meta["limit"] == 2

    def test_allows_below_limit(self, tracker):
        _open(tracker, "AAPL", "long", 100, 100.0)
        gate = DefaultRiskGate(max_concurrent=2)
        assert gate.check(FakeSignal(price=100.0), tracker=tracker, clock=_clock(datetime.now(timezone.utc))) is None


class TestMaxPositionNotional:
    def test_blocks_above_limit(self, tracker):
        gate = DefaultRiskGate(max_position_notional=5_000.0)
        sig = FakeSignal(qty=100, price=100.0)  # 10k notional
        block = gate.check(sig, tracker=tracker, clock=_clock(datetime.now(timezone.utc)))
        assert isinstance(block, RiskBlock)
        assert block.gate == "max_position_notional"

    def test_allows_below_limit(self, tracker):
        gate = DefaultRiskGate(max_position_notional=20_000.0)
        sig = FakeSignal(qty=100, price=100.0)
        assert gate.check(sig, tracker=tracker, clock=_clock(datetime.now(timezone.utc))) is None

    def test_uses_stop_price_when_no_price(self, tracker):
        gate = DefaultRiskGate(max_position_notional=5_000.0)
        sig = FakeSignal(qty=100, stop_price=100.0)
        block = gate.check(sig, tracker=tracker, clock=_clock(datetime.now(timezone.utc)))
        assert isinstance(block, RiskBlock)

    def test_uses_meta_ref_price_for_market_orders(self, tracker):
        gate = DefaultRiskGate(max_position_notional=5_000.0)
        sig = FakeSignal(qty=100, meta={"ref_price": 100.0})
        block = gate.check(sig, tracker=tracker, clock=_clock(datetime.now(timezone.utc)))
        assert isinstance(block, RiskBlock)


class TestNoNewEntriesAfter:
    def test_blocks_after_cutoff(self, tracker):
        gate = DefaultRiskGate(no_new_entries_after=dtime(15, 0))
        now = datetime(2024, 1, 15, 15, 30, tzinfo=timezone.utc)
        block = gate.check(FakeSignal(price=100.0), tracker=tracker, clock=_clock(now))
        assert isinstance(block, RiskBlock)
        assert block.gate == "no_new_entries_after"

    def test_allows_before_cutoff(self, tracker):
        gate = DefaultRiskGate(no_new_entries_after=dtime(15, 0))
        now = datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc)
        assert gate.check(FakeSignal(price=100.0), tracker=tracker, clock=_clock(now)) is None


class TestPerSymbolMaxNotional:
    def test_blocks_when_total_exceeds(self, tracker):
        _open(tracker, "AAPL", "long", 50, 100.0)  # 5000 existing
        tracker.mark("AAPL", 100.0)
        gate = DefaultRiskGate(per_symbol_max_notional=8_000.0)
        sig = FakeSignal(symbol="AAPL", qty=50, price=100.0)  # +5000 = 10000
        block = gate.check(sig, tracker=tracker, clock=_clock(datetime.now(timezone.utc)))
        assert isinstance(block, RiskBlock)
        assert block.gate == "per_symbol_max_notional"

    def test_only_counts_same_symbol(self, tracker):
        _open(tracker, "MSFT", "long", 100, 100.0)
        tracker.mark("MSFT", 100.0)
        gate = DefaultRiskGate(per_symbol_max_notional=8_000.0)
        sig = FakeSignal(symbol="AAPL", qty=50, price=100.0)
        assert gate.check(sig, tracker=tracker, clock=_clock(datetime.now(timezone.utc))) is None


class TestGateOrdering:
    def test_first_failing_gate_wins(self, tracker):
        # Two gates would block; verify daily_loss_limit (first) reports.
        _open(tracker, "AAPL", "long", 100, 100.0)
        tracker.mark("AAPL", 80.0)  # -2000 P&L
        gate = DefaultRiskGate(
            daily_loss_limit=-500.0,
            max_concurrent=1,
        )
        block = gate.check(FakeSignal(price=100.0), tracker=tracker, clock=_clock(datetime.now(timezone.utc)))
        assert block is not None
        assert block.gate == "daily_loss_limit"
