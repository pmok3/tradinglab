"""Tests for the EntryEvaluator (the entries-v1 core)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from typing import Any, Dict, List, Optional

import pytest

from tradinglab.core import thread_guard
from tradinglab.core.risk_gate import DefaultRiskGate, RiskBlock
from tradinglab.entries.audit import AuditLog
from tradinglab.entries.evaluator import EntryEvaluator
from tradinglab.entries.model import (
    Direction,
    EntryStrategy,
    EntryTrigger,
    PositionAlreadyOpenPolicy,
    SizingKind,
    SizingRule,
    TriggerKind,
    Universe,
)
from tradinglab.entries.signals import (
    EntryOrderKind,
    EntryPaperSink,
    EntrySignal,
)
from tradinglab.exits.model import OrderSide
from tradinglab.exits.paper_engine import PaperBrokerEngine
from tradinglab.exits.spec import Bar
from tradinglab.positions.tracker import PositionTracker


@pytest.fixture(autouse=True)
def _no_tk():
    with thread_guard.tk_thread_check_disabled():
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bar(o, h, l, c, ts=None) -> Bar:
    return Bar(
        date=ts or datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc),
        open=float(o), high=float(h), low=float(l), close=float(c),
        volume=0,
    )


def _strategy(
    *,
    name: str = "test",
    direction: Direction = Direction.LONG,
    trigger_kind: TriggerKind = TriggerKind.MARKET,
    price: float | None = None,
    stop_price: float | None = None,
    scanner_id: str | None = None,
    qty: float = 100.0,
    universe_symbols=("AAPL",),
    universe_scanner_id: str | None = None,
    universe_from_chart: bool = False,
    on_fill_exit_ids=(),
    cooldown_secs: int = 0,
    max_per_symbol: int = 1,
    max_total: int | None = None,
    arm_window_start: str = "00:00",
    arm_window_end: str = "23:59",
    block_on_open: bool = True,
    enabled: bool = True,
) -> EntryStrategy:
    if universe_from_chart:
        universe = Universe(from_attached_chart=True)
    elif universe_scanner_id:
        universe = Universe(scanner_id=universe_scanner_id)
    else:
        universe = Universe(symbols=tuple(universe_symbols))
    trig = EntryTrigger(
        kind=trigger_kind, price=price, stop_price=stop_price,
        scanner_id=scanner_id,
    )
    sizing = SizingRule(kind=SizingKind.FIXED_QTY, qty=qty)
    policy = (
        PositionAlreadyOpenPolicy.BLOCK if block_on_open
        else PositionAlreadyOpenPolicy.STACK
    )
    return EntryStrategy(
        name=name,
        direction=direction,
        universe=universe,
        trigger=trig,
        sizing=sizing,
        on_fill_exit_ids=tuple(on_fill_exit_ids),
        enabled=enabled,
        cooldown_secs=cooldown_secs,
        max_fires_per_session_per_symbol=max_per_symbol,
        max_fires_per_session_total=max_total,
        position_already_open_policy=policy,
        arm_window_start=arm_window_start,
        arm_window_end=arm_window_end,
    )


def _build(
    *,
    risk_gate=None,
    bars_registry=None,
    scan_runner=None,
    exit_evaluator=None,
    exit_storage=None,
    get_active_symbol=None,
    audit=None,
):
    tracker = PositionTracker()
    engine = PaperBrokerEngine(tracker)
    sink = EntryPaperSink(engine)
    ev = EntryEvaluator(
        tracker=tracker,
        sink=sink,
        audit=audit,
        risk_gate=risk_gate,
        bars_registry=bars_registry,
        scan_runner=scan_runner,
        exit_evaluator=exit_evaluator,
        exit_storage=exit_storage,
        get_active_symbol=get_active_symbol,
    )
    return ev, tracker, engine, sink


# ---------------------------------------------------------------------------
# Library + arm state
# ---------------------------------------------------------------------------


class TestArmState:
    def test_set_strategies_replaces_library(self):
        ev, *_ = _build()
        s1 = _strategy(name="A")
        s2 = _strategy(name="B")
        ev.set_strategies([s1, s2])
        assert {s.name for s in ev.all_strategies()} == {"A", "B"}

    def test_arm_unknown_raises(self):
        ev, *_ = _build()
        with pytest.raises(KeyError):
            ev.arm("ghost")

    def test_arm_disabled_raises(self):
        ev, *_ = _build()
        s = _strategy(enabled=False)
        ev.set_strategies([s])
        with pytest.raises(ValueError, match="disabled"):
            ev.arm(s.id)

    def test_arm_invalid_universe_raises(self):
        ev, *_ = _build()
        # Empty universe → invalid (XOR fails).
        bad_universe = Universe()  # all empty
        s = EntryStrategy(
            name="bad", universe=bad_universe,
            trigger=EntryTrigger(kind=TriggerKind.MARKET),
            sizing=SizingRule(kind=SizingKind.FIXED_QTY, qty=10),
        )
        ev.set_strategies([s])
        with pytest.raises(ValueError, match="universe"):
            ev.arm(s.id)

    def test_disarm_idempotent(self):
        ev, *_ = _build()
        s = _strategy()
        ev.set_strategies([s])
        ev.arm(s.id)
        ev.disarm(s.id)
        ev.disarm(s.id)  # no error
        assert not ev.is_armed(s.id)

    def test_disarm_all(self):
        ev, *_ = _build()
        s1 = _strategy(name="A")
        s2 = _strategy(name="B")
        ev.set_strategies([s1, s2])
        ev.arm(s1.id)
        ev.arm(s2.id)
        ev.disarm_all()
        assert ev.armed_strategies() == set()

    def test_set_strategies_drops_armed_for_removed(self):
        ev, *_ = _build()
        s1 = _strategy(name="A")
        s2 = _strategy(name="B")
        ev.set_strategies([s1, s2])
        ev.arm(s1.id)
        ev.arm(s2.id)
        ev.set_strategies([s2])  # remove s1
        assert ev.is_armed(s2.id)
        assert not ev.is_armed(s1.id)


# ---------------------------------------------------------------------------
# Trigger eval — MARKET / LIMIT / STOP / STOP_LIMIT
# ---------------------------------------------------------------------------


class TestMarketTrigger:
    def test_market_fires_on_close(self):
        ev, tracker, engine, sink = _build()
        s = _strategy(trigger_kind=TriggerKind.MARKET)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99, 100.5, ts)}, ts)
        assert len(signals) == 1
        assert signals[0].kind == EntryOrderKind.MARKET
        assert signals[0].symbol == "AAPL"
        assert signals[0].qty == 100.0
        assert signals[0].position_side == "long"

    def test_market_does_not_fire_on_forming_bar(self):
        ev, *_ = _build()
        s = _strategy(trigger_kind=TriggerKind.MARKET)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick(
            {"AAPL": _bar(100, 101, 99, 100, ts)}, ts,
            last_bar_forming=True,
        )
        assert signals == []

    def test_short_market_uses_sell_side(self):
        ev, *_ = _build()
        s = _strategy(trigger_kind=TriggerKind.MARKET, direction=Direction.SHORT)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        assert signals[0].side == OrderSide.SELL
        assert signals[0].position_side == "short"


class TestLimitTrigger:
    def test_long_limit_fires_when_low_touches(self):
        ev, *_ = _build()
        s = _strategy(trigger_kind=TriggerKind.LIMIT, price=99.0)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        # Bar.low touches 98.5 ≤ 99 → fires.
        signals = ev.on_tick({"AAPL": _bar(100, 101, 98.5, 100, ts)}, ts)
        assert len(signals) == 1
        assert signals[0].kind == EntryOrderKind.LIMIT
        assert signals[0].price == 99.0

    def test_long_limit_no_fire_when_low_above(self):
        ev, *_ = _build()
        s = _strategy(trigger_kind=TriggerKind.LIMIT, price=99.0)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99.5, 100, ts)}, ts)
        assert signals == []

    def test_short_limit_fires_when_high_touches(self):
        ev, *_ = _build()
        s = _strategy(
            trigger_kind=TriggerKind.LIMIT, price=101.0,
            direction=Direction.SHORT,
        )
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 101.5, 99, 100, ts)}, ts)
        assert len(signals) == 1
        assert signals[0].side == OrderSide.SELL


class TestStopTrigger:
    def test_long_stop_fires_on_breakout(self):
        ev, *_ = _build()
        s = _strategy(trigger_kind=TriggerKind.STOP, stop_price=105.0)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 105.5, 99, 105, ts)}, ts)
        assert len(signals) == 1
        assert signals[0].kind == EntryOrderKind.STOP
        assert signals[0].price == 105.0

    def test_long_stop_no_fire_when_below(self):
        ev, *_ = _build()
        s = _strategy(trigger_kind=TriggerKind.STOP, stop_price=105.0)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 104, 99, 100, ts)}, ts)
        assert signals == []


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


class TestGates:
    def test_position_already_open_blocks(self):
        ev, tracker, *_ = _build()
        s = _strategy()
        ev.set_strategies([s])
        ev.arm(s.id)
        # Pre-existing open position with the SAME strategy_id → block.
        tracker.open(
            symbol="AAPL", side="long", qty=10, price=100.0,
            source="manual", strategy_id=s.id,
        )
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        assert signals == []
        assert ev.stats().blocked >= 1

    def test_position_open_for_other_strategy_does_not_block(self):
        """A's position doesn't block B's entries."""
        ev, tracker, *_ = _build()
        s = _strategy()
        ev.set_strategies([s])
        ev.arm(s.id)
        tracker.open(
            symbol="AAPL", side="long", qty=10, price=100.0,
            source="manual", strategy_id="other-strat",
        )
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        assert len(signals) == 1

    def test_cooldown_blocks_within_window(self):
        ev, *_ = _build()
        s = _strategy(cooldown_secs=60, max_per_symbol=10)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts1 = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts1)}, ts1)
        # 30 seconds later — within cooldown.
        ts2 = ts1 + timedelta(seconds=30)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts2)}, ts2)
        assert signals == []
        assert ev.stats().cooldowns >= 1

    def test_cooldown_clears_after_window(self):
        ev, *_ = _build()
        s = _strategy(cooldown_secs=60, max_per_symbol=10)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts1 = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts1)}, ts1)
        ts2 = ts1 + timedelta(seconds=61)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts2)}, ts2)
        assert len(signals) == 1

    def test_max_fires_per_symbol(self):
        ev, *_ = _build()
        s = _strategy(
            max_per_symbol=1, block_on_open=False,  # so we can re-fire
        )
        ev.set_strategies([s])
        ev.arm(s.id)
        ts1 = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        ts2 = ts1 + timedelta(minutes=5)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts1)}, ts1)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts2)}, ts2)
        assert signals == []  # blocked by max_per_symbol

    def test_max_fires_per_session_total(self):
        ev, *_ = _build()
        s = _strategy(
            max_per_symbol=10, max_total=2, block_on_open=False,
            universe_symbols=("AAPL", "MSFT"),
        )
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        bars = {
            "AAPL": _bar(100, 101, 99, 100, ts),
            "MSFT": _bar(100, 101, 99, 100, ts),
        }
        # First tick fires both (2 total).
        s1 = ev.on_tick(bars, ts)
        # Second tick: total already at 2 → blocked.
        ts2 = ts + timedelta(minutes=5)
        bars2 = {
            "AAPL": _bar(100, 101, 99, 100, ts2),
            "MSFT": _bar(100, 101, 99, 100, ts2),
        }
        s2 = ev.on_tick(bars2, ts2)
        assert len(s1) == 2
        assert s2 == []

    def test_dedup_same_bar_same_strategy(self):
        ev, *_ = _build()
        s = _strategy(max_per_symbol=10, block_on_open=False)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        bar = _bar(100, 101, 99, 100, ts)
        s1 = ev.on_tick({"AAPL": bar}, ts)
        s2 = ev.on_tick({"AAPL": bar}, ts)
        assert len(s1) == 1
        assert s2 == []
        assert ev.stats().dedup_skips >= 1

    def test_arm_window_blocks_outside(self):
        ev, *_ = _build()
        s = _strategy(arm_window_start="09:35", arm_window_end="15:30")
        ev.set_strategies([s])
        ev.arm(s.id)
        # 08:00 UTC is outside 09:35-15:30 (treated as same-clock).
        ts = datetime(2024, 1, 15, 8, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        assert signals == []


class TestRiskGate:
    def test_risk_gate_blocks(self):
        # max_position_notional=$1000; sizing 100 shares × close $100 = $10000
        gate = DefaultRiskGate(max_position_notional=1000.0)
        ev, *_ = _build(risk_gate=gate)
        s = _strategy()  # qty=100
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        assert signals == []
        assert ev.stats().risk_blocks >= 1


# ---------------------------------------------------------------------------
# Universe resolution
# ---------------------------------------------------------------------------


class TestUniverse:
    def test_symbols_universe(self):
        ev, *_ = _build()
        s = _strategy(universe_symbols=("AAPL", "MSFT"))
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick(
            {
                "AAPL": _bar(100, 101, 99, 100, ts),
                "MSFT": _bar(200, 201, 199, 200, ts),
                "TSLA": _bar(50, 51, 49, 50, ts),  # not in universe
            },
            ts,
        )
        symbols = {sg.symbol for sg in signals}
        assert symbols == {"AAPL", "MSFT"}

    def test_from_attached_chart_universe(self):
        active_sym = ["AAPL"]
        ev, *_ = _build(get_active_symbol=lambda: active_sym[0])
        s = _strategy(universe_from_chart=True)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        # Only AAPL fires.
        signals = ev.on_tick(
            {"AAPL": _bar(100, 101, 99, 100, ts), "MSFT": _bar(200, 201, 199, 200, ts)},
            ts,
        )
        assert {sg.symbol for sg in signals} == {"AAPL"}

    def test_from_attached_chart_no_active_symbol(self):
        ev, *_ = _build(get_active_symbol=lambda: None)
        s = _strategy(universe_from_chart=True)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        assert signals == []

    def test_scanner_id_universe_no_fire_for_non_scanner_alert(self):
        """scanner_id universe with non-SCANNER_ALERT trigger → no fires."""
        ev, *_ = _build()
        s = _strategy(
            trigger_kind=TriggerKind.MARKET,
            universe_scanner_id="my_scanner",
            universe_symbols=(),
        )
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        assert signals == []


# ---------------------------------------------------------------------------
# Scanner alert path
# ---------------------------------------------------------------------------


class _FakeRow:
    def __init__(self, symbol, close=100.0, evidence=()):
        self.symbol = symbol
        self.metrics = {"close": close}
        self.evidence = list(evidence)


class _FakeScanResult:
    def __init__(self, new_rows):
        self.new_rows = list(new_rows)


class _FakeScanRunner:
    def __init__(self):
        self._subs: list = []

    def subscribe(self, callback):
        self._subs.append(callback)
        return lambda: self._subs.remove(callback)

    def emit(self, results):
        for cb in list(self._subs):
            cb(results)


class TestScannerAlert:
    def test_scanner_alert_fires(self):
        runner = _FakeScanRunner()
        ev, tracker, engine, sink = _build(scan_runner=runner)
        s = _strategy(
            trigger_kind=TriggerKind.SCANNER_ALERT,
            scanner_id="scan-1",
            universe_symbols=("AAPL", "MSFT"),
        )
        ev.set_strategies([s])
        ev.arm(s.id)
        runner.emit({"scan-1": _FakeScanResult([_FakeRow("AAPL", close=150.0)])})
        # Pending order should have been submitted.
        pending = engine.pending_orders_for_symbol("AAPL")
        assert len(pending) == 1

    def test_scanner_alert_filters_by_universe_symbols(self):
        runner = _FakeScanRunner()
        ev, tracker, engine, sink = _build(scan_runner=runner)
        s = _strategy(
            trigger_kind=TriggerKind.SCANNER_ALERT,
            scanner_id="scan-1",
            universe_symbols=("AAPL",),
        )
        ev.set_strategies([s])
        ev.arm(s.id)
        runner.emit(
            {
                "scan-1": _FakeScanResult(
                    [_FakeRow("AAPL"), _FakeRow("MSFT")]
                )
            }
        )
        # AAPL is in universe; MSFT is filtered.
        assert len(engine.pending_orders_for_symbol("AAPL")) == 1
        assert engine.pending_orders_for_symbol("MSFT") == []

    def test_scanner_id_universe_passes_all_rows(self):
        runner = _FakeScanRunner()
        ev, tracker, engine, sink = _build(scan_runner=runner)
        s = _strategy(
            trigger_kind=TriggerKind.SCANNER_ALERT,
            scanner_id="scan-1",
            universe_symbols=(),
            universe_scanner_id="scan-1",
        )
        ev.set_strategies([s])
        ev.arm(s.id)
        runner.emit(
            {
                "scan-1": _FakeScanResult(
                    [_FakeRow("AAPL"), _FakeRow("MSFT")]
                )
            }
        )
        assert len(engine.pending_orders_for_symbol("AAPL")) == 1
        assert len(engine.pending_orders_for_symbol("MSFT")) == 1

    def test_unsubscribe_on_close(self):
        runner = _FakeScanRunner()
        ev, *_ = _build(scan_runner=runner)
        assert len(runner._subs) == 1
        ev.close()
        assert runner._subs == []


# ---------------------------------------------------------------------------
# On-fill bracket bind chain
# ---------------------------------------------------------------------------


class _FakeExitStorage:
    def __init__(self, lib: dict[str, Any]):
        self._lib = lib

    def load(self, sid):
        return self._lib.get(sid)


class _FakeExitEvaluator:
    def __init__(self):
        self.attached: list = []

    def attach_strategy(self, position_id, strategy):
        self.attached.append((position_id, strategy))


class TestOnFillBind:
    def test_modal_request_when_no_bracket_ids(self):
        ev, tracker, engine, sink = _build()
        s = _strategy(on_fill_exit_ids=())
        ev.set_strategies([s])
        ev.arm(s.id)

        modal_calls: list = []
        ev.subscribe_modal_request(lambda pid, strat: modal_calls.append((pid, strat)))

        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        # Trigger the engine fill.
        engine.on_bar_for_pending(
            "AAPL", _bar(100, 101, 99, 100, ts), is_close=True,
        )
        assert len(modal_calls) == 1
        pid, strat = modal_calls[0]
        assert strat.id == s.id
        assert tracker.get(pid) is not None

    def test_bracket_bind_calls_exit_evaluator(self):
        # Fake exit strategy lookup.
        fake_exit = object()
        exit_storage = _FakeExitStorage({"exit-1": fake_exit})
        exit_evaluator = _FakeExitEvaluator()
        ev, tracker, engine, sink = _build(
            exit_storage=exit_storage,
            exit_evaluator=exit_evaluator,
        )
        s = _strategy(on_fill_exit_ids=("exit-1",))
        ev.set_strategies([s])
        ev.arm(s.id)

        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        engine.on_bar_for_pending(
            "AAPL", _bar(100, 101, 99, 100, ts), is_close=True,
        )
        assert len(exit_evaluator.attached) == 1
        pid, strat = exit_evaluator.attached[0]
        assert strat is fake_exit
        assert ev.stats().on_fill_binds == 1

    def test_bracket_bind_missing_exit_id_logs_failure(self):
        exit_storage = _FakeExitStorage({})  # exit-1 missing
        exit_evaluator = _FakeExitEvaluator()
        ev, tracker, engine, sink = _build(
            exit_storage=exit_storage,
            exit_evaluator=exit_evaluator,
        )
        s = _strategy(on_fill_exit_ids=("exit-1",))
        ev.set_strategies([s])
        ev.arm(s.id)

        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        engine.on_bar_for_pending(
            "AAPL", _bar(100, 101, 99, 100, ts), is_close=True,
        )
        # No attach happened.
        assert exit_evaluator.attached == []
        assert ev.stats().on_fill_bind_failures == 1

    def test_partial_bind_one_good_one_missing(self):
        fake_exit = object()
        exit_storage = _FakeExitStorage({"good": fake_exit})  # bad-id missing
        exit_evaluator = _FakeExitEvaluator()
        ev, tracker, engine, sink = _build(
            exit_storage=exit_storage,
            exit_evaluator=exit_evaluator,
        )
        s = _strategy(on_fill_exit_ids=("good", "bad-id"))
        ev.set_strategies([s])
        ev.arm(s.id)

        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        engine.on_bar_for_pending(
            "AAPL", _bar(100, 101, 99, 100, ts), is_close=True,
        )
        assert len(exit_evaluator.attached) == 1
        assert ev.stats().on_fill_binds == 1
        assert ev.stats().on_fill_bind_failures == 1

    def test_bind_failure_when_no_exit_evaluator_configured(self):
        ev, tracker, engine, sink = _build()  # no exit evaluator
        s = _strategy(on_fill_exit_ids=("exit-1",))
        ev.set_strategies([s])
        ev.arm(s.id)

        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        engine.on_bar_for_pending(
            "AAPL", _bar(100, 101, 99, 100, ts), is_close=True,
        )
        assert ev.stats().on_fill_bind_failures == 1


# ---------------------------------------------------------------------------
# Lifecycle: reset_session, session counter rollover, close()
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_reset_session_clears_counters(self):
        ev, *_ = _build()
        s = _strategy(max_per_symbol=1, block_on_open=False)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        # Without reset_session, max_per_symbol blocks the next fire.
        ts2 = ts + timedelta(minutes=5)
        s2 = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts2)}, ts2)
        assert s2 == []
        ev.reset_session()
        ts3 = ts + timedelta(minutes=10)
        s3 = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts3)}, ts3)
        assert len(s3) == 1

    def test_counter_rolls_over_at_date_boundary(self):
        ev, *_ = _build()
        s = _strategy(max_per_symbol=1, block_on_open=False)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts1 = datetime(2024, 1, 15, 23, 50, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts1)}, ts1)
        ts2 = datetime(2024, 1, 16, 0, 5, tzinfo=timezone.utc)  # next UTC day
        s2 = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts2)}, ts2)
        assert len(s2) == 1

    def test_close_unsubscribes_tracker(self):
        ev, tracker, *_ = _build()
        # Subscriber count is private; just verify close() is idempotent.
        ev.close()
        ev.close()  # no error


