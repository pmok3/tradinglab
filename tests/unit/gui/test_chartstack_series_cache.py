"""Unit tests for :mod:`tradinglab.gui.chartstack.series_cache`."""

from __future__ import annotations

import pytest

from tradinglab.gui.chartstack.series_cache import Bar, CardSeriesCache


def test_upsert_appends_when_ts_differs() -> None:
    c = CardSeriesCache(maxlen=10)
    c.upsert_tick(1, (1.0, 1.5, 0.5, 1.2, 100))
    c.upsert_tick(2, (1.2, 1.6, 1.1, 1.4, 200))
    assert len(c) == 2
    bars = c.snapshot()
    assert bars[0].ts == 1
    assert bars[1].ts == 2


def test_upsert_mutates_in_place_when_ts_matches() -> None:
    c = CardSeriesCache(maxlen=10)
    c.upsert_tick(1, (1.0, 1.0, 1.0, 1.0, 100))
    first = c.latest()
    c.upsert_tick(1, (1.0, 2.0, 0.5, 1.5, 250))
    bars = c.snapshot()
    assert len(bars) == 1
    assert bars[0].high == 2.0
    assert bars[0].low == 0.5
    assert bars[0].close == 1.5
    assert bars[0].volume == 250
    # Same instance object — confirms in-place mutation, not replace.
    assert c.latest() is first


def test_eviction_at_capacity() -> None:
    c = CardSeriesCache(maxlen=3)
    for ts in range(5):
        c.upsert_tick(ts, (1.0, 1.0, 1.0, 1.0, 1.0))
    bars = c.snapshot()
    assert len(bars) == 3
    assert [b.ts for b in bars] == [2, 3, 4]


def test_append_rollover() -> None:
    c = CardSeriesCache(maxlen=2)
    c.append_rollover(Bar(ts=10, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0))
    c.append_rollover(Bar(ts=11, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0))
    c.append_rollover(Bar(ts=12, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0))
    bars = c.snapshot()
    assert [b.ts for b in bars] == [11, 12]


def test_invalidate_clears_cache() -> None:
    c = CardSeriesCache(maxlen=3)
    c.upsert_tick(1, (1.0, 1.0, 1.0, 1.0, 1.0))
    c.invalidate()
    assert len(c) == 0
    assert c.snapshot() == []
    assert c.latest() is None


def test_invalid_maxlen_raises() -> None:
    with pytest.raises(ValueError):
        CardSeriesCache(maxlen=0)


def test_bad_ohlcv_shape_raises() -> None:
    c = CardSeriesCache(maxlen=10)
    with pytest.raises(ValueError):
        c.upsert_tick(1, (1.0, 1.0, 1.0))


def test_append_rollover_type_check() -> None:
    c = CardSeriesCache(maxlen=10)
    with pytest.raises(TypeError):
        c.append_rollover("not a bar")  # type: ignore[arg-type]
