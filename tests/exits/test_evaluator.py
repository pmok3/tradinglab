"""Unit tests for ``tradinglab.exits.evaluator``."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from tradinglab.core.thread_guard import (
    TkThreadViolation,
)
from tradinglab.exits import audit as audit_mod
from tradinglab.exits.audit import AuditLog
from tradinglab.exits.evaluator import EvaluatorStats, ExitEvaluator
from tradinglab.exits.model import (
    ActivationUnit,
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
    OCOGroup,
    TrailBasis,
    TrailUnit,
    TriggerKind,
)
from tradinglab.exits.signals import (
    ExitOrderKind,
    ExitSignal,
    ExitSignalSink,
    SchwabTraderNotConfigured,
)
from tradinglab.exits.spec import Bar
from tradinglab.positions.model import Position
from tradinglab.positions.tracker import PositionTracker


# ---------------------------------------------------------------------------
# Test sink
# ---------------------------------------------------------------------------


@dataclass
class _RecordedCall:
    method: str
    arg: Any


class _RecordingSink:
    """Captures every signal/cancel call.

    Mirrors the :class:`ExitSignalSink` Protocol; tests inspect the
    ``submitted`` / ``cancelled_ids`` lists to assert the evaluator
    drove the sink correctly. Optional ``raise_on_submit`` makes
    the next ``submit`` raise to test the broken-trigger path.
    """

    def __init__(self) -> None:
        self.submitted: List[ExitSignal] = []
        self.cancelled_ids: List[str] = []
        self.cancel_all_calls: List[str] = []
        self._next = 0
        self._working: Dict[str, str] = {}  # order_id -> position_id
        self.raise_on_submit: bool = False
        self.raise_on_submit_message: str = "sink failure"

    def submit(self, signal: ExitSignal) -> str:
        if self.raise_on_submit:
            raise RuntimeError(self.raise_on_submit_message)
        oid = f"order-{self._next}"
        self._next += 1
        self.submitted.append(signal)
        self._working[oid] = signal.position_id
        return oid

    def cancel(self, order_id: str) -> bool:
        self.cancelled_ids.append(order_id)
        if order_id in self._working:
            del self._working[order_id]
            return True
        return False

    def cancel_all_for_position(self, position_id: str) -> int:
        self.cancel_all_calls.append(position_id)
        n = 0
        for oid, pid in list(self._working.items()):
            if pid == position_id:
                del self._working[oid]
                n += 1
        return n

    def working_order_ids_for_position(self, position_id: str) -> List[str]:
        return [oid for oid, pid in self._working.items() if pid == position_id]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def audit_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setattr("tradinglab.disk_cache._cache_dir", lambda: root)
    monkeypatch.setattr(audit_mod, "_cache_dir", lambda: root)
    return root / "exits" / "audit"


@pytest.fixture
def tracker() -> PositionTracker:
    return PositionTracker()


@pytest.fixture
def sink() -> _RecordingSink:
    return _RecordingSink()


@pytest.fixture
def evaluator(
    tracker: PositionTracker,
    sink: _RecordingSink,
    audit_root: Path,
) -> ExitEvaluator:
    audit = AuditLog()
    evlt = ExitEvaluator(tracker=tracker, sink=sink, audit=audit)
    yield evlt
    evlt.close()
    audit.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_long(tracker: PositionTracker, *, symbol: str = "AAPL", qty: float = 100.0,
               price: float = 180.0) -> Position:
    return tracker.open(
        symbol=symbol, side="long", qty=qty, price=price, source="sandbox"
    )


def _open_short(tracker: PositionTracker, *, symbol: str = "AAPL", qty: float = 100.0,
                price: float = 180.0) -> Position:
    return tracker.open(
        symbol=symbol, side="short", qty=qty, price=price, source="sandbox"
    )


def _bar(o: float, h: float, l: float, c: float, *, ts: Optional[datetime] = None) -> Bar:
    return Bar(open=o, high=h, low=l, close=c, volume=0.0, date=ts)


def _make_strategy(legs: List[ExitLeg], *, eod_kill: bool = False,
                    eod_offset_min: int = 5,
                    oco_groups: Optional[List[OCOGroup]] = None) -> ExitStrategy:
    return ExitStrategy(
        name="test",
        legs=legs,
        oco_groups=list(oco_groups or []),
        eod_kill_switch=eod_kill,
        eod_offset_min=eod_offset_min,
    )


def _market_leg(label: str = "exit") -> ExitLeg:
    return ExitLeg(
        label=label,
        triggers=[ExitTrigger(kind=TriggerKind.MARKET)],
    )


def _stop_leg(price: float, *, label: str = "stop") -> ExitLeg:
    return ExitLeg(
        label=label,
        triggers=[ExitTrigger(kind=TriggerKind.STOP, price=price)],
    )


def _limit_leg(price: float, *, label: str = "target") -> ExitLeg:
    return ExitLeg(
        label=label,
        triggers=[ExitTrigger(kind=TriggerKind.LIMIT, price=price)],
    )


def _trailing_leg(*, trail_pct: float, basis: TrailBasis = TrailBasis.CLOSE,
                   activation_unit: Optional[ActivationUnit] = None,
                   activation_value: Optional[float] = None,
                   label: str = "trail") -> ExitLeg:
    return ExitLeg(
        label=label,
        triggers=[ExitTrigger(
            kind=TriggerKind.TRAILING_STOP,
            trail_unit=TrailUnit.PERCENT,
            trail_value=trail_pct,
            trail_basis=basis,
            activation_unit=activation_unit,
            activation_value=activation_value,
        )],
    )


# ---------------------------------------------------------------------------
# Attach / detach (4)
# ---------------------------------------------------------------------------


def test_attach_strategy_arms_all_legs(evaluator: ExitEvaluator,
                                          tracker: PositionTracker) -> None:
    pos = _open_long(tracker)
    strat = _make_strategy([_stop_leg(170.0), _limit_leg(190.0)])
    evaluator.attach_strategy(pos.id, strat)
    assert evaluator.is_attached(pos.id)
    assert evaluator.attached_strategy(pos.id) is strat
    # Both legs armed
    for leg in strat.legs:
        slot = evaluator.trigger_state(pos.id, leg.id, leg.triggers[0].id)
        assert slot is not None
        assert slot.armed is True


def test_attach_unknown_position_raises(evaluator: ExitEvaluator) -> None:
    strat = _make_strategy([_market_leg()])
    with pytest.raises(KeyError):
        evaluator.attach_strategy("no-such-pos", strat)


def test_detach_strategy_disarms_and_cancels(evaluator: ExitEvaluator,
                                                tracker: PositionTracker,
                                                sink: _RecordingSink) -> None:
    pos = _open_long(tracker)
    strat = _make_strategy([_stop_leg(170.0)])
    evaluator.attach_strategy(pos.id, strat)
    # Drive a fire so there's something to cancel
    bar = _bar(180.0, 180.0, 169.0, 175.0)  # low pierces stop
    evaluator.on_bar(pos.id, bar)
    assert len(sink.submitted) == 1
    # Detach — should still cancel via cancel_all_for_position
    assert evaluator.detach_strategy(pos.id) is True
    assert pos.id in sink.cancel_all_calls
    assert evaluator.is_attached(pos.id) is False


def test_detach_returns_false_when_not_attached(evaluator: ExitEvaluator,
                                                   tracker: PositionTracker) -> None:
    pos = _open_long(tracker)
    assert evaluator.detach_strategy(pos.id) is False


def test_attach_replaces_previous_strategy(evaluator: ExitEvaluator,
                                              tracker: PositionTracker,
                                              sink: _RecordingSink) -> None:
    pos = _open_long(tracker)
    strat1 = _make_strategy([_stop_leg(170.0)])
    strat2 = _make_strategy([_stop_leg(165.0)])
    evaluator.attach_strategy(pos.id, strat1)
    evaluator.attach_strategy(pos.id, strat2)
    assert evaluator.attached_strategy(pos.id) is strat2
    # Original strategy's cancel_all should have been called on auto-replace
    assert pos.id in sink.cancel_all_calls


# ---------------------------------------------------------------------------
# on_bar — no-op cases (3)
# ---------------------------------------------------------------------------


def test_on_bar_no_strategy_attached_returns_empty(evaluator: ExitEvaluator,
                                                       tracker: PositionTracker) -> None:
    pos = _open_long(tracker)
    assert evaluator.on_bar(pos.id, _bar(180, 181, 179, 180)) == []


def test_on_bar_after_detach_returns_empty(evaluator: ExitEvaluator,
                                              tracker: PositionTracker) -> None:
    pos = _open_long(tracker)
    evaluator.attach_strategy(pos.id, _make_strategy([_stop_leg(170.0)]))
    evaluator.detach_strategy(pos.id)
    assert evaluator.on_bar(pos.id, _bar(180, 180, 165, 167)) == []


def test_on_bar_unknown_position_returns_empty(evaluator: ExitEvaluator) -> None:
    assert evaluator.on_bar("no-such-pos", _bar(180, 181, 179, 180)) == []


# ---------------------------------------------------------------------------
# Native triggers — fire semantics (8)
# ---------------------------------------------------------------------------


def test_market_trigger_fires_immediately(evaluator: ExitEvaluator,
                                              tracker: PositionTracker,
                                              sink: _RecordingSink) -> None:
    pos = _open_long(tracker)
    evaluator.attach_strategy(pos.id, _make_strategy([_market_leg()]))
    fired = evaluator.on_bar(pos.id, _bar(180, 180, 180, 180))
    assert len(fired) == 1
    assert fired[0].kind == ExitOrderKind.MARKET
    assert fired[0].qty == 100.0
    assert sink.submitted == fired


def test_limit_trigger_fires_when_high_touches(evaluator: ExitEvaluator,
                                                   tracker: PositionTracker) -> None:
    pos = _open_long(tracker)
    evaluator.attach_strategy(pos.id, _make_strategy([_limit_leg(190.0)]))
    fired = evaluator.on_bar(pos.id, _bar(189, 191, 188, 189))
    assert len(fired) == 1
    assert fired[0].kind == ExitOrderKind.LIMIT
    assert fired[0].price == 190.0


def test_limit_trigger_no_fire_when_no_touch(evaluator: ExitEvaluator,
                                                  tracker: PositionTracker) -> None:
    pos = _open_long(tracker)
    evaluator.attach_strategy(pos.id, _make_strategy([_limit_leg(195.0)]))
    fired = evaluator.on_bar(pos.id, _bar(189, 191, 188, 189))
    assert fired == []


def test_stop_trigger_fires_when_low_pierces(evaluator: ExitEvaluator,
                                                 tracker: PositionTracker) -> None:
    pos = _open_long(tracker)
    evaluator.attach_strategy(pos.id, _make_strategy([_stop_leg(170.0)]))
    fired = evaluator.on_bar(pos.id, _bar(180, 180, 169, 175))
    assert len(fired) == 1
    assert fired[0].kind == ExitOrderKind.STOP


def test_stop_trigger_short_fires_when_high_pierces(evaluator: ExitEvaluator,
                                                         tracker: PositionTracker) -> None:
    pos = _open_short(tracker)
    leg = ExitLeg(label="cover-stop",
                   triggers=[ExitTrigger(kind=TriggerKind.STOP, price=190.0)])
    evaluator.attach_strategy(pos.id, _make_strategy([leg]))
    fired = evaluator.on_bar(pos.id, _bar(180, 191, 180, 185))
    assert len(fired) == 1
    assert fired[0].side.value == "buy"


def test_trigger_fires_only_once_per_bar(evaluator: ExitEvaluator,
                                              tracker: PositionTracker,
                                              sink: _RecordingSink) -> None:
    pos = _open_long(tracker)
    evaluator.attach_strategy(pos.id, _make_strategy([_market_leg()]))
    bar = _bar(180, 180, 180, 180, ts=datetime(2025, 1, 15, 14, 0, tzinfo=timezone.utc))
    evaluator.on_bar(pos.id, bar)
    # Re-evaluating the same bar should NOT re-fire (trigger now disarmed)
    evaluator.on_bar(pos.id, bar)
    assert len(sink.submitted) == 1


def test_disabled_leg_does_not_fire(evaluator: ExitEvaluator,
                                       tracker: PositionTracker) -> None:
    pos = _open_long(tracker)
    leg = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET)], enabled=False)
    evaluator.attach_strategy(pos.id, _make_strategy([leg]))
    assert evaluator.on_bar(pos.id, _bar(180, 180, 180, 180)) == []


def test_disabled_trigger_does_not_fire(evaluator: ExitEvaluator,
                                           tracker: PositionTracker) -> None:
    pos = _open_long(tracker)
    leg = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET, enabled=False)])
    evaluator.attach_strategy(pos.id, _make_strategy([leg]))
    assert evaluator.on_bar(pos.id, _bar(180, 180, 180, 180)) == []


# ---------------------------------------------------------------------------
# Trailing stop (4)
# ---------------------------------------------------------------------------


def test_trailing_stop_hwm_updates_and_fires(evaluator: ExitEvaluator,
                                                  tracker: PositionTracker) -> None:
    pos = _open_long(tracker, price=180.0)
    leg = _trailing_leg(trail_pct=2.0)  # 2% trailing
    evaluator.attach_strategy(pos.id, _make_strategy([leg]))
    # Bar 1: price climbs to 190; HWM=190; trail_price = 190 * 0.98 = 186.2
    # Use a tight bar (low=188 > 186.2) so the trail does NOT fire on bar 1.
    evaluator.on_bar(pos.id, _bar(180, 190, 188, 190),
                       interval="1m")
    slot = evaluator.trigger_state(pos.id, leg.id, leg.triggers[0].id)
    assert slot.state.hwm == 190.0
    assert slot.armed is True
    # Bar 2: price drops to 185 (below 186.2 trail) → fire
    fired = evaluator.on_bar(pos.id, _bar(190, 190, 185, 185))
    assert len(fired) == 1
    assert fired[0].kind == ExitOrderKind.MARKET


def test_trailing_stop_with_activation_gate(evaluator: ExitEvaluator,
                                                tracker: PositionTracker) -> None:
    pos = _open_long(tracker, price=180.0)
    # Activation: 5% above entry (i.e. 189) before trail arms
    leg = _trailing_leg(
        trail_pct=2.0,
        activation_unit=ActivationUnit.PERCENT,
        activation_value=5.0,
    )
    evaluator.attach_strategy(pos.id, _make_strategy([leg]))
    # Bar 1: price up to 184 — below 189 activation; trail not active
    evaluator.on_bar(pos.id, _bar(180, 184, 180, 184))
    # Bar 2: price drops back to 175 — would be fire if activated, but isn't
    fired = evaluator.on_bar(pos.id, _bar(184, 184, 175, 175))
    assert fired == []


def test_trailing_stop_ratchet_does_not_loosen(evaluator: ExitEvaluator,
                                                   tracker: PositionTracker) -> None:
    pos = _open_long(tracker, price=180.0)
    leg = _trailing_leg(trail_pct=2.0)
    evaluator.attach_strategy(pos.id, _make_strategy([leg]))
    evaluator.on_bar(pos.id, _bar(180, 195, 180, 195))
    slot = evaluator.trigger_state(pos.id, leg.id, leg.triggers[0].id)
    high_trail = slot.state.trail_price
    # Price drops but doesn't pierce trail
    evaluator.on_bar(pos.id, _bar(195, 195, 192, 192))
    slot2 = evaluator.trigger_state(pos.id, leg.id, leg.triggers[0].id)
    # Trail should be unchanged (ratchet)
    assert slot2.state.trail_price == high_trail


def test_trailing_stop_short_position(evaluator: ExitEvaluator,
                                          tracker: PositionTracker) -> None:
    pos = _open_short(tracker, price=180.0)
    leg = _trailing_leg(trail_pct=2.0)
    evaluator.attach_strategy(pos.id, _make_strategy([leg]))
    # Bar 1: price drops to 170 → favorable for short, LWM=170, trail=173.4
    # Use bar.high=173 < 173.4 so bar 1 doesn't fire prematurely.
    evaluator.on_bar(pos.id, _bar(173, 173, 170, 170))
    # Bar 2: price rises to 174 (above 170 * 1.02 = 173.4) → fire
    fired = evaluator.on_bar(pos.id, _bar(170, 174, 170, 174))
    assert len(fired) == 1


# ---------------------------------------------------------------------------
# OCO behavior (5)
# ---------------------------------------------------------------------------


def test_oco_any_fire_cancels_siblings(evaluator: ExitEvaluator,
                                           tracker: PositionTracker,
                                           sink: _RecordingSink) -> None:
    pos = _open_long(tracker)
    stop = _stop_leg(170.0, label="hard-stop")
    target = _limit_leg(195.0, label="target")
    strat = _make_strategy(
        [stop, target],
        oco_groups=[OCOGroup(leg_ids=(stop.id, target.id), cancel_on="any_fire")],
    )
    evaluator.attach_strategy(pos.id, strat)
    # Limit fires (target hit)
    evaluator.on_bar(pos.id, _bar(190, 196, 188, 195))
    # The stop leg should now be disarmed (sibling cancelled inline)
    stop_slot = evaluator.trigger_state(pos.id, stop.id, stop.triggers[0].id)
    assert stop_slot.armed is False


def test_oco_full_closeout_defers_cancel_until_qty_zero(
    evaluator: ExitEvaluator,
    tracker: PositionTracker,
    sink: _RecordingSink,
) -> None:
    pos = _open_long(tracker, qty=100.0)
    stop = _stop_leg(170.0)
    target = _limit_leg(195.0)
    strat = _make_strategy(
        [stop, target],
        oco_groups=[OCOGroup(leg_ids=(stop.id, target.id),
                              cancel_on="full_closeout")],
    )
    evaluator.attach_strategy(pos.id, strat)
    # Target fires — but qty_open hasn't gone to zero yet
    evaluator.on_bar(pos.id, _bar(190, 196, 188, 195))
    stop_slot = evaluator.trigger_state(pos.id, stop.id, stop.triggers[0].id)
    # Stop should still be armed (deferred)
    assert stop_slot.armed is True


def test_oco_full_closeout_drains_on_qty_zero(
    evaluator: ExitEvaluator,
    tracker: PositionTracker,
    sink: _RecordingSink,
) -> None:
    pos = _open_long(tracker, qty=100.0)
    stop = _stop_leg(170.0)
    target = _limit_leg(195.0)
    strat = _make_strategy(
        [stop, target],
        oco_groups=[OCOGroup(leg_ids=(stop.id, target.id),
                              cancel_on="full_closeout")],
    )
    evaluator.attach_strategy(pos.id, strat)
    evaluator.on_bar(pos.id, _bar(190, 196, 188, 195))
    # Now simulate the position closing out via tracker
    tracker.apply_fill(position_id=pos.id, qty=100.0, price=195.0)
    # The deferred cancel should have run, auto-detaching too
    assert evaluator.is_attached(pos.id) is False


def test_oco_partial_closeout_does_not_drain(
    evaluator: ExitEvaluator,
    tracker: PositionTracker,
) -> None:
    pos = _open_long(tracker, qty=100.0)
    stop = _stop_leg(170.0)
    target = _limit_leg(195.0)
    strat = _make_strategy(
        [stop, target],
        oco_groups=[OCOGroup(leg_ids=(stop.id, target.id),
                              cancel_on="full_closeout")],
    )
    evaluator.attach_strategy(pos.id, strat)
    evaluator.on_bar(pos.id, _bar(190, 196, 188, 195))
    # Partial close — qty_open > 0
    tracker.apply_fill(position_id=pos.id, qty=50.0, price=195.0)
    assert evaluator.is_attached(pos.id) is True
    stop_slot = evaluator.trigger_state(pos.id, stop.id, stop.triggers[0].id)
    assert stop_slot.armed is True  # stop still armed


def test_oco_groups_disjoint_isolation(evaluator: ExitEvaluator,
                                            tracker: PositionTracker,
                                            sink: _RecordingSink) -> None:
    pos = _open_long(tracker, qty=200.0)
    leg_a = _stop_leg(170.0, label="a")
    leg_b = _limit_leg(195.0, label="b")
    leg_c = _stop_leg(160.0, label="c")
    leg_d = _limit_leg(200.0, label="d")
    strat = _make_strategy(
        [leg_a, leg_b, leg_c, leg_d],
        oco_groups=[
            OCOGroup(leg_ids=(leg_a.id, leg_b.id), cancel_on="any_fire"),
            OCOGroup(leg_ids=(leg_c.id, leg_d.id), cancel_on="any_fire"),
        ],
    )
    evaluator.attach_strategy(pos.id, strat)
    # Bar pierces target_b (limit 195) but not target_d (limit 200)
    evaluator.on_bar(pos.id, _bar(190, 196, 188, 195))
    # leg_a disarmed (sibling of b); leg_c, leg_d untouched
    assert evaluator.trigger_state(pos.id, leg_a.id, leg_a.triggers[0].id).armed is False
    assert evaluator.trigger_state(pos.id, leg_c.id, leg_c.triggers[0].id).armed is True
    assert evaluator.trigger_state(pos.id, leg_d.id, leg_d.triggers[0].id).armed is True


# ---------------------------------------------------------------------------
# EOD kill switch (4)
# ---------------------------------------------------------------------------


def test_eod_kill_switch_fires_at_threshold(evaluator: ExitEvaluator,
                                                 tracker: PositionTracker,
                                                 sink: _RecordingSink) -> None:
    pos = _open_long(tracker, qty=100.0, price=180.0)
    strat = _make_strategy(
        [_stop_leg(170.0)],
        eod_kill=True, eod_offset_min=5,  # session_close defaults to 16:00 → trigger at 15:55
    )
    evaluator.attach_strategy(pos.id, strat)
    eod_bar = _bar(180, 181, 179, 180,
                    ts=datetime(2025, 1, 15, 15, 55, tzinfo=timezone.utc))
    fired = evaluator.on_bar(pos.id, eod_bar)
    assert len(fired) == 1
    assert fired[0].kind == ExitOrderKind.MARKET
    assert fired[0].qty == 100.0
    # Other triggers should have been cancelled via cancel_all_for_position
    assert pos.id in sink.cancel_all_calls


def test_eod_kill_switch_disabled_no_fire(evaluator: ExitEvaluator,
                                               tracker: PositionTracker) -> None:
    pos = _open_long(tracker, qty=100.0, price=180.0)
    strat = _make_strategy([_stop_leg(170.0)], eod_kill=False)
    evaluator.attach_strategy(pos.id, strat)
    eod_bar = _bar(180, 181, 179, 180,
                    ts=datetime(2025, 1, 15, 15, 55, tzinfo=timezone.utc))
    fired = evaluator.on_bar(pos.id, eod_bar)
    assert fired == []


def test_eod_kill_switch_before_threshold_no_fire(evaluator: ExitEvaluator,
                                                       tracker: PositionTracker) -> None:
    pos = _open_long(tracker, qty=100.0, price=180.0)
    strat = _make_strategy([_stop_leg(170.0)], eod_kill=True, eod_offset_min=5)
    evaluator.attach_strategy(pos.id, strat)
    early_bar = _bar(180, 181, 179, 180,
                       ts=datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc))
    fired = evaluator.on_bar(pos.id, early_bar)
    assert fired == []


def test_eod_kill_switch_only_fires_once(evaluator: ExitEvaluator,
                                              tracker: PositionTracker,
                                              sink: _RecordingSink) -> None:
    pos = _open_long(tracker, qty=100.0, price=180.0)
    strat = _make_strategy([_stop_leg(170.0)], eod_kill=True, eod_offset_min=5)
    evaluator.attach_strategy(pos.id, strat)
    eod_bar = _bar(180, 181, 179, 180,
                    ts=datetime(2025, 1, 15, 15, 55, tzinfo=timezone.utc))
    evaluator.on_bar(pos.id, eod_bar)
    # Second bar at later time — should NOT re-fire
    later_bar = _bar(180, 181, 179, 180,
                      ts=datetime(2025, 1, 15, 15, 56, tzinfo=timezone.utc))
    evaluator.on_bar(pos.id, later_bar)
    assert len(sink.submitted) == 1


# ---------------------------------------------------------------------------
# Panic flatten (3)
# ---------------------------------------------------------------------------


def test_panic_flatten_phase1_disarms_and_cancels(evaluator: ExitEvaluator,
                                                       tracker: PositionTracker,
                                                       sink: _RecordingSink) -> None:
    pos = _open_long(tracker)
    stop = _stop_leg(170.0)
    target = _limit_leg(195.0)
    strat = _make_strategy([stop, target])
    evaluator.attach_strategy(pos.id, strat)
    # Drive a fire so there's something to cancel
    evaluator.on_bar(pos.id, _bar(190, 196, 188, 195))
    # Now panic flatten phase 1
    evaluator.panic_flatten_position(pos.id)
    # All triggers disarmed
    for leg in strat.legs:
        slot = evaluator.trigger_state(pos.id, leg.id, leg.triggers[0].id)
        if slot is not None:
            assert slot.armed is False
    # cancel_all_for_position was called
    assert sink.cancel_all_calls.count(pos.id) >= 1


def test_panic_flatten_unknown_position_returns_zero(evaluator: ExitEvaluator) -> None:
    assert evaluator.panic_flatten_position("no-such") == 0


def test_panic_flatten_phase2_submits_market(evaluator: ExitEvaluator,
                                                  tracker: PositionTracker,
                                                  sink: _RecordingSink) -> None:
    pos = _open_long(tracker, qty=100.0)
    evaluator.attach_strategy(pos.id, _make_strategy([_stop_leg(170.0)]))
    evaluator.panic_flatten_position(pos.id)
    sig = evaluator.submit_market_flatten(pos.id)
    assert sig is not None
    assert sig.kind == ExitOrderKind.MARKET
    assert sig.qty == 100.0
    assert sig.label == "Panic flatten"


# ---------------------------------------------------------------------------
# Indicator triggers (3)
# ---------------------------------------------------------------------------


def test_indicator_trigger_no_registry_no_fire(evaluator: ExitEvaluator,
                                                    tracker: PositionTracker) -> None:
    pos = _open_long(tracker)
    # Indicator trigger without configured bars_registry
    from tradinglab.scanner.model import Condition, FieldRef, Group

    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef.builtin("close"),
                op=">",
                params={"right": FieldRef.literal(200.0)},
            ),
        ],
    )
    leg = ExitLeg(triggers=[
        ExitTrigger(kind=TriggerKind.INDICATOR, condition=cond, evaluate_intrabar=False),
    ])
    evaluator.attach_strategy(pos.id, _make_strategy([leg]))
    fired = evaluator.on_bar(pos.id, _bar(190, 195, 188, 192), is_close=True)
    # No registry → no fire (graceful)
    assert fired == []


def test_indicator_trigger_intrabar_disabled_does_not_fire_on_open(
    evaluator: ExitEvaluator, tracker: PositionTracker
) -> None:
    pos = _open_long(tracker)
    from tradinglab.scanner.model import Condition, FieldRef, Group

    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef.builtin("close"),
                op=">",
                params={"right": FieldRef.literal(0.0)},
            ),
        ],
    )
    leg = ExitLeg(triggers=[
        ExitTrigger(kind=TriggerKind.INDICATOR, condition=cond, evaluate_intrabar=False),
    ])
    evaluator.attach_strategy(pos.id, _make_strategy([leg]))
    # is_close=False, evaluate_intrabar=False → no-op
    fired = evaluator.on_bar(pos.id, _bar(190, 195, 188, 192), is_close=False)
    assert fired == []


def test_indicator_trigger_no_condition_no_fire(evaluator: ExitEvaluator,
                                                    tracker: PositionTracker) -> None:
    pos = _open_long(tracker)
    leg = ExitLeg(triggers=[
        ExitTrigger(kind=TriggerKind.INDICATOR, condition=None, evaluate_intrabar=True),
    ])
    evaluator.attach_strategy(pos.id, _make_strategy([leg]))
    fired = evaluator.on_bar(pos.id, _bar(190, 195, 188, 192))
    assert fired == []


# ---------------------------------------------------------------------------
# Sink failure handling (2)
# ---------------------------------------------------------------------------


def test_sink_submit_failure_marks_trigger_broken(evaluator: ExitEvaluator,
                                                       tracker: PositionTracker,
                                                       sink: _RecordingSink) -> None:
    pos = _open_long(tracker)
    leg = _market_leg()
    evaluator.attach_strategy(pos.id, _make_strategy([leg]))
    sink.raise_on_submit = True
    fired = evaluator.on_bar(pos.id, _bar(180, 180, 180, 180))
    assert fired == []
    slot = evaluator.trigger_state(pos.id, leg.id, leg.triggers[0].id)
    assert slot.broken is True
    assert slot.error_count == 1
    assert evaluator.stats().errors == 1


def test_sink_failure_does_not_break_other_legs(evaluator: ExitEvaluator,
                                                     tracker: PositionTracker,
                                                     sink: _RecordingSink) -> None:
    """First trigger fails, second still evaluates (subject to ordering)."""
    pos = _open_long(tracker)
    leg_a = _market_leg(label="a")
    leg_b = _market_leg(label="b")
    evaluator.attach_strategy(pos.id, _make_strategy([leg_a, leg_b]))
    # Only fail the FIRST submit; reset for subsequent
    submit_count = {"n": 0}
    orig_submit = sink.submit

    def selective_submit(signal):
        submit_count["n"] += 1
        if submit_count["n"] == 1:
            raise RuntimeError("first fail")
        return orig_submit(signal)

    sink.submit = selective_submit  # type: ignore[assignment]
    fired = evaluator.on_bar(pos.id, _bar(180, 180, 180, 180))
    # Second leg fires successfully
    assert len(fired) == 1
    assert fired[0].label == "b"


# ---------------------------------------------------------------------------
# B6: qty_pct fire-time resolution (2)
# ---------------------------------------------------------------------------


def test_qty_pct_resolves_against_qty_open_at_fire_time(
    evaluator: ExitEvaluator, tracker: PositionTracker
) -> None:
    pos = _open_long(tracker, qty=100.0)
    # qty_pct=50 → fire half
    leg = ExitLeg(triggers=[
        ExitTrigger(kind=TriggerKind.MARKET, qty_pct=50.0),
    ])
    evaluator.attach_strategy(pos.id, _make_strategy([leg]))
    fired = evaluator.on_bar(pos.id, _bar(180, 180, 180, 180))
    assert len(fired) == 1
    assert fired[0].qty == 50.0


def test_qty_pct_re_resolves_after_partial_fill(
    evaluator: ExitEvaluator, tracker: PositionTracker, sink: _RecordingSink
) -> None:
    pos = _open_long(tracker, qty=100.0)
    leg_target = ExitLeg(triggers=[
        ExitTrigger(kind=TriggerKind.LIMIT, price=195.0, qty_pct=50.0),
    ])
    leg_market = ExitLeg(triggers=[ExitTrigger(kind=TriggerKind.MARKET, qty_pct=100.0)])
    evaluator.attach_strategy(pos.id, _make_strategy([leg_target, leg_market]))
    # First, target fires for 50 (50% of 100)
    fired1 = evaluator.on_bar(pos.id, _bar(190, 196, 188, 195))
    # Find the LIMIT signal among possibly multiple fires
    limit_sigs = [s for s in fired1 if s.kind == ExitOrderKind.LIMIT]
    assert len(limit_sigs) == 1
    assert limit_sigs[0].qty == 50.0
    market_sigs = [s for s in fired1 if s.kind == ExitOrderKind.MARKET]
    # Market fires for 100% of qty_open now (still 100, since no fill applied yet)
    assert len(market_sigs) == 1
    assert market_sigs[0].qty == 100.0


# ---------------------------------------------------------------------------
# Tk-thread invariant (3)
# ---------------------------------------------------------------------------


def test_attach_strategy_requires_tk_thread(tracker: PositionTracker,
                                                sink: _RecordingSink,
                                                audit_root: Path) -> None:
    pos = _open_long(tracker)
    strat = _make_strategy([_market_leg()])
    audit = AuditLog()
    evaluator = ExitEvaluator(tracker=tracker, sink=sink, audit=audit)
    captured: List[BaseException] = []

    def worker() -> None:
        try:
            evaluator.attach_strategy(pos.id, strat)
        except BaseException as exc:
            captured.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2.0)
    assert len(captured) == 1
    assert isinstance(captured[0], TkThreadViolation)
    evaluator.close()
    audit.close()


def test_on_bar_requires_tk_thread(tracker: PositionTracker,
                                       sink: _RecordingSink,
                                       audit_root: Path) -> None:
    pos = _open_long(tracker)
    audit = AuditLog()
    evaluator = ExitEvaluator(tracker=tracker, sink=sink, audit=audit)
    evaluator.attach_strategy(pos.id, _make_strategy([_market_leg()]))
    captured: List[BaseException] = []

    def worker() -> None:
        try:
            evaluator.on_bar(pos.id, _bar(180, 180, 180, 180))
        except BaseException as exc:
            captured.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2.0)
    assert len(captured) == 1
    assert isinstance(captured[0], TkThreadViolation)
    evaluator.close()
    audit.close()


def test_panic_flatten_requires_tk_thread(tracker: PositionTracker,
                                              sink: _RecordingSink,
                                              audit_root: Path) -> None:
    pos = _open_long(tracker)
    audit = AuditLog()
    evaluator = ExitEvaluator(tracker=tracker, sink=sink, audit=audit)
    evaluator.attach_strategy(pos.id, _make_strategy([_market_leg()]))
    captured: List[BaseException] = []

    def worker() -> None:
        try:
            evaluator.panic_flatten_position(pos.id)
        except BaseException as exc:
            captured.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2.0)
    assert len(captured) == 1
    assert isinstance(captured[0], TkThreadViolation)
    evaluator.close()
    audit.close()


# ---------------------------------------------------------------------------
# Stats + audit integration (3)
# ---------------------------------------------------------------------------


def test_stats_increment_on_fire(evaluator: ExitEvaluator,
                                    tracker: PositionTracker) -> None:
    pos = _open_long(tracker)
    evaluator.attach_strategy(pos.id, _make_strategy([_market_leg()]))
    evaluator.on_bar(pos.id, _bar(180, 180, 180, 180))
    s = evaluator.stats()
    assert s.fires == 1
    assert s.bars_processed == 1


def test_audit_records_attach_fire_submit(evaluator: ExitEvaluator,
                                              tracker: PositionTracker,
                                              audit_root: Path) -> None:
    pos = _open_long(tracker)
    evaluator.attach_strategy(pos.id, _make_strategy([_market_leg()]))
    evaluator.on_bar(pos.id, _bar(180, 180, 180, 180))
    # Force flush by closing the audit log via the evaluator's close().
    # The fixture closes audit at teardown; read after teardown is the
    # natural flow, but we can probe the file directly.
    audit_files = list(audit_root.glob("*.jsonl"))
    assert len(audit_files) >= 1
    contents = audit_files[0].read_text(encoding="utf-8")
    assert "strategy_attach" in contents
    assert "\"fire\"" in contents
    assert "\"submit\"" in contents


def test_audit_records_eod_kill_switch(evaluator: ExitEvaluator,
                                            tracker: PositionTracker,
                                            audit_root: Path) -> None:
    pos = _open_long(tracker)
    strat = _make_strategy([_stop_leg(170.0)], eod_kill=True, eod_offset_min=5)
    evaluator.attach_strategy(pos.id, strat)
    evaluator.on_bar(pos.id, _bar(180, 181, 179, 180,
                                       ts=datetime(2025, 1, 15, 15, 55,
                                                    tzinfo=timezone.utc)))
    audit_files = list(audit_root.glob("*.jsonl"))
    contents = audit_files[0].read_text(encoding="utf-8")
    assert "eod_kill_switch_fired" in contents


# ---------------------------------------------------------------------------
# Tracker subscription / auto-detach (2)
# ---------------------------------------------------------------------------


def test_full_close_via_tracker_auto_detaches(
    evaluator: ExitEvaluator, tracker: PositionTracker
) -> None:
    pos = _open_long(tracker, qty=100.0)
    evaluator.attach_strategy(pos.id, _make_strategy([_stop_leg(170.0)]))
    tracker.apply_fill(position_id=pos.id, qty=100.0, price=180.0)
    assert evaluator.is_attached(pos.id) is False


def test_partial_close_does_not_auto_detach(
    evaluator: ExitEvaluator, tracker: PositionTracker
) -> None:
    pos = _open_long(tracker, qty=100.0)
    evaluator.attach_strategy(pos.id, _make_strategy([_stop_leg(170.0)]))
    tracker.apply_fill(position_id=pos.id, qty=50.0, price=180.0)
    assert evaluator.is_attached(pos.id) is True


# ---------------------------------------------------------------------------
# Within-last-N-bars evidence threading (Phase 7)
# ---------------------------------------------------------------------------


def _build_registry_with_candles(symbol: str, interval: str, candles):
    """Build a `(MultiIntervalCache, BarsRegistry)` populated for one key.

    The registry is what `ExitEvaluator(bars_registry=...)` consumes; the
    cache is exposed only so the test can keep a reference (set_bars
    has already injected the candles).
    """
    from tradinglab.core.bars_registry import BarsRegistry
    from tradinglab.data.multi_interval_cache import MultiIntervalCache

    cache = MultiIntervalCache()
    cache.set_bars(symbol, interval, list(candles))
    return cache, BarsRegistry(cache)


def _ascending_candles(n: int, start_close: float = 100.0,
                        interval_min: int = 5):
    """`n` 5m bars whose closes increase by 1 each bar."""
    from tradinglab.models import Candle

    out = []
    for i in range(n):
        c = start_close + i
        out.append(
            Candle(
                date=datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
                + timedelta(minutes=i * interval_min),
                open=c - 0.5, high=c + 1.0, low=c - 1.0, close=c,
                volume=1000 + i, session="regular",
            )
        )
    return out


def test_indicator_trigger_evidence_appears_in_audit(
    tracker: PositionTracker,
    sink: _RecordingSink,
    audit_root: Path,
) -> None:
    """An INDICATOR trigger with `within_last_bars=N` writes per-leaf
    `MatchEvidence` into the ``fire`` audit record's ``meta``."""
    from tradinglab.scanner.model import (
        Condition,
        FieldRef,
        Group,
        WITHIN_LAST_MODE_ANY,
    )

    pos = _open_long(tracker, symbol="AAPL")

    # Use the canonical test fixtures (AuditLog + ExitEvaluator) with a
    # populated bars_registry so the indicator path actually runs.
    audit = AuditLog()
    candles = _ascending_candles(10)  # closes 100..109
    _, registry = _build_registry_with_candles("AAPL", "5m", candles)
    evlt = ExitEvaluator(
        tracker=tracker, sink=sink, audit=audit, bars_registry=registry,
        default_interval="5m",
    )

    # close > 105 was True at bar index 6 (close=106). Set N=4 so the
    # walk window is [i-4 ... i] = bars 6..10. The leaf's any-mode
    # latch fires at the most-recent True bar.
    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef.builtin("close"),
                op=">",
                params={"right": FieldRef.literal(105.0)},
                within_last_bars=4,
                within_last_mode=WITHIN_LAST_MODE_ANY,
                interval="5m",
            ),
        ],
    )
    leg = ExitLeg(triggers=[
        ExitTrigger(
            kind=TriggerKind.INDICATOR, condition=cond, evaluate_intrabar=False,
            interval="5m",
        ),
    ])
    evlt.attach_strategy(pos.id, _make_strategy([leg]))

    # Fire the trigger on the most recent (closed) bar.
    last_close = candles[-1].close
    last_ts = candles[-1].date
    fired = evlt.on_bar(
        pos.id,
        _bar(last_close - 0.5, last_close + 0.5, last_close - 1.0, last_close,
             ts=last_ts),
        is_close=True,
    )
    assert len(fired) == 1, f"expected 1 fire, got {fired!r}"

    fire_recs = [r for r in audit.tail(50) if r["kind"] == "fire"]
    assert len(fire_recs) == 1
    meta = fire_recs[0]["meta"]
    assert "evidence" in meta, f"meta={meta!r}"
    ev_list = meta["evidence"]
    assert isinstance(ev_list, list) and ev_list
    leaf = ev_list[0]
    # Plain-dict serialisation contract from Decision.evidence.
    assert set(leaf.keys()) >= {"node_id", "bars_ago", "timestamp"}
    # Most-recent True bar is the current bar (close=109 > 105) so
    # bars_ago should be 0 in any-mode.
    assert leaf["bars_ago"] == 0
    # The condition's id must be carried through.
    assert leaf["node_id"] == cond.children[0].id

    evlt.close()
    audit.close()