# ---------------------------------------------------------------------------
# Pending tracking
# ---------------------------------------------------------------------------


class TestPendingTracking:
    def test_pending_position_id_recorded(self):
        ev, *_ = _build()
        s = _strategy()
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        signals = ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        pending = ev.pending_position_ids()
        assert signals[0].pending_position_id in pending

    def test_pending_cleared_after_fill(self):
        ev, tracker, engine, sink = _build()
        s = _strategy()
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        engine.on_bar_for_pending(
            "AAPL", _bar(100, 101, 99, 100, ts), is_close=True,
        )
        assert ev.pending_position_ids() == {}


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAudit:
    def test_audit_records_arm_fire_submit(self, tmp_path):
        audit = AuditLog(tmp_path)
        ev, *_ = _build(audit=audit)
        s = _strategy()
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        recs = audit.tail(20)
        kinds = [r["kind"] for r in recs]
        assert "entry_arm" in kinds
        assert "entry_fire" in kinds
        assert "entry_submit" in kinds

    def test_audit_records_blocked_reason(self, tmp_path):
        audit = AuditLog(tmp_path)
        ev, tracker, *_ = _build(audit=audit)
        s = _strategy()
        ev.set_strategies([s])
        ev.arm(s.id)
        tracker.open(
            symbol="AAPL", side="long", qty=10, price=100.0,
            source="manual", strategy_id=s.id,
        )
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        recs = audit.tail(20)
        blocked = [r for r in recs if r["kind"] == "entry_blocked"]
        assert blocked
        assert blocked[0]["meta"]["reason"] == "position_already_open"


