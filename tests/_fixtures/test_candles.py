"""Tests for the canonical candle fixtures.

Pins the §7.10 landmine fix (Monday-not-Saturday default seed), tz-awareness
default, OHLC continuity, and ``random_walk`` determinism. Also asserts the
RTH-friendly default by passing the fixture's output through
``runner._filter_rth_only`` and confirming bars survive.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from tests._fixtures.candles import (
    DEFAULT_MONDAY,
    ET,
    daily,
    flat,
    ramp,
    random_walk,
)
from tradinglab.models import Candle

# --------------------------------------------------------------- shape --

def test_ramp_returns_n_candles_with_ramping_closes():
    bars = ramp(10, step=0.5)
    assert len(bars) == 10
    closes = [b.close for b in bars]
    assert closes == pytest.approx([100.5, 101.0, 101.5, 102.0, 102.5,
                                    103.0, 103.5, 104.0, 104.5, 105.0])
    assert all(isinstance(b, Candle) for b in bars)


def test_ramp_open_equals_prev_close():
    bars = ramp(5)
    assert bars[0].open == pytest.approx(100.0)
    for prev, cur in zip(bars[:-1], bars[1:], strict=True):
        assert cur.open == pytest.approx(prev.close)


def test_ramp_high_low_bracket_open_close():
    bars = ramp(8)
    for b in bars:
        assert b.high >= max(b.open, b.close)
        assert b.low <= min(b.open, b.close)


def test_ramp_volume_monotone():
    bars = ramp(5)
    assert [b.volume for b in bars] == [1000, 1001, 1002, 1003, 1004]


def test_ramp_zero_n_returns_empty():
    assert ramp(0) == []


def test_ramp_negative_n_raises():
    with pytest.raises(ValueError):
        ramp(-1)


# ------------------------------------------------------- §7.10 landmine --

def test_default_start_is_monday_not_saturday():
    """The whole point of this module: default seed MUST be a weekday so
    ``require_market_open=True`` strategies actually fire. See CLAUDE.md
    §7.10 (Saturday-seed silent zero-fills landmine)."""
    bars = ramp(1)
    assert bars[0].date.weekday() == 0, (
        f"default seed must be Monday (weekday 0), got weekday "
        f"{bars[0].date.weekday()} — §7.10 landmine reintroduced")
    assert DEFAULT_MONDAY.weekday() == 0


def test_default_seed_is_tz_aware_et():
    bars = ramp(1)
    assert bars[0].date.tzinfo is not None
    assert bars[0].date.utcoffset() == DEFAULT_MONDAY.utcoffset()


def test_tz_aware_false_produces_naive():
    bars = ramp(1, tz_aware=False)
    assert bars[0].date.tzinfo is None


def _is_rth(dt: datetime) -> bool:
    """Inline Mon-Fri 09:30-16:00 ET check that mirrors
    ``runner._filter_rth_only`` semantics. Reimplemented here so this test
    module doesn't import ``strategy_tester`` (which a parallel agent is
    mid-refactoring); keeps the §7.10 landmine pin self-contained."""
    et_dt = dt.astimezone(ET) if dt.tzinfo is not None else dt.replace(tzinfo=ET)
    if et_dt.weekday() >= 5:
        return False
    t = et_dt.time()
    return (t.hour, t.minute) >= (9, 30) and (t.hour, t.minute) <= (16, 0)


def test_default_candles_survive_rth_filter():
    """The §7.10 averted: every bar in the default ramp passes the
    Mon-Fri 09:30-16:00 ET gate. A Saturday-seed fixture would lose
    all bars here."""
    bars = ramp(20, interval_min=5)  # 20 * 5min = 100 min, fits inside RTH
    kept = [b for b in bars if _is_rth(b.date)]
    assert len(kept) == len(bars)


def test_saturday_seed_drops_via_rth_filter():
    """Pin the inverse: explicitly seeding on a Saturday DOES get filtered
    out (proving the test above isn't accidentally permissive)."""
    sat = datetime(2024, 6, 1, 9, 30, tzinfo=ET)  # Saturday
    assert sat.weekday() == 5
    bars = ramp(20, start=sat, interval_min=5)
    kept = [b for b in bars if _is_rth(b.date)]
    assert kept == []


# -------------------------------------------------------------- flat --

def test_flat_all_same_price():
    bars = flat(7, price=42.5)
    assert len(bars) == 7
    for b in bars:
        assert b.open == pytest.approx(42.5)
        assert b.close == pytest.approx(42.5)


def test_flat_pass_through_kwargs():
    bars = flat(3, price=50.0, interval_min=15)
    assert (bars[1].date - bars[0].date).total_seconds() == 15 * 60


# ---------------------------------------------------------- random_walk --

def test_random_walk_shape():
    bars = random_walk(50, seed=0)
    assert len(bars) == 50
    assert all(isinstance(b, Candle) for b in bars)


def test_random_walk_deterministic_across_calls():
    a = random_walk(30, seed=42)
    b = random_walk(30, seed=42)
    assert [x.close for x in a] == [x.close for x in b]
    assert [x.open for x in a] == [x.open for x in b]


def test_random_walk_different_seeds_differ():
    a = random_walk(30, seed=0)
    b = random_walk(30, seed=1)
    assert [x.close for x in a] != [x.close for x in b]


def test_random_walk_continuity():
    bars = random_walk(20, seed=7)
    for prev, cur in zip(bars[:-1], bars[1:], strict=True):
        assert cur.open == pytest.approx(prev.close)


# --------------------------------------------------------------- daily --

def test_daily_shape_and_spacing():
    bars = daily(5)
    assert len(bars) == 5
    for prev, cur in zip(bars[:-1], bars[1:], strict=True):
        assert (cur.date - prev.date).days == 1


def test_daily_default_starts_monday():
    bars = daily(1)
    assert bars[0].date.weekday() == 0


def test_daily_ramping_closes():
    bars = daily(4, start_price=200.0, step=1.0)
    assert [b.close for b in bars] == pytest.approx([201.0, 202.0, 203.0, 204.0])

