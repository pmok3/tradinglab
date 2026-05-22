"""Tests for :mod:`tradinglab.gui.evidence_overlay`."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
from matplotlib.figure import Figure

from tradinglab.core import thread_guard
from tradinglab.gui.evidence_overlay import (
    EvidenceMarker,
    EvidenceOverlay,
    _find_bar_index_by_timestamp,
    _format_bars_ago,
    _parse_iso_to_utc,
    compute_evidence_markers,
)
from tradinglab.positions.tracker import PositionTracker


@pytest.fixture(autouse=True)
def _no_tk():
    with thread_guard.tk_thread_check_disabled():
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeCandle:
    """Minimal Candle stand-in carrying just the ``date`` attribute."""

    date: datetime


def _candles(n: int, *, start: datetime, interval_min: int = 5) -> List[_FakeCandle]:
    return [
        _FakeCandle(date=start + timedelta(minutes=i * interval_min))
        for i in range(n)
    ]


class _FakeAudit:
    """Stand-in :class:`AuditLog` with a fixed ``tail`` payload."""

    def __init__(self, records: Optional[List[Dict[str, Any]]] = None):
        self._records = list(records or [])
        self.raise_on_tail = False

    def tail(self, n: int):
        if self.raise_on_tail:
            raise RuntimeError("tail boom")
        return list(self._records[-n:])


def _entry_fire_record(*, symbol: str, evidence: List[Dict[str, Any]]):
    return {
        "ts": "2024-01-15T10:40:00+00:00",
        "kind": "entry_fire",
        "strategy_id": "s1",
        "symbol": symbol,
        "qty": 100,
        "price": 100.0,
        "meta": {"reason": "ok", "evidence": evidence},
    }


def _exit_fire_record(*, position_id: str, evidence: List[Dict[str, Any]]):
    return {
        "ts": "2024-01-15T10:45:00+00:00",
        "kind": "fire",
        "strategy_id": "s2",
        "position_id": position_id,
        "qty": 100,
        "price": 105.0,
        "meta": {"reason": "indicator_true", "kind": "market",
                  "evidence": evidence},
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_parse_iso_to_utc_handles_aware_naive_and_z():
    utc = timezone.utc
    assert _parse_iso_to_utc("2024-01-15T10:35:00+00:00") == datetime(
        2024, 1, 15, 10, 35, tzinfo=utc
    )
    assert _parse_iso_to_utc("2024-01-15T10:35:00") == datetime(
        2024, 1, 15, 10, 35, tzinfo=utc
    )
    assert _parse_iso_to_utc("2024-01-15T10:35:00Z") == datetime(
        2024, 1, 15, 10, 35, tzinfo=utc
    )
    assert _parse_iso_to_utc("") is None
    assert _parse_iso_to_utc("garbage") is None


def test_find_bar_index_by_timestamp_matches_exact_second():
    start = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    cs = _candles(5, start=start)
    assert _find_bar_index_by_timestamp(cs, start) == 0
    assert _find_bar_index_by_timestamp(
        cs, start + timedelta(minutes=15)
    ) == 3
    # No match.
    assert _find_bar_index_by_timestamp(
        cs, start - timedelta(minutes=10)
    ) is None
    assert _find_bar_index_by_timestamp([], start) is None


def test_format_bars_ago_phrasing():
    assert _format_bars_ago(0) == "now"
    assert _format_bars_ago(1) == "1 bar"
    assert _format_bars_ago(2) == "2 bars"
    assert _format_bars_ago(7) == "7 bars"


# ---------------------------------------------------------------------------
# compute_evidence_markers — entries
# ---------------------------------------------------------------------------


def test_compute_markers_no_audit_logs_returns_empty():
    cs = _candles(5, start=datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc))
    out = compute_evidence_markers(
        primary_symbol="AAPL",
        primary_candles=cs,
        entries_audit=None,
        exits_audit=None,
        tracker=None,
    )
    assert out == []


def test_compute_markers_no_symbol_returns_empty():
    cs = _candles(5, start=datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc))
    out = compute_evidence_markers(
        primary_symbol="",
        primary_candles=cs,
        entries_audit=_FakeAudit([]),
        exits_audit=None,
        tracker=None,
    )
    assert out == []


def test_compute_markers_filters_entries_by_symbol():
    start = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    cs = _candles(10, start=start)
    iso_idx2 = (start + timedelta(minutes=10)).isoformat()
    rec_aapl = _entry_fire_record(
        symbol="AAPL",
        evidence=[
            {"node_id": "ema-cross", "bars_ago": 1,
             "timestamp": iso_idx2, "value": 99.5},
        ],
    )
    rec_msft = _entry_fire_record(
        symbol="MSFT",
        evidence=[
            {"node_id": "ema-cross", "bars_ago": 1,
             "timestamp": iso_idx2, "value": 99.5},
        ],
    )
    out = compute_evidence_markers(
        primary_symbol="AAPL",
        primary_candles=cs,
        entries_audit=_FakeAudit([rec_aapl, rec_msft]),
        exits_audit=None,
        tracker=None,
    )
    assert len(out) == 1
    m = out[0]
    assert m.source == "entry"
    assert m.bar_index == 2
    assert m.bars_ago == 1
    assert m.node_id == "ema-cross"


def test_compute_markers_drops_evidence_without_candle_match():
    start = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    cs = _candles(5, start=start)
    far_past = (start - timedelta(days=5)).isoformat()
    rec = _entry_fire_record(
        symbol="AAPL",
        evidence=[
            {"node_id": "x", "bars_ago": 999, "timestamp": far_past,
             "value": None},
        ],
    )
    out = compute_evidence_markers(
        primary_symbol="AAPL",
        primary_candles=cs,
        entries_audit=_FakeAudit([rec]),
        exits_audit=None,
        tracker=None,
    )
    assert out == []


def test_compute_markers_skips_records_without_evidence():
    """`entry_fire` without a `meta.evidence` key contributes nothing."""
    start = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    cs = _candles(5, start=start)
    rec = {
        "ts": "2024-01-15T10:00:00+00:00",
        "kind": "entry_fire",
        "symbol": "AAPL",
        "meta": {"reason": "ok"},  # no evidence key
    }
    out = compute_evidence_markers(
        primary_symbol="AAPL",
        primary_candles=cs,
        entries_audit=_FakeAudit([rec]),
        exits_audit=None,
        tracker=None,
    )
    assert out == []


def test_compute_markers_audit_tail_exception_swallowed():
    """A misbehaving audit log must NOT crash the renderer."""
    start = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    cs = _candles(5, start=start)
    bad = _FakeAudit([])
    bad.raise_on_tail = True
    out = compute_evidence_markers(
        primary_symbol="AAPL",
        primary_candles=cs,
        entries_audit=bad,
        exits_audit=None,
        tracker=None,
    )
    assert out == []


# ---------------------------------------------------------------------------
# compute_evidence_markers — exits via tracker
# ---------------------------------------------------------------------------


def test_compute_markers_exits_resolves_symbol_via_tracker():
    start = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    cs = _candles(10, start=start)
    iso_idx3 = (start + timedelta(minutes=15)).isoformat()

    tracker = PositionTracker()
    pos = tracker.open(symbol="AAPL", side="long", qty=100, price=100.0,
                        source="manual")

    rec = _exit_fire_record(
        position_id=pos.id,
        evidence=[
            {"node_id": "stop-cond", "bars_ago": 0,
             "timestamp": iso_idx3, "value": 98.0},
        ],
    )
    out = compute_evidence_markers(
        primary_symbol="AAPL",
        primary_candles=cs,
        entries_audit=None,
        exits_audit=_FakeAudit([rec]),
        tracker=tracker,
    )
    assert len(out) == 1
    m = out[0]
    assert m.source == "exit"
    assert m.bar_index == 3
    assert m.bars_ago == 0


def test_compute_markers_exits_drops_unknown_position():
    """An exit fire whose position_id isn't in the tracker is silently dropped."""
    start = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    cs = _candles(5, start=start)
    rec = _exit_fire_record(
        position_id="ghost-pid",
        evidence=[{"node_id": "x", "bars_ago": 0,
                   "timestamp": start.isoformat(), "value": 1.0}],
    )
    out = compute_evidence_markers(
        primary_symbol="AAPL",
        primary_candles=cs,
        entries_audit=None,
        exits_audit=_FakeAudit([rec]),
        tracker=PositionTracker(),
    )
    assert out == []


