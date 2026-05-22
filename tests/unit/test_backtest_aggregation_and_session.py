"""Batch 13 — aggregation interval helpers + SessionSpec round-trip.

Targets:
    * ``backtest/aggregation.py`` — ``divides_evenly``, ``interval_minutes``,
      and the session-open-anchored bucketing of ``aggregate``.
    * ``backtest/session.py`` — ``SessionSpec.to_dict`` / ``from_dict``
      byte-equal round-trip and stable JSON key order.
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict

import pytest

from tradinglab.backtest import aggregation
from tradinglab.backtest.aggregation import (
    aggregate,
    divides_evenly,
    interval_minutes,
)
from tradinglab.backtest.session import SessionSpec
from tradinglab.models import Candle

# ---------------------------------------------------------------------------
# 1. divides_evenly + interval_minutes
# ---------------------------------------------------------------------------

def test_divides_evenly_and_interval_minutes():
    # divides_evenly: target is integer multiple of primary in minutes.
    assert divides_evenly("5m", "15m") is True
    assert divides_evenly("5m", "1h") is True
    # 2m → 5m is not an integer multiple (5 % 2 != 0).
    assert divides_evenly("2m", "5m") is False
    # Unknown intervals always come back as False, never raise.
    assert divides_evenly("xxx", "5m") is False
    assert divides_evenly("5m", "xxx") is False

    # interval_minutes: known intervals return their minute count.
    assert interval_minutes("1m") == 1
    assert interval_minutes("5m") == 5
    assert interval_minutes("1h") == 60
    assert interval_minutes("60m") == 60

    # Stricter contract than constants.py: aggregation module rejects "1d"
    # because it is not in INTERVAL_MINUTES.
    assert "1d" not in aggregation.INTERVAL_MINUTES
    with pytest.raises(ValueError):
        interval_minutes("1d")
    with pytest.raises(ValueError):
        interval_minutes("totally-bogus")


# ---------------------------------------------------------------------------
# 2. session-open-anchored bucketing
# ---------------------------------------------------------------------------

def _make_5m_session(date: _dt.date, start: _dt.time, end: _dt.time) -> list[Candle]:
    """Build inclusive 5-minute candles from ``start`` to ``end`` on ``date``."""
    bars: list[Candle] = []
    cursor = _dt.datetime.combine(date, start)
    last = _dt.datetime.combine(date, end)
    n = 0
    while cursor <= last:
        bars.append(
            Candle(
                date=cursor,
                open=100.0 + n * 0.10,
                high=100.5 + n * 0.10,
                low=99.5 + n * 0.10,
                close=100.2 + n * 0.10,
                volume=1_000 + n,
                session="regular",
            )
        )
        cursor += _dt.timedelta(minutes=5)
        n += 1
    return bars


def test_aggregate_anchors_on_session_open_not_utc_modulo():
    # ---- Day 1: 09:30 → 11:30 ET (naive) at 5m, aggregated to 1h. ---------
    day1 = _dt.date(2025, 4, 29)
    day1_bars = _make_5m_session(day1, _dt.time(9, 30), _dt.time(11, 30))
    assert len(day1_bars) == 25  # 09:30 through 11:30 inclusive at 5m.

    result_day1 = aggregate(day1_bars, "5m", "1h")

    # Three buckets — anchored on session open (09:30), NOT UTC-modulo (09:00).
    starts_day1 = [c.date for c in result_day1]
    assert starts_day1 == [
        _dt.datetime(2025, 4, 29, 9, 30),
        _dt.datetime(2025, 4, 29, 10, 30),
        _dt.datetime(2025, 4, 29, 11, 30),
    ], (
        "Aggregation must anchor on the session's first primary-bar timestamp, "
        f"not the UTC-clock hour. Got: {starts_day1!r}"
    )

    # Regression guard: nothing landed at 09:00, 10:00, or 11:00 (UTC-modulo).
    for c in result_day1:
        assert c.date.minute == 30

    # ---- Append Day 2 starting at 09:30 — anchor must reset on new date. --
    day2 = _dt.date(2025, 4, 30)
    day2_bars = _make_5m_session(day2, _dt.time(9, 30), _dt.time(11, 30))

    combined = day1_bars + day2_bars
    result_both = aggregate(combined, "5m", "1h")

    starts_all = [c.date for c in result_both]
    # Day 1 buckets first.
    assert starts_all[:3] == starts_day1
    # Day 2's first bucket also anchored on 09:30 (anchor reset on date change).
    assert starts_all[3] == _dt.datetime(2025, 4, 30, 9, 30), (
        "New calendar date must reset the session anchor; the first day-2 "
        f"bucket should start at 09:30 on the next date. Got: {starts_all[3]!r}"
    )
    assert starts_all[3:] == [
        _dt.datetime(2025, 4, 30, 9, 30),
        _dt.datetime(2025, 4, 30, 10, 30),
        _dt.datetime(2025, 4, 30, 11, 30),
    ]


# ---------------------------------------------------------------------------
# 3. SessionSpec to_dict / from_dict round-trip + stable JSON key order
# ---------------------------------------------------------------------------

def test_session_spec_to_from_dict_round_trip():
    # Construct with non-default values for every field so a missing or
    # reordered field in to_dict/from_dict would be caught by the equality
    # check below.
    spec = SessionSpec(
        deck_seed=4242,
        tickers=("AAPL", "MSFT", "NVDA"),
        start_clock_iso="2025-04-29T09:30:00-04:00",
        slippage_bps=2.5,
        commission=0.65,
        engine_version="sandbox-test-engine",
        setup_tags=("breakout", "vwap-reclaim"),
        starting_cash=250_000.0,
        include_extended=True,
        auto_cycle=True,
        cycle_dates=("2025-04-29", "2025-04-30", "2025-05-01"),
        universe_id="watchlist:Mega Caps",
        universe_symbols=("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"),
        strict_offline=True,
    )

    # to_dict → from_dict → asdict byte-equal (every field round-tripped).
    payload = spec.to_dict()
    restored = SessionSpec.from_dict(payload)
    assert asdict(restored) == asdict(spec)
    # Sanity: frozen-dataclass __eq__ also agrees.
    assert restored == spec

    # to_dict has stable key order (canonical), so dumping the original and
    # the round-tripped spec with sort_keys=False yields byte-identical JSON.
    dump_orig = json.dumps(spec.to_dict(), sort_keys=False)
    dump_roundtrip = json.dumps(restored.to_dict(), sort_keys=False)
    assert dump_orig == dump_roundtrip, (
        "to_dict() must emit keys in a stable canonical order so sandbox "
        "saves are byte-identical across save/load cycles."
    )

    # Belt-and-braces: dumping the same spec twice is also byte-identical
    # (catches accidental introduction of e.g. set-iteration order).
    assert json.dumps(spec.to_dict(), sort_keys=False) == dump_orig