def test_indicator_trigger_no_lookback_emits_no_evidence(
    tracker: PositionTracker,
    sink: _RecordingSink,
    audit_root: Path,
) -> None:
    """When `within_last_bars=0` (today's behaviour), the audit record's
    ``meta`` MUST NOT carry an ``evidence`` key."""
    from tradinglab.scanner.model import Condition, FieldRef, Group

    pos = _open_long(tracker, symbol="AAPL")

    audit = AuditLog()
    candles = _ascending_candles(10)
    _, registry = _build_registry_with_candles("AAPL", "5m", candles)
    evlt = ExitEvaluator(
        tracker=tracker, sink=sink, audit=audit, bars_registry=registry,
        default_interval="5m",
    )

    cond = Group(
        combinator="and",
        children=[
            Condition(
                left=FieldRef.builtin("close"),
                op=">",
                params={"right": FieldRef.literal(105.0)},
                interval="5m",
            ),
        ],
    )
    leg = ExitLeg(triggers=[
        ExitTrigger(
            kind=TriggerKind.INDICATOR, condition=cond, evaluate_intrabar=False,
            interval="5m",
        ),
    ])
    evlt.attach_strategy(pos.id, _make_strategy([leg]))

    last = candles[-1]
    fired = evlt.on_bar(
        pos.id,
        _bar(last.close - 0.5, last.close + 0.5, last.close - 1.0, last.close,
             ts=last.date),
        is_close=True,
    )
    assert len(fired) == 1

    fire_recs = [r for r in audit.tail(50) if r["kind"] == "fire"]
    assert len(fire_recs) == 1
    assert "evidence" not in fire_recs[0]["meta"]

    evlt.close()
    audit.close()