# ---------------------------------------------------------------------------
# Within-last-N-bars evidence threading (Phase 7)
# ---------------------------------------------------------------------------


class _CapturingSink:
    """Minimal :class:`EntrySignalSink` that just records the signal."""

    def __init__(self) -> None:
        self.signals: list[EntrySignal] = []
        self._n = 0

    def submit(self, signal):
        self.signals.append(signal)
        self._n += 1
        return f"capture-{self._n}"

    def cancel(self, order_id):  # pragma: no cover - unused
        return False

    def cancel_all_pending_for_symbol(self, symbol):  # pragma: no cover
        return 0

    def cancel_all_pending(self):  # pragma: no cover - unused
        return 0


def _build_with_sink(*, sink, scan_runner=None):
    tracker = PositionTracker()
    ev = EntryEvaluator(
        tracker=tracker,
        sink=sink,
        audit=None,
        risk_gate=None,
        bars_registry=None,
        scan_runner=scan_runner,
        exit_evaluator=None,
        exit_storage=None,
        get_active_symbol=None,
    )
    return ev, tracker


class TestLookbackEvidenceThreading:
    """Phase 7 — entries evaluator threads MatchEvidence into signal.extra."""

    def test_scanner_alert_no_evidence_omits_key(self):
        # Row without evidence must not pollute signal.extra with empty list.
        from tradinglab.scanner.model import MatchEvidence  # noqa: F401

        runner = _FakeScanRunner()
        sink = _CapturingSink()
        ev, _ = _build_with_sink(sink=sink, scan_runner=runner)
        s = _strategy(
            trigger_kind=TriggerKind.SCANNER_ALERT,
            scanner_id="scan-1",
            universe_symbols=("AAPL",),
        )
        ev.set_strategies([s])
        ev.arm(s.id)
        runner.emit({"scan-1": _FakeScanResult([_FakeRow("AAPL")])})
        assert len(sink.signals) == 1
        sig = sink.signals[0]
        assert "evidence" not in sig.extra
        assert sig.extra.get("ref_price") == 100.0

    def test_scanner_alert_evidence_threaded_into_signal_extra(self):
        from tradinglab.scanner.model import MatchEvidence

        runner = _FakeScanRunner()
        sink = _CapturingSink()
        ev, _ = _build_with_sink(sink=sink, scan_runner=runner)
        s = _strategy(
            trigger_kind=TriggerKind.SCANNER_ALERT,
            scanner_id="scan-1",
            universe_symbols=("AAPL",),
        )
        ev.set_strategies([s])
        ev.arm(s.id)
        ev1 = MatchEvidence(
            node_id="cond-ema-cross",
            bars_ago=1,
            timestamp="2024-01-15T10:35:00",
            value=180.5,
        )
        ev2 = MatchEvidence(
            node_id="cond-red-bar",
            bars_ago=0,
            timestamp="2024-01-15T10:40:00",
            value=None,
        )
        row = _FakeRow("AAPL", evidence=[ev1, ev2])
        runner.emit({"scan-1": _FakeScanResult([row])})

        assert len(sink.signals) == 1
        sig = sink.signals[0]
        assert "evidence" in sig.extra
        ev_list = sig.extra["evidence"]
        assert isinstance(ev_list, list) and len(ev_list) == 2
        # Serialized to plain dicts so the signal stays JSON-safe.
        assert ev_list[0] == {
            "node_id": "cond-ema-cross",
            "bars_ago": 1,
            "timestamp": "2024-01-15T10:35:00",
            "value": 180.5,
        }
        assert ev_list[1] == {
            "node_id": "cond-red-bar",
            "bars_ago": 0,
            "timestamp": "2024-01-15T10:40:00",
            "value": None,
        }

    def test_market_trigger_emits_no_evidence(self):
        sink = _CapturingSink()
        ev, _ = _build_with_sink(sink=sink)
        s = _strategy(trigger_kind=TriggerKind.MARKET)
        ev.set_strategies([s])
        ev.arm(s.id)
        ts = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)
        ev.on_tick({"AAPL": _bar(100, 101, 99, 100, ts)}, ts)
        assert len(sink.signals) == 1
        sig = sink.signals[0]
        assert "evidence" not in sig.extra
