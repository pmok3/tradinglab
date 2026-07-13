"""Unit tests for scheduler foreground/refresh/cache-policy (6d).

Covers the last review-driven pieces: a cancel predicate that drops a
no-longer-waited foreground job; a per-source reserve so background yields
tokens while a foreground is pending; time-gated `enqueue_at` + `next_wakeup`
for the bar-aligned refresh cadence (Decision 7); and `cache_policy_for`
(Decision 5 — active/compare band-0 → memory, everything else disk-only).
"""
from __future__ import annotations

from tradinglab.data.prefetch.buckets import UNLIMITED_RATE, SourceBucketRegistry
from tradinglab.data.prefetch.priority import FOREGROUND_BAND, FetchJob
from tradinglab.data.prefetch.scheduler import (
    CACHE_DISK_ONLY,
    CACHE_MEMORY_AND_DISK,
    PrefetchScheduler,
)
from tradinglab.data.prefetch.tiers import (
    TIER_ACTIVE,
    TIER_COMPARE,
    TIER_UNIVERSE,
    standard_tiers,
)


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _sched(clock=None, **kw):
    clock = clock or _Clock()
    reg = SourceBucketRegistry(defaults={"a": UNLIMITED_RATE, "b": UNLIMITED_RATE},
                               clock=clock)
    return PrefetchScheduler(standard_tiers(), buckets=reg, clock=clock, **kw), clock


def _job(source="a", band_index=0, symbol="AMD", interval="5m",
         tier_rank=TIER_ACTIVE):
    return FetchJob(source=source, symbol=symbol, interval=interval,
                    band_index=band_index, tier_rank=tier_rank,
                    interval_rank=0, generation=0)


def _dispatch(s):
    return s.next_dispatch().job


# ----------------------------------------------------------------- cancel
def test_cancel_predicate_drops_job():
    s, _ = _sched()
    s.enqueue(_job(), cancel=lambda: True)
    assert _dispatch(s) is None
    assert s.pending_count == 0


def test_cancel_predicate_false_dispatches():
    s, _ = _sched()
    s.enqueue(_job(), cancel=lambda: False)
    assert _dispatch(s) is not None


def test_cancelled_foreground_not_dispatched():
    s, _ = _sched()
    cancelled = {"v": False}
    s.enqueue(_job(band_index=FOREGROUND_BAND, symbol="NVDA"),
              cancel=lambda: cancelled["v"])
    cancelled["v"] = True
    assert _dispatch(s) is None


# ----------------------------------------------------- foreground_pending
def test_foreground_pending_true_while_queued():
    s, _ = _sched()
    s.enqueue(_job(band_index=FOREGROUND_BAND, source="a"))
    assert s.foreground_pending("a") is True
    assert s.foreground_pending("b") is False


def test_foreground_pending_false_after_dispatch():
    s, _ = _sched()
    s.enqueue(_job(band_index=FOREGROUND_BAND, source="a"))
    _dispatch(s)
    assert s.foreground_pending("a") is False


# --------------------------------------------------------------- reserve
def test_reserve_holds_tokens_for_pending_foreground():
    s, c = _sched()
    # foreground queued but time-gated (not yet dispatchable)
    s.enqueue_at(_job(band_index=FOREGROUND_BAND, source="a", symbol="NVDA"),
                 c.t + 100.0)
    s.enqueue(_job(source="a", symbol="AMD", tier_rank=TIER_ACTIVE))
    # background 'a' is reserved for the pending foreground → nothing dispatches
    assert _dispatch(s) is None


def test_reserve_is_per_source():
    s, c = _sched()
    s.enqueue_at(_job(band_index=FOREGROUND_BAND, source="a", symbol="NVDA"),
                 c.t + 100.0)
    s.enqueue(_job(source="a", symbol="AMD"))
    s.enqueue(_job(source="b", symbol="MSFT", tier_rank=TIER_UNIVERSE))
    job = _dispatch(s)
    assert job is not None and job.source == "b"   # 'b' not reserved


# ------------------------------------------------------ enqueue_at / wakeup
def test_enqueue_at_gates_until_time():
    s, c = _sched()
    s.enqueue_at(_job(), c.t + 50.0)
    assert _dispatch(s) is None
    c.advance(51.0)
    assert _dispatch(s) is not None


def test_next_wakeup_reports_earliest_gate():
    s, c = _sched()
    s.enqueue_at(_job(symbol="AMD"), c.t + 80.0)
    s.enqueue_at(_job(symbol="NVDA", tier_rank=TIER_COMPARE), c.t + 30.0)
    assert s.next_wakeup() == c.t + 30.0


def test_next_wakeup_none_when_nothing_gated():
    s, c = _sched()
    s.enqueue(_job())
    assert s.next_wakeup() is None


# ------------------------------------------------------------ cache_policy
def test_cache_policy_active_band0_is_memory():
    s, _ = _sched()
    assert s.cache_policy_for(_job(tier_rank=TIER_ACTIVE, band_index=0)) == CACHE_MEMORY_AND_DISK
    assert s.cache_policy_for(_job(tier_rank=TIER_COMPARE, band_index=0)) == CACHE_MEMORY_AND_DISK


def test_cache_policy_deep_band_is_disk_only():
    s, _ = _sched()
    assert s.cache_policy_for(_job(tier_rank=TIER_ACTIVE, band_index=1)) == CACHE_DISK_ONLY


def test_cache_policy_low_tier_is_disk_only():
    s, _ = _sched()
    assert s.cache_policy_for(_job(tier_rank=TIER_UNIVERSE, band_index=0)) == CACHE_DISK_ONLY


def test_cache_policy_foreground_active_is_memory():
    s, _ = _sched()
    fg = _job(tier_rank=TIER_ACTIVE, band_index=FOREGROUND_BAND)
    assert s.cache_policy_for(fg) == CACHE_MEMORY_AND_DISK
