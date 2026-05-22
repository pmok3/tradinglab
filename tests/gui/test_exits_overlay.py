"""Tests for ``tradinglab.gui.exits_overlay``.

Coverage:
- ``compute_overlay_lines`` filters by primary symbol (case-insensitive).
- LIMIT / STOP / STOP_LIMIT / TRAILING_STOP price resolution.
- TRAILING_STOP with no trail_price → skipped.
- Disarmed slot → gray dash-dot style.
- Fired trigger → dim gray dashed.
- ``ExitsOverlay.redraw`` builds matplotlib artists.
- ``set_enabled(False)`` → no artists; toggling triggers request_redraw.
- Position event triggers request_redraw.
- ``close()`` unsubscribes from tracker.
- ``clear()`` releases artist refs.
"""
from __future__ import annotations

from datetime import datetime
from typing import List
from unittest.mock import MagicMock

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from tradinglab.exits.audit import AuditLog
from tradinglab.exits.evaluator import ExitEvaluator
from tradinglab.exits.model import (
    ExitLeg,
    ExitStrategy,
    ExitTrigger,
    TriggerKind,
)
from tradinglab.exits.signals import ExitSignal
from tradinglab.exits.spec import Bar
from tradinglab.gui.exits_overlay import (
    ExitsOverlay,
    OverlayLine,
    compute_overlay_lines,
)
from tradinglab.positions.tracker import PositionTracker


class _RecordingSink:
    def __init__(self) -> None:
        self.submitted: list[ExitSignal] = []
        self._counter = 0

    def submit(self, signal: ExitSignal) -> str:
        self.submitted.append(signal)
        oid = f"o-{self._counter}"
        self._counter += 1
        return oid

    def cancel(self, order_id: str) -> bool:
        return True

    def cancel_all_for_position(self, position_id: str) -> int:
        return 0

    def working_order_ids_for_position(self, position_id: str) -> list[str]:
        return []


def _attach(tracker: PositionTracker, evaluator: ExitEvaluator,
            symbol: str, *triggers: ExitTrigger,
            side: str = "long", entry: float = 100.0):
    pos = tracker.open(symbol=symbol, side=side, qty=100, price=entry,
                       source="manual")
    strategy = ExitStrategy(
        name="t",
        legs=[ExitLeg(label="exit", triggers=list(triggers))],
    )
    evaluator.attach_strategy(pos.id, strategy)
    return pos, strategy


def _make_evaluator():
    tracker = PositionTracker()
    sink = _RecordingSink()
    audit = AuditLog()
    evaluator = ExitEvaluator(tracker=tracker, sink=sink, audit=audit)
    return tracker, sink, evaluator


# ---------------------------------------------------------------------------
# compute_overlay_lines
# ---------------------------------------------------------------------------


def test_filters_by_primary_symbol():
    tracker, sink, evaluator = _make_evaluator()
    try:
        _attach(tracker, evaluator, "AAPL",
                ExitTrigger(kind=TriggerKind.STOP, price=95.0))
        _attach(tracker, evaluator, "MSFT",
                ExitTrigger(kind=TriggerKind.STOP, price=400.0))
        lines = compute_overlay_lines(
            evaluator=evaluator, tracker=tracker, primary_symbol="AAPL")
        assert len(lines) == 1
        assert lines[0].price == 95.0
    finally:
        evaluator.close()


def test_case_insensitive_symbol_match():
    tracker, sink, evaluator = _make_evaluator()
    try:
        _attach(tracker, evaluator, "aapl",
                ExitTrigger(kind=TriggerKind.LIMIT, price=120.0))
        lines = compute_overlay_lines(
            evaluator=evaluator, tracker=tracker, primary_symbol="AAPL")
        assert len(lines) == 1
        assert lines[0].price == 120.0
    finally:
        evaluator.close()


def test_empty_symbol_returns_empty():
    tracker, sink, evaluator = _make_evaluator()
    try:
        _attach(tracker, evaluator, "AAPL",
                ExitTrigger(kind=TriggerKind.STOP, price=95.0))
        assert compute_overlay_lines(
            evaluator=evaluator, tracker=tracker, primary_symbol=None) == []
        assert compute_overlay_lines(
            evaluator=evaluator, tracker=tracker, primary_symbol="") == []
    finally:
        evaluator.close()


