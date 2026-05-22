"""Tests for :class:`tradinglab.streaming.resampler.BarResampler`.

Pure-logic tests; no Tk, no I/O. Each test feeds 1m :class:`Candle`s
into a fresh :class:`BarResampler` and asserts on the emitted
:class:`BarEvent`s.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tradinglab.models import Candle
from tradinglab.streaming.resampler import (
    BarResampler,
    supported_intervals,
)

# --- helpers ---------------------------------------------------------------


def _mk(
    h: int, m: int, *,
    o: float = 100.0, hi: float = 101.0, lo: float = 99.0,
    c: float = 100.5, v: int = 1000,
    session: str = "regular",
    day: int = 4,
) -> Candle:
    return Candle(
        date=datetime(2026, 5, day, h, m),
        open=o, high=hi, low=lo, close=c, volume=v, session=session,
    )


def _last_closed(events):
    closed = [e for e in events if e.closed]
    assert closed, f"no closed events in {events!r}"
    return closed[-1]


def _last_forming(events):
    forming = [e for e in events if not e.closed]
    assert forming, f"no forming events in {events!r}"
    return forming[-1]


# --- core alignment & aggregation -------------------------------------------


def test_5m_alignment_full_bucket_closes_on_next():
    r = BarResampler("5m")
    minutes_ohlcv = [
        (30, 100.0, 101.0, 99.5, 100.5, 100),
        (31, 100.5, 102.0, 100.0, 101.5, 200),
        (32, 101.5, 103.0, 101.0, 102.5, 300),
        (33, 102.5, 102.8, 100.5, 100.8, 400),
        (34, 100.8, 101.0, 99.0, 99.5, 500),
    ]
    for m, o, hi, lo, cl, vol in minutes_ohlcv:
        events = r.on_1m_tick(
            _mk(9, m, o=o, hi=hi, lo=lo, c=cl, v=vol),
            forming=False,
        )
        assert not any(e.closed for e in events)

    events = r.on_1m_tick(
        _mk(9, 35, o=99.5, hi=100.0, lo=98.0, c=98.5, v=600),
        forming=False,
    )
    closed = _last_closed(events)
    assert closed.candle.date == datetime(2026, 5, 4, 9, 30)
    assert closed.candle.open == 100.0
    assert closed.candle.high == 103.0
    assert closed.candle.low == 99.0
    assert closed.candle.close == 99.5
    assert closed.candle.volume == 1500
    assert closed.source_minute_count == 5

    forming = _last_forming(events)
    assert forming.candle.date == datetime(2026, 5, 4, 9, 35)
    assert forming.candle.open == 99.5
    assert forming.candle.close == 98.5


def test_5m_alignment_with_mid_session_start():
    r = BarResampler("5m")
    events = r.on_1m_tick(_mk(9, 32), forming=False)
    forming = _last_forming(events)
    assert forming.candle.date == datetime(2026, 5, 4, 9, 30)
    assert not any(e.closed for e in events)

    events = r.on_1m_tick(_mk(9, 35), forming=False)
    closed = _last_closed(events)
    assert closed.candle.date == datetime(2026, 5, 4, 9, 30)
    assert closed.source_minute_count == 1


def test_15m_alignment_boundary_at_09_45():
    r = BarResampler("15m")
    for m in range(30, 45):
        events = r.on_1m_tick(_mk(9, m, v=10), forming=False)
        assert not any(e.closed for e in events)
    events = r.on_1m_tick(_mk(9, 45, v=10), forming=False)
    closed = _last_closed(events)
    assert closed.candle.date == datetime(2026, 5, 4, 9, 30)
    assert closed.candle.volume == 150
    assert closed.source_minute_count == 15


def test_forming_bar_updates_reflect_latest_values():
    r = BarResampler("5m")
    c = _mk(9, 30, o=100.0, hi=100.0, lo=100.0, c=100.0, v=10)
    e1 = r.on_1m_tick(c, forming=True)
    f1 = _last_forming(e1)
    assert f1.candle.close == 100.0
    assert f1.candle.high == 100.0
    assert f1.candle.volume == 10

    c.close = 102.0
    c.high = 102.5
    c.low = 99.0
    c.volume = 25
    e2 = r.on_1m_tick(c, forming=True)
    f2 = _last_forming(e2)
    assert f2.candle.close == 102.0
    assert f2.candle.high == 102.5
    assert f2.candle.low == 99.0
    assert f2.candle.volume == 25
    assert f2.candle.date == datetime(2026, 5, 4, 9, 30)
    assert not any(e.closed for e in e1 + e2)


def test_multi_bucket_spillover_seeds_new_bucket():
    r = BarResampler("5m")
    r.on_1m_tick(_mk(9, 30, o=100.0, hi=101.0, lo=99.5, c=100.5, v=50),
                 forming=False)
    events = r.on_1m_tick(
        _mk(9, 35, o=200.0, hi=201.0, lo=199.0, c=200.5, v=70),
        forming=False,
    )
    assert events[0].closed is True
    assert events[0].candle.date == datetime(2026, 5, 4, 9, 30)
    assert events[0].candle.open == 100.0
    assert events[0].candle.close == 100.5
    assert events[0].source_minute_count == 1

    assert events[1].closed is False
    assert events[1].candle.date == datetime(2026, 5, 4, 9, 35)
    assert events[1].candle.open == 200.0
    assert events[1].candle.close == 200.5
    assert events[1].source_minute_count == 1


def test_session_anchor_pre_market_bucket_walks_backwards():
    """Pre-market bars anchor to buckets walking backwards from 09:30."""
    r = BarResampler("5m")
    events = r.on_1m_tick(
        _mk(9, 25, o=100.0, hi=100.0, lo=100.0, c=100.0, v=5,
            session="pre"),
        forming=False,
    )
    forming = _last_forming(events)
    assert forming.candle.date == datetime(2026, 5, 4, 9, 25)
    assert forming.candle.session == "pre"

    r2 = BarResampler("5m")
    events2 = r2.on_1m_tick(
        _mk(9, 23, session="pre"), forming=False,
    )
    assert _last_forming(events2).candle.date == datetime(2026, 5, 4, 9, 20)


def test_volume_sums_across_merged_bars():
    r = BarResampler("5m")
    for m in range(30, 35):
        r.on_1m_tick(_mk(9, m, v=100), forming=False)
    events = r.on_1m_tick(_mk(9, 35, v=999), forming=False)
    closed = _last_closed(events)
    assert closed.candle.volume == 500


def test_high_low_aggregation_correct():
    r = BarResampler("5m")
    bars = [
        (30, 100, 101, 99,  100),
        (31, 100, 105, 98,  101),
        (32, 101, 102, 100, 101),
        (33, 101, 110, 99,  108),
        (34, 108, 109, 107, 108),
    ]
    for m, o, hi, lo, cl in bars:
        r.on_1m_tick(_mk(9, m, o=o, hi=hi, lo=lo, c=cl, v=10), forming=False)
    events = r.on_1m_tick(_mk(9, 35), forming=False)
    closed = _last_closed(events)
    assert closed.candle.high == 110
    assert closed.candle.low == 98


def test_1h_alignment():
    r = BarResampler("1h")
    for total in range(60):
        h, m = divmod(30 + total, 60)
        r.on_1m_tick(_mk(9 + h, m, v=1), forming=False)
    events = r.on_1m_tick(_mk(10, 30, v=1), forming=False)
    closed = _last_closed(events)
    assert closed.candle.date == datetime(2026, 5, 4, 9, 30)
    assert closed.source_minute_count == 60
    assert closed.candle.volume == 60


def test_reset_drops_state():
    r = BarResampler("5m")
    r.on_1m_tick(_mk(9, 30, c=100.0, v=10), forming=False)
    assert r.current_forming() is not None
    r.reset()
    assert r.current_forming() is None
    events = r.on_1m_tick(
        _mk(9, 31, o=200.0, hi=201.0, lo=199.0, c=200.5, v=20),
        forming=False,
    )
    assert not any(e.closed for e in events)
    forming = _last_forming(events)
    # Bucket anchors at 09:30 (the 5m bucket containing 09:31), and the
    # bucket is seeded fresh by the post-reset candle — no leakage from
    # the pre-reset 09:30 candle.
    assert forming.candle.date == datetime(2026, 5, 4, 9, 30)
    assert forming.candle.open == 200.0
    assert forming.candle.volume == 20
    assert forming.source_minute_count == 1


def test_current_forming_returns_in_progress_bar():
    r = BarResampler("5m")
    assert r.current_forming() is None
    r.on_1m_tick(_mk(9, 30, o=100.0, hi=101.0, lo=99.0, c=100.5, v=50),
                 forming=False)
    r.on_1m_tick(_mk(9, 31, o=100.5, hi=102.0, lo=100.0, c=101.5, v=60),
                 forming=True)
    cf = r.current_forming()
    assert cf is not None
    assert cf.date == datetime(2026, 5, 4, 9, 30)
    assert cf.high == 102.0
    assert cf.low == 99.0
    assert cf.volume == 110


def test_unsupported_interval_raises():
    with pytest.raises(ValueError):
        BarResampler("1d")
    with pytest.raises(ValueError):
        BarResampler("7m")
    with pytest.raises(ValueError):
        BarResampler("foo")


def test_supported_intervals_constant_matches_constructor():
    for iv in supported_intervals():
        BarResampler(iv)
    assert "1m" not in supported_intervals()
    assert "1d" not in supported_intervals()


def test_forming_then_closed_same_minute_does_not_double_count():
    r = BarResampler("5m")
    c = _mk(9, 30, o=100.0, hi=100.0, lo=100.0, c=100.0, v=10)
    r.on_1m_tick(c, forming=True)
    c.high = 101.0
    c.close = 100.8
    c.volume = 25
    r.on_1m_tick(c, forming=False)
    cf = r.current_forming()
    assert cf is not None
    assert cf.high == 101.0
    assert cf.close == 100.8
    assert cf.volume == 25
    events = r.on_1m_tick(_mk(9, 35), forming=False)
    closed = _last_closed(events)
    assert closed.source_minute_count == 1
    assert closed.candle.volume == 25


def test_session_open_time_override_aligns_to_custom_anchor():
    r = BarResampler("5m", session_open_time=(8, 0))
    events = r.on_1m_tick(_mk(8, 7), forming=False)
    forming = _last_forming(events)
    assert forming.candle.date == datetime(2026, 5, 4, 8, 5)
