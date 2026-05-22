"""Tests for ``tradinglab.positions.model``: Position + PositionEvent dataclasses."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradinglab.positions.model import (
    Position,
    PositionEvent,
    PositionEventKind,
)


def _ts() -> datetime:
    return datetime(2026, 5, 4, 14, 30, tzinfo=timezone.utc)


# ---- Position basic --------------------------------------------------------

def test_position_long_constructs_with_required_fields():
    p = Position(
        id="x", symbol="AAPL", side="long",
        qty_initial=100.0, qty_open=100.0,
        avg_entry_price=175.0, entry_time=_ts(),
        source="sandbox",
    )
    assert p.is_open is True
    assert p.signed_qty_open() == 100.0


def test_position_short_signed_qty_is_negative():
    p = Position(
        id="x", symbol="AAPL", side="short",
        qty_initial=50.0, qty_open=50.0,
        avg_entry_price=180.0, entry_time=_ts(),
        source="manual",
    )
    assert p.signed_qty_open() == -50.0


def test_position_is_open_false_when_qty_zero():
    p = Position(
        id="x", symbol="AAPL", side="long",
        qty_initial=100.0, qty_open=0.0,
        avg_entry_price=175.0, entry_time=_ts(), source="manual",
    )
    assert p.is_open is False


# ---- unrealized_pnl --------------------------------------------------------

def test_unrealized_pnl_long_profitable():
    p = Position(
        id="x", symbol="AAPL", side="long",
        qty_initial=100.0, qty_open=100.0,
        avg_entry_price=175.0, entry_time=_ts(), source="sandbox",
        last_price=180.0,
    )
    assert p.unrealized_pnl() == pytest.approx(500.0)


def test_unrealized_pnl_short_profitable():
    p = Position(
        id="x", symbol="AAPL", side="short",
        qty_initial=100.0, qty_open=100.0,
        avg_entry_price=180.0, entry_time=_ts(), source="manual",
        last_price=175.0,
    )
    assert p.unrealized_pnl() == pytest.approx(500.0)


def test_unrealized_pnl_zero_when_qty_open_zero():
    p = Position(
        id="x", symbol="AAPL", side="long",
        qty_initial=100.0, qty_open=0.0,
        avg_entry_price=175.0, entry_time=_ts(), source="sandbox",
        last_price=180.0,
    )
    assert p.unrealized_pnl() == 0.0


def test_unrealized_pnl_zero_when_last_price_unset():
    p = Position(
        id="x", symbol="AAPL", side="long",
        qty_initial=100.0, qty_open=100.0,
        avg_entry_price=175.0, entry_time=_ts(), source="sandbox",
        last_price=0.0,
    )
    assert p.unrealized_pnl() == 0.0


# ---- Position serialization ------------------------------------------------

def test_position_round_trips_through_dict():
    p = Position(
        id="abc-123", symbol="AAPL", side="long",
        qty_initial=100.0, qty_open=50.0,
        avg_entry_price=175.0, entry_time=_ts(), source="sandbox",
        realized_pnl=250.0, high_watermark=180.0, low_watermark=174.0,
        last_price=178.0, bars_held=12, strategy_id="strategy-uuid-1",
        extra={"foo": "bar", "n": 7},
    )
    d = p.to_dict()
    p2 = Position.from_dict(d)
    assert p2.id == p.id and p2.symbol == p.symbol and p2.side == p.side
    assert p2.qty_initial == p.qty_initial and p2.qty_open == p.qty_open
    assert p2.avg_entry_price == p.avg_entry_price
    assert p2.entry_time == p.entry_time
    assert p2.realized_pnl == p.realized_pnl
    assert p2.high_watermark == p.high_watermark
    assert p2.low_watermark == p.low_watermark
    assert p2.last_price == p.last_price
    assert p2.bars_held == p.bars_held
    assert p2.strategy_id == p.strategy_id
    assert p2.extra == p.extra
    assert p2.source == p.source


def test_position_from_dict_rejects_invalid_side():
    raw = {
        "id": "x", "symbol": "A", "side": "wrong",
        "qty_initial": 1, "qty_open": 1, "avg_entry_price": 1,
        "entry_time": _ts().isoformat(), "source": "sandbox",
    }
    with pytest.raises(ValueError):
        Position.from_dict(raw)


def test_position_from_dict_rejects_invalid_source():
    raw = {
        "id": "x", "symbol": "A", "side": "long",
        "qty_initial": 1, "qty_open": 1, "avg_entry_price": 1,
        "entry_time": _ts().isoformat(), "source": "live",
    }
    with pytest.raises(ValueError):
        Position.from_dict(raw)


def test_position_from_dict_defaults_optional_fields():
    raw = {
        "id": "x", "symbol": "A", "side": "long",
        "qty_initial": 1, "qty_open": 1, "avg_entry_price": 100.0,
        "entry_time": _ts().isoformat(), "source": "sandbox",
    }
    p = Position.from_dict(raw)
    assert p.realized_pnl == 0.0
    assert p.high_watermark == 0.0
    assert p.last_price == 0.0
    assert p.bars_held == 0
    assert p.strategy_id is None
    assert p.extra == {}


def test_position_naive_entry_time_round_trips_as_utc():
    naive = datetime(2026, 5, 4, 14, 30)
    p = Position(
        id="x", symbol="A", side="long",
        qty_initial=1, qty_open=1, avg_entry_price=1,
        entry_time=naive, source="sandbox",
    )
    d = p.to_dict()
    p2 = Position.from_dict(d)
    assert p2.entry_time.tzinfo is not None
    assert p2.entry_time.utcoffset().total_seconds() == 0


# ---- PositionEvent ---------------------------------------------------------

def test_position_event_round_trips():
    ev = PositionEvent(
        position_id="pos-1",
        kind=PositionEventKind.PARTIAL_CLOSE,
        ts=_ts(),
        qty=50.0,
        price=178.0,
        meta={"reason": "target_1"},
    )
    d = ev.to_dict()
    ev2 = PositionEvent.from_dict(d)
    assert ev2.position_id == "pos-1"
    assert ev2.kind == PositionEventKind.PARTIAL_CLOSE
    assert ev2.qty == 50.0
    assert ev2.price == 178.0
    assert ev2.meta == {"reason": "target_1"}


def test_position_event_kind_enum_string_values_are_persisted():
    assert PositionEventKind.OPEN.value == "open"
    assert PositionEventKind.CLOSE.value == "close"
    assert PositionEventKind.STRATEGY_BIND.value == "strategy_bind"


def test_position_event_from_dict_rejects_unknown_kind():
    raw = {
        "position_id": "x", "kind": "explode", "ts": _ts().isoformat(),
        "qty": 0, "price": 0, "meta": {},
    }
    with pytest.raises(ValueError):
        PositionEvent.from_dict(raw)