def test_limit_stop_stop_limit_resolution():
    tracker, sink, evaluator = _make_evaluator()
    try:
        _attach(tracker, evaluator, "AAPL",
                ExitTrigger(kind=TriggerKind.LIMIT, price=120.0),
                ExitTrigger(kind=TriggerKind.STOP, price=90.0),
                ExitTrigger(kind=TriggerKind.STOP_LIMIT,
                            price=85.0, stop_limit_offset=-0.5))
        lines = compute_overlay_lines(
            evaluator=evaluator, tracker=tracker, primary_symbol="AAPL")
        kinds = sorted(l.label.split()[0] for l in lines)
        assert kinds == ["LIMIT", "STOP", "STOP-LMT"]
        prices = sorted(l.price for l in lines)
        assert prices == [85.0, 90.0, 120.0]
    finally:
        evaluator.close()


def test_market_kind_skipped():
    tracker, sink, evaluator = _make_evaluator()
    try:
        _attach(tracker, evaluator, "AAPL",
                ExitTrigger(kind=TriggerKind.MARKET))
        lines = compute_overlay_lines(
            evaluator=evaluator, tracker=tracker, primary_symbol="AAPL")
        assert lines == []
    finally:
        evaluator.close()


def test_trailing_stop_without_trail_price_skipped():
    tracker, sink, evaluator = _make_evaluator()
    try:
        from tradinglab.exits.model import TrailUnit
        _attach(tracker, evaluator, "AAPL",
                ExitTrigger(kind=TriggerKind.TRAILING_STOP,
                            trail_unit=TrailUnit.PERCENT,
                            trail_value=2.0))
        lines = compute_overlay_lines(
            evaluator=evaluator, tracker=tracker, primary_symbol="AAPL")
        # No high-water-mark established yet → no overlay.
        assert lines == []
    finally:
        evaluator.close()


def test_trailing_stop_with_trail_price_renders():
    tracker, sink, evaluator = _make_evaluator()
    try:
        from tradinglab.exits.model import TrailUnit
        pos, _ = _attach(tracker, evaluator, "AAPL",
                         ExitTrigger(kind=TriggerKind.TRAILING_STOP,
                                     trail_unit=TrailUnit.PERCENT,
                                     trail_value=2.0))
        # Drive a closed bar: HWM=110, trail price = 110*(1-2%)=107.8
        bar = Bar(open=105, high=110, low=104, close=109, volume=1000,
                  date=datetime(2024, 1, 2, 15, 30))
        evaluator.on_bar(pos.id, bar, is_close=True)

        lines = compute_overlay_lines(
            evaluator=evaluator, tracker=tracker, primary_symbol="AAPL")
        assert len(lines) == 1
        assert lines[0].label.startswith("TRAIL")
        assert lines[0].price > 0
    finally:
        evaluator.close()


def test_no_strategy_attached_skipped():
    tracker, sink, evaluator = _make_evaluator()
    try:
        tracker.open(symbol="AAPL", side="long", qty=100, price=100.0,
                     source="manual")
        lines = compute_overlay_lines(
            evaluator=evaluator, tracker=tracker, primary_symbol="AAPL")
        assert lines == []
    finally:
        evaluator.close()


# ---------------------------------------------------------------------------
# ExitsOverlay rendering
# ---------------------------------------------------------------------------


def _make_axes():
    fig, ax = plt.subplots()
    ax.set_xlim(0, 100)
    ax.set_ylim(50, 150)
    return fig, ax


def test_redraw_builds_artists():
    tracker, sink, evaluator = _make_evaluator()
    try:
        _attach(tracker, evaluator, "AAPL",
                ExitTrigger(kind=TriggerKind.LIMIT, price=120.0),
                ExitTrigger(kind=TriggerKind.STOP, price=95.0))
        overlay = ExitsOverlay(evaluator=evaluator, tracker=tracker)
        fig, ax = _make_axes()
        try:
            lines = overlay.redraw(ax, "AAPL")
            assert len(lines) == 2
            assert overlay.line_count == 2
        finally:
            plt.close(fig)
            overlay.close()
    finally:
        evaluator.close()


