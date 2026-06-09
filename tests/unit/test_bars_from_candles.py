"""Bit-for-bit equivalence + dtype contract for the single-pass
``Bars.from_candles`` / ``_epoch_ns`` timestamp path.

The single-pass extraction (commit: prefetch/merge perf sprint) replaced
seven walks of the candle list (5× ``np.fromiter`` + 2× ``np.array``
list-comprehension) with one loop, and the per-bar ``astimezone``-based
timestamp conversion with the fast ``datetime.timestamp()`` path. These
tests pin that the new output is byte-identical to the old reference so
journal values / chart axes / indicator inputs never drift.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from tradinglab.core.bars import Bars, _epoch_ns, _to_naive_utc
from tradinglab.models import Candle

_ET = ZoneInfo("America/New_York")


def _ref_timestamps(candles):
    """The pre-optimization reference timestamp array."""
    return np.array(
        [_to_naive_utc(c.date) for c in candles], dtype="datetime64[ns]"
    )


def _make(n, *, tz=_ET, micros=False, naive=False, start=None):
    base = start or datetime(2024, 3, 4, 9, 30, tzinfo=tz)
    if naive:
        base = base.replace(tzinfo=None)
    out = []
    for i in range(n):
        d = base + timedelta(minutes=5 * i)
        if micros:
            d = d + timedelta(microseconds=(i * 137) % 1_000_000)
        sess = "pre" if i % 7 == 0 else "post" if i % 11 == 0 else "regular"
        out.append(
            Candle(date=d, open=1.0 + i, high=2.0 + i, low=0.5 + i,
                   close=1.5 + i, volume=1000 + i, session=sess)
        )
    return out


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(n=200),                               # ET tz-aware, whole-minute
        dict(n=200, micros=True),                  # ET tz-aware, sub-second
        dict(n=200, tz=timezone.utc),              # UTC tz-aware
        dict(n=200, naive=True),                    # naive (verbatim-as-UTC)
        dict(n=1),                                  # single bar
        dict(n=300, start=datetime(2024, 11, 3, 0, 30, tzinfo=_ET)),  # DST fall-back day
    ],
)
def test_from_candles_timestamps_bit_identical(kwargs):
    candles = _make(**kwargs)
    bars = Bars.from_candles(candles)
    ref = _ref_timestamps(candles)
    assert bars.timestamps.dtype == np.dtype("datetime64[ns]")
    assert np.array_equal(bars.timestamps, ref)


def test_epoch_ns_matches_datetime64_scalar():
    for d in (
        datetime(2024, 3, 4, 9, 30, tzinfo=_ET),
        datetime(2024, 7, 1, 16, 0, 0, 123456, tzinfo=_ET),
        datetime(2024, 1, 1, tzinfo=timezone.utc),
        datetime(2024, 6, 15, 14, 22, 33),  # naive
    ):
        expected = np.datetime64(_to_naive_utc(d), "ns").astype("int64")
        assert _epoch_ns(d) == int(expected)


def test_from_candles_ohlcv_and_session_identical():
    candles = _make(150, micros=True)
    bars = Bars.from_candles(candles)
    assert np.array_equal(bars.open, np.array([c.open for c in candles], np.float64))
    assert np.array_equal(bars.high, np.array([c.high for c in candles], np.float64))
    assert np.array_equal(bars.low, np.array([c.low for c in candles], np.float64))
    assert np.array_equal(bars.close, np.array([c.close for c in candles], np.float64))
    assert np.array_equal(bars.volume, np.array([c.volume for c in candles], np.float64))
    assert bars.volume.dtype == np.float64
    assert np.array_equal(bars.session, np.array([c.session for c in candles], dtype=object))


def test_from_candles_empty():
    bars = Bars.from_candles([])
    assert len(bars) == 0
    assert bars.timestamps.dtype == np.dtype("datetime64[ns]")
    assert bars.volume.dtype == np.float64
    assert bars.session.dtype == object


def test_from_arrays_derives_timestamps_from_candles_bit_identical():
    candles = _make(120)
    o = np.array([c.open for c in candles], np.float64)
    h = np.array([c.high for c in candles], np.float64)
    lo = np.array([c.low for c in candles], np.float64)
    c_ = np.array([c.close for c in candles], np.float64)
    v = np.array([c.volume for c in candles], np.float64)
    bars = Bars.from_arrays(open=o, high=h, low=lo, close=c_, volume=v, candles=candles)
    assert np.array_equal(bars.timestamps, _ref_timestamps(candles))
