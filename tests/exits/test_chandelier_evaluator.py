"""End-to-end evaluator-dispatch tests for CHANDELIER triggers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from tradinglab.exits import audit as audit_mod
from tradinglab.exits.audit import AuditLog
from tradinglab.exits.evaluator import ExitEvaluator
from tradinglab.exits.model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
    OCOGroup,
    TriggerKind,
)
from tradinglab.exits.signals import ExitSignal
from tradinglab.exits.spec import Bar
from tradinglab.positions.model import Position
from tradinglab.positions.tracker import PositionTracker


# ---------------------------------------------------------------------------
# Minimal recording sink (mirrors test_evaluator.py)
# ---------------------------------------------------------------------------


class _RecordingSink:
    def __init__(self) -> None:
        self.submitted: List[ExitSignal] = []
        self._next = 0
        self._working: Dict[str, str] = {}

    def submit(self, signal: ExitSignal) -> str:
        oid = f"order-{self._next}"
        self._next += 1
        self.submitted.append(signal)
        self._working[oid] = signal.position_id
        return oid

    def cancel(self, order_id: str) -> bool:
        if order_id in self._working:
            del self._working[order_id]
            return True
        return False

    def cancel_all_for_position(self, position_id: str) -> int:
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
def evaluator(tracker: PositionTracker, sink: _RecordingSink, audit_root: Path):
    audit = AuditLog()
    evlt = ExitEvaluator(tracker=tracker, sink=sink, audit=audit)
    yield evlt
    evlt.close()
    audit.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_long(tracker: PositionTracker, *, qty: float = 100.0, price: float = 100.0) -> Position:
    return tracker.open(symbol="X", side="long", qty=qty, price=price, source="sandbox")


def _open_short(tracker: PositionTracker, *, qty: float = 100.0, price: float = 100.0) -> Position:
    return tracker.open(symbol="X", side="short", qty=qty, price=price, source="sandbox")


def _bar(o: float, h: float, l: float, c: float, *, ts: Optional[datetime] = None) -> Bar:
    return Bar(open=o, high=h, low=l, close=c, volume=0.0, date=ts)


def _chandelier_leg(
    *,
    lookback: int = 5,
    atr_period: int = 3,
    multiplier: float = 1.0,
    ma_type: str = "RMA",
    label: str = "chand",
) -> ExitLeg:
    return ExitLeg(
        label=label,
        triggers=[ExitTrigger(
            kind=TriggerKind.CHANDELIER,
            chandelier_lookback=lookback,
            chandelier_atr_period=atr_period,
            chandelier_multiplier=multiplier,
            chandelier_ma_type=ma_type,
        )],
    )


def _strategy(legs: List[ExitLeg], oco: Optional[List[OCOGroup]] = None) -> ExitStrategy:
    return ExitStrategy(name="chand", legs=legs, oco_groups=oco or [])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_chandelier_does_not_fire_on_entry_bar(evaluator, tracker, sink) -> None:
    pos = _open_long(tracker)
    evaluator.attach_strategy(pos.id, _strategy([_chandelier_leg()]))
    # Activation bar — even if a wild bar, no fire (don't fire on entry).
    fired = evaluator.on_bar(pos.id, _bar(100, 105, 50, 100))
    assert fired == []
    assert sink.submitted == []


def test_chandelier_long_fires_on_drop_after_warmup(evaluator, tracker, sink) -> None:
    pos = _open_long(tracker, price=100.0)
    evaluator.attach_strategy(
        pos.id,
        _strategy([_chandelier_leg(lookback=3, atr_period=2, multiplier=1.0)]),
    )
    base = datetime(2024, 1, 2, 9, 30)
    # Activation bar: rolling-high = 110 (well above future lows).
    evaluator.on_bar(pos.id, _bar(100, 110, 99, 105, ts=base + timedelta(minutes=0)))
    # Two warmup bars whose lows stay above where the stop will land.
    evaluator.on_bar(pos.id, _bar(105, 110, 108, 109, ts=base + timedelta(minutes=1)))
    evaluator.on_bar(pos.id, _bar(109, 110, 108, 109, ts=base + timedelta(minutes=2)))
    # No fire so far.
    assert sink.submitted == [], "stop should not have fired yet"
    # Now a hard drop — fires the chandelier.
    fired = evaluator.on_bar(pos.id, _bar(108, 108, 90, 91, ts=base + timedelta(minutes=3)))
    assert len(fired) == 1
    assert len(sink.submitted) == 1


def test_chandelier_short_fires_on_rally(evaluator, tracker, sink) -> None:
    pos = _open_short(tracker, price=100.0)
    evaluator.attach_strategy(
        pos.id,
        _strategy([_chandelier_leg(lookback=3, atr_period=2, multiplier=1.0)]),
    )
    base = datetime(2024, 1, 2, 9, 30)
    # Activation: rolling-low = 90.
    evaluator.on_bar(pos.id, _bar(100, 101, 90, 95, ts=base + timedelta(minutes=0)))
    # Two warmup bars whose highs stay below the future short stop.
    evaluator.on_bar(pos.id, _bar(95, 92, 90, 91, ts=base + timedelta(minutes=1)))
    evaluator.on_bar(pos.id, _bar(91, 92, 90, 91, ts=base + timedelta(minutes=2)))
    assert sink.submitted == []
    # Rally — fires the short chandelier (high pierces stop).
    fired = evaluator.on_bar(pos.id, _bar(91, 110, 91, 109, ts=base + timedelta(minutes=3)))
    assert len(fired) == 1


def test_chandelier_freeze_at_entry_ignores_later_template_edits(
    evaluator, tracker, sink,
) -> None:
    pos = _open_long(tracker, price=100.0)
    leg = _chandelier_leg(lookback=3, atr_period=2, multiplier=1.0)
    strat = _strategy([leg])
    evaluator.attach_strategy(pos.id, strat)
    base = datetime(2024, 1, 2, 9, 30)
    # Activation + warmup (rolling-high anchored well above warmup lows).
    evaluator.on_bar(pos.id, _bar(100, 110, 99, 105, ts=base + timedelta(minutes=0)))
    evaluator.on_bar(pos.id, _bar(105, 110, 108, 109, ts=base + timedelta(minutes=1)))
    evaluator.on_bar(pos.id, _bar(109, 110, 108, 109, ts=base + timedelta(minutes=2)))
    assert sink.submitted == []
    # Mutate template — must be a no-op on the live attachment (params frozen).
    leg.triggers[0].chandelier_multiplier = 100.0  # would push stop unreachably low
    # The original frozen stop is still in force.
    fired = evaluator.on_bar(pos.id, _bar(108, 108, 90, 91, ts=base + timedelta(minutes=3)))
    assert len(fired) == 1, "Frozen params must keep the original stop active."


def test_chandelier_oco_first_to_fire(evaluator, tracker, sink) -> None:
    """When a chandelier leg and a stop leg are in an OCO group, the
    first to fire wins and the other is cancelled."""
    pos = _open_long(tracker, price=100.0)
    chand_leg = _chandelier_leg(lookback=3, atr_period=2, multiplier=1.0, label="chand")
    stop_leg = ExitLeg(
        label="hard_stop",
        triggers=[ExitTrigger(kind=TriggerKind.STOP, price=99.0)],
    )
    oco = OCOGroup(leg_ids=(chand_leg.id, stop_leg.id), cancel_on="any_fire")
    strat = _strategy([chand_leg, stop_leg], oco=[oco])
    evaluator.attach_strategy(pos.id, strat)
    base = datetime(2024, 1, 2, 9, 30)
    # Bar drops below the hard stop @ 99 — stop fires first; chandelier is
    # still warming up (no stop yet), so the hard stop wins.
    fired = evaluator.on_bar(pos.id, _bar(100, 100, 98, 98, ts=base))
    # Exactly one fire on this bar (dedup within bar + OCO cancels sib).
    assert len(fired) == 1
    assert fired[0].position_id == pos.id


def test_chandelier_no_fire_when_detached(evaluator, tracker, sink) -> None:
    pos = _open_long(tracker, price=100.0)
    evaluator.attach_strategy(
        pos.id, _strategy([_chandelier_leg(lookback=3, atr_period=2, multiplier=1.0)]),
    )
    evaluator.detach_strategy(pos.id)
    base = datetime(2024, 1, 2, 9, 30)
    fired = evaluator.on_bar(pos.id, _bar(100, 100, 50, 50, ts=base))
    assert fired == []
    assert sink.submitted == []