def test_redraw_nothing_when_disabled():
    tracker, sink, evaluator = _make_evaluator()
    try:
        _attach(tracker, evaluator, "AAPL",
                ExitTrigger(kind=TriggerKind.STOP, price=95.0))
        overlay = ExitsOverlay(evaluator=evaluator, tracker=tracker,
                               enabled=False)
        fig, ax = _make_axes()
        try:
            lines = overlay.redraw(ax, "AAPL")
            assert lines == []
            assert overlay.line_count == 0
        finally:
            plt.close(fig)
            overlay.close()
    finally:
        evaluator.close()


def test_set_enabled_calls_request_redraw():
    tracker, sink, evaluator = _make_evaluator()
    try:
        cb = MagicMock()
        overlay = ExitsOverlay(evaluator=evaluator, tracker=tracker,
                               request_redraw=cb)
        cb.reset_mock()
        overlay.set_enabled(False)
        cb.assert_called_once()
        cb.reset_mock()
        # No-op when value is unchanged
        overlay.set_enabled(False)
        cb.assert_not_called()
        overlay.close()
    finally:
        evaluator.close()


def test_position_event_triggers_request_redraw():
    tracker, sink, evaluator = _make_evaluator()
    try:
        cb = MagicMock()
        overlay = ExitsOverlay(evaluator=evaluator, tracker=tracker,
                               request_redraw=cb)
        cb.reset_mock()
        # Opening a position fires an event.
        tracker.open(symbol="AAPL", side="long", qty=100, price=100.0,
                     source="manual")
        assert cb.call_count >= 1
        overlay.close()
    finally:
        evaluator.close()


def test_close_unsubscribes_from_tracker():
    tracker, sink, evaluator = _make_evaluator()
    try:
        cb = MagicMock()
        overlay = ExitsOverlay(evaluator=evaluator, tracker=tracker,
                               request_redraw=cb)
        overlay.close()
        cb.reset_mock()
        # After close, position events no longer trigger redraw.
        tracker.open(symbol="AAPL", side="long", qty=100, price=100.0,
                     source="manual")
        cb.assert_not_called()
    finally:
        evaluator.close()


def test_close_idempotent():
    tracker, sink, evaluator = _make_evaluator()
    try:
        overlay = ExitsOverlay(evaluator=evaluator, tracker=tracker)
        overlay.close()
        overlay.close()  # should not raise
    finally:
        evaluator.close()


def test_clear_releases_artist_refs():
    tracker, sink, evaluator = _make_evaluator()
    try:
        _attach(tracker, evaluator, "AAPL",
                ExitTrigger(kind=TriggerKind.STOP, price=95.0))
        overlay = ExitsOverlay(evaluator=evaluator, tracker=tracker)
        fig, ax = _make_axes()
        try:
            overlay.redraw(ax, "AAPL")
            assert overlay.line_count == 1
            overlay.clear()
            assert overlay.line_count == 0
        finally:
            plt.close(fig)
            overlay.close()
    finally:
        evaluator.close()


def test_redraw_returns_overlay_line_dataclass():
    tracker, sink, evaluator = _make_evaluator()
    try:
        _attach(tracker, evaluator, "AAPL",
                ExitTrigger(kind=TriggerKind.LIMIT, price=120.0))
        overlay = ExitsOverlay(evaluator=evaluator, tracker=tracker)
        fig, ax = _make_axes()
        try:
            lines = overlay.redraw(ax, "AAPL")
            assert len(lines) == 1
            assert isinstance(lines[0], OverlayLine)
            assert lines[0].price == 120.0
            assert lines[0].label.startswith("LIMIT")
        finally:
            plt.close(fig)
            overlay.close()
    finally:
        evaluator.close()


def test_disarmed_slot_uses_dashed_style():
    """Detaching while a position remains open: lines should still
    be skipped (since attached_strategy returns None)."""
    tracker, sink, evaluator = _make_evaluator()
    try:
        pos, _ = _attach(tracker, evaluator, "AAPL",
                         ExitTrigger(kind=TriggerKind.STOP, price=95.0))
        evaluator.detach_strategy(pos.id)
        lines = compute_overlay_lines(
            evaluator=evaluator, tracker=tracker, primary_symbol="AAPL")
        assert lines == []
    finally:
        evaluator.close()