def test_compute_markers_exits_filters_by_position_symbol():
    start = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    cs = _candles(5, start=start)
    tracker = PositionTracker()
    aapl = tracker.open(symbol="AAPL", side="long", qty=10, price=100,
                          source="manual")
    msft = tracker.open(symbol="MSFT", side="long", qty=10, price=200,
                          source="manual")
    iso = start.isoformat()
    rec_aapl = _exit_fire_record(
        position_id=aapl.id,
        evidence=[{"node_id": "n", "bars_ago": 0, "timestamp": iso,
                   "value": 1.0}],
    )
    rec_msft = _exit_fire_record(
        position_id=msft.id,
        evidence=[{"node_id": "n", "bars_ago": 0, "timestamp": iso,
                   "value": 1.0}],
    )
    out = compute_evidence_markers(
        primary_symbol="AAPL",
        primary_candles=cs,
        entries_audit=None,
        exits_audit=_FakeAudit([rec_aapl, rec_msft]),
        tracker=tracker,
    )
    assert len(out) == 1
    assert out[0].source == "exit"


# ---------------------------------------------------------------------------
# compute_evidence_markers — combined + sort + label format
# ---------------------------------------------------------------------------


def test_compute_markers_combines_entries_and_exits_sorted():
    start = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    cs = _candles(10, start=start)

    tracker = PositionTracker()
    pos = tracker.open(symbol="AAPL", side="long", qty=10, price=100,
                        source="manual")

    iso_at = lambda i: (start + timedelta(minutes=i * 5)).isoformat()

    entry = _entry_fire_record(
        symbol="AAPL",
        evidence=[
            {"node_id": "ema-cross", "bars_ago": 1,
             "timestamp": iso_at(7), "value": 100.0},
            {"node_id": "vol-spike", "bars_ago": 0,
             "timestamp": iso_at(8), "value": 200.0},
        ],
    )
    exit_ = _exit_fire_record(
        position_id=pos.id,
        evidence=[
            {"node_id": "stop", "bars_ago": 2,
             "timestamp": iso_at(5), "value": 98.0},
        ],
    )
    out = compute_evidence_markers(
        primary_symbol="AAPL",
        primary_candles=cs,
        entries_audit=_FakeAudit([entry]),
        exits_audit=_FakeAudit([exit_]),
        tracker=tracker,
    )
    indices = [m.bar_index for m in out]
    assert indices == [5, 7, 8]
    sources = [m.source for m in out]
    assert sources == ["exit", "entry", "entry"]


