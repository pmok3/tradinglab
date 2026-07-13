"""Unit tests for ``data.prefetch.priority`` — the ordering value types.

Contract (design §4): the scheduler orders work by a totally-ordered
``PriorityKey = (band_index, tier_rank, interval_rank, seq)`` — pure band-major
(breadth-first), tier as the secondary sort. ``FetchJob`` is the immutable work
unit carrying identity (for dedup) + priority + per-tier generation.
"""
from __future__ import annotations

import heapq

import pytest

from tradinglab.data.prefetch.priority import (
    FOREGROUND_BAND,
    FetchJob,
    PriorityKey,
)


# --------------------------------------------------------------- PriorityKey
def test_band_index_dominates_tier():
    # band 0 of the LOWEST tier still beats band 1 of the HIGHEST tier
    # (breadth-first / band-major).
    assert PriorityKey(0, 90, 0, 5) < PriorityKey(1, 10, 0, 0)


def test_tier_is_secondary_within_a_band():
    assert PriorityKey(0, 10, 0, 0) < PriorityKey(0, 20, 0, 0)


def test_interval_rank_is_tertiary():
    assert PriorityKey(0, 10, 0, 0) < PriorityKey(0, 10, 1, 0)


def test_seq_is_fifo_tiebreak():
    assert PriorityKey(0, 10, 0, 1) < PriorityKey(0, 10, 0, 2)


def test_foreground_band_sorts_before_all_background():
    fg = PriorityKey(FOREGROUND_BAND, 90, 9, 999)
    for tier in (10, 20, 90):
        assert fg < PriorityKey(0, tier, 0, 0)
    assert FOREGROUND_BAND == -1


def test_heap_pops_in_priority_order():
    keys = [
        PriorityKey(1, 10, 0, 0),   # deeper band
        PriorityKey(0, 20, 0, 0),
        PriorityKey(0, 10, 1, 0),
        PriorityKey(0, 10, 0, 0),   # best
        PriorityKey(FOREGROUND_BAND, 10, 0, 0),  # foreground = absolute first
    ]
    heap: list[PriorityKey] = []
    for k in keys:
        heapq.heappush(heap, k)
    popped = [heapq.heappop(heap) for _ in range(len(keys))]
    assert popped == sorted(keys)
    assert popped[0].band_index == FOREGROUND_BAND
    assert popped[1] == PriorityKey(0, 10, 0, 0)


def test_priority_key_is_frozen():
    k = PriorityKey(0, 10, 0, 0)
    with pytest.raises((AttributeError, TypeError)):
        k.band_index = 5  # type: ignore[misc]


# ------------------------------------------------------------------ FetchJob
def _job(**kw):
    base = dict(
        source="alpaca", symbol="AMD", interval="5m",
        band_index=0, tier_rank=10, interval_rank=0, generation=1, seq=3,
    )
    base.update(kw)
    return FetchJob(**base)


def test_dedup_key_is_source_symbol_interval_band():
    j = _job()
    assert j.dedup_key == ("alpaca", "AMD", "5m", 0)


def test_series_key_is_band_independent():
    # Identity across bands — used to attach a high-tier request to an
    # in-flight fetch of the same series.
    assert _job(band_index=0).series_key == _job(band_index=3).series_key
    assert _job().series_key == ("alpaca", "AMD", "5m")


def test_priority_reflects_fields():
    j = _job(band_index=2, tier_rank=40, interval_rank=1, seq=7)
    assert j.priority() == PriorityKey(2, 40, 1, 7)


def test_is_foreground():
    assert _job(band_index=FOREGROUND_BAND).is_foreground is True
    assert _job(band_index=0).is_foreground is False


def test_fetch_job_is_frozen():
    j = _job()
    with pytest.raises((AttributeError, TypeError)):
        j.symbol = "NVDA"  # type: ignore[misc]


def test_with_seq_returns_updated_copy():
    j = _job(seq=0)
    j2 = j.with_seq(42)
    assert j2.seq == 42 and j.seq == 0
    assert j2.priority().seq == 42
    # everything else preserved
    assert j2.dedup_key == j.dedup_key and j2.generation == j.generation


def test_jobs_orderable_via_priority_in_heap():
    jobs = [_job(band_index=1, seq=0), _job(tier_rank=20, seq=1),
            _job(seq=2), _job(band_index=FOREGROUND_BAND, seq=3)]
    heap: list[tuple[PriorityKey, int, FetchJob]] = []
    for i, jb in enumerate(jobs):
        heapq.heappush(heap, (jb.priority(), i, jb))
    order = [heapq.heappop(heap)[2] for _ in range(len(jobs))]
    assert order[0].is_foreground
    assert order[-1].band_index == 1  # deepest band last
