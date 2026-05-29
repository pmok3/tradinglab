"""Unit tests for :func:`tradinglab.disk_cache.merge_candles`.

Targets ONLY the in-memory merge function — file I/O round-trip is
covered by the disk-cache integration/smoke tests
(``check_e0_disk_cache_persist``). This file pins the three
contractual behaviors documented in ``disk_cache.spec.md``:

* empty-side passthrough,
* newer wins on date overlap,
* tz-aware vs tz-naive fallback returns ``new`` without raising.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tradinglab.disk_cache import merge_candles
from tradinglab.models import Candle


def _candle(date: datetime, close: float = 1.0) -> Candle:
    """Build a minimal Candle for merge tests (OHLC all = ``close``)."""
    return Candle(
        date=date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100,
    )


def test_merge_candles_basics():
    # Both empty → []
    assert merge_candles([], []) == []
    assert merge_candles(None, None) == []
    assert merge_candles(None, []) == []
    assert merge_candles([], None) == []

    # old=[c1], new=[] → [c1]
    t1 = datetime(2024, 1, 1, 9, 30)
    c1 = _candle(t1, 1.0)
    result = merge_candles([c1], [])
    assert result == [c1]
    # Result must contain the same Candle object (no spurious copy).
    assert result[0] is c1

    # old=[], new=[c2] → [c2]
    t2 = datetime(2024, 1, 1, 9, 31)
    c2 = _candle(t2, 2.0)
    result = merge_candles([], [c2])
    assert result == [c2]
    assert result[0] is c2


def test_merge_candles_newer_wins_on_overlap():
    t1 = datetime(2024, 1, 1, 9, 30)
    old_c = _candle(t1, 1.0)
    new_c = _candle(t1, 2.0)

    merged = merge_candles([old_c], [new_c])

    # Exactly one bar — same date key collapses.
    assert len(merged) == 1
    # Newer side wins: close == 2.0 (the value from ``new``).
    assert merged[0].close == 2.0
    assert merged[0].date == t1
    # And it is in fact the ``new`` Candle object, not the old one.
    assert merged[0] is new_c


def test_merge_candles_sorted_union_with_overlap():
    t1 = datetime(2024, 1, 1, 9, 30)
    t2 = datetime(2024, 1, 1, 9, 35)
    t3 = datetime(2024, 1, 1, 9, 40)
    t4 = datetime(2024, 1, 1, 9, 45)
    old = [_candle(t1, 1.0), _candle(t2, 2.0), _candle(t4, 4.0)]
    new = [_candle(t2, 20.0), _candle(t3, 3.0)]

    merged = merge_candles(old, new)

    assert [c.date for c in merged] == [t1, t2, t3, t4]
    assert [c.close for c in merged] == [1.0, 20.0, 3.0, 4.0]
    assert merged[1] is new[0]


def test_merge_candles_last_duplicate_in_new_wins():
    t1 = datetime(2024, 1, 1, 9, 30)
    old = [_candle(t1, 1.0)]
    new = [_candle(t1, 2.0), _candle(t1, 3.0)]

    merged = merge_candles(old, new)

    assert len(merged) == 1
    assert merged[0].close == 3.0
    assert merged[0] is new[-1]


def test_merge_candles_tz_aware_fallback():
    # old is tz-naive, new is tz-aware UTC → comparison during sort
    # would raise TypeError. Production code documents a fallback that
    # returns ``new`` unmodified rather than letting the exception
    # bubble through the fetch path.
    t_naive = datetime(2024, 1, 1, 9, 30)
    t_aware = datetime(2024, 1, 1, 9, 31, tzinfo=timezone.utc)
    old = [_candle(t_naive, 1.0)]
    new = [_candle(t_aware, 2.0)]

    merged = merge_candles(old, new)

    # No merge attempted: result equals ``new`` (same Candle objects,
    # same order). list() creates a fresh container, so identity holds
    # at the element level but not at the list level.
    assert merged == new
    assert len(merged) == 1
    assert merged[0] is new[0]
    # And crucially: the tz-naive ``old`` bar is dropped — the fallback
    # branch returns ``list(new)``, not a union.
    assert all(c.date.tzinfo is timezone.utc for c in merged)