def test_marker_label_format():
    start = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    cs = _candles(3, start=start)
    rec = _entry_fire_record(
        symbol="AAPL",
        evidence=[
            {"node_id": "abcdef0123", "bars_ago": 2,
             "timestamp": start.isoformat(), "value": 1.0},
        ],
    )
    out = compute_evidence_markers(
        primary_symbol="AAPL",
        primary_candles=cs,
        entries_audit=_FakeAudit([rec]),
        exits_audit=None,
        tracker=None,
    )
    assert len(out) == 1
    assert out[0].label.startswith("E:abcdef ")
    assert "2 bars" in out[0].label


# ---------------------------------------------------------------------------
# Class lifecycle
# ---------------------------------------------------------------------------


def test_overlay_disabled_returns_no_markers():
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    ov = EvidenceOverlay(enabled=False)
    out = ov.redraw(ax, "AAPL", _candles(3, start=datetime(
        2024, 1, 15, 9, 30, tzinfo=timezone.utc)))
    assert out == []
    assert ov.marker_count == 0


def test_overlay_redraw_creates_artists():
    start = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    cs = _candles(5, start=start)
    rec = _entry_fire_record(
        symbol="AAPL",
        evidence=[
            {"node_id": "n1", "bars_ago": 1,
             "timestamp": (start + timedelta(minutes=10)).isoformat(),
             "value": 1.0},
        ],
    )
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    ov = EvidenceOverlay(entries_audit=_FakeAudit([rec]))
    out = ov.redraw(ax, "AAPL", cs)
    assert len(out) == 1
    assert ov.marker_count == 1
    # Re-render rebuilds (lifecycle parity with entries/exits overlays).
    out2 = ov.redraw(ax, "AAPL", cs)
    assert len(out2) == 1
    assert ov.marker_count == 1


def test_overlay_set_enabled_toggles_request_redraw():
    calls: List[bool] = []
    ov = EvidenceOverlay(
        enabled=False, request_redraw=lambda: calls.append(True)
    )
    ov.set_enabled(False)  # no-op
    assert calls == []
    ov.set_enabled(True)
    assert calls == [True]
    ov.set_enabled(True)  # no-op (idempotent)
    assert calls == [True]


def test_overlay_close_clears_artists():
    fig = Figure()
    ax = fig.add_subplot(1, 1, 1)
    start = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc)
    rec = _entry_fire_record(
        symbol="AAPL",
        evidence=[
            {"node_id": "n", "bars_ago": 0,
             "timestamp": start.isoformat(), "value": 1.0},
        ],
    )
    ov = EvidenceOverlay(entries_audit=_FakeAudit([rec]))
    ov.redraw(ax, "AAPL", _candles(3, start=start))
    assert ov.marker_count == 1
    ov.close()
    assert ov.marker_count == 0


def test_overlay_redraw_no_axis_safe():
    ov = EvidenceOverlay()
    out = ov.redraw(None, "AAPL", [])
    assert out == []
