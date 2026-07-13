"""Unit tests for ``data.prefetch.scheduler`` — core state machine (6a).

Covers the pure, headless policy layer: enqueue/dedup/promote, per-tier
generation with the enqueue-all rebuild (review fix for lower-tier ownership),
and ``next_dispatch -> DispatchDecision`` with the generation gate, global +
per-source inflight caps, and the rate-gate (skip to a ready different-source
job instead of spinning). Deepening, retry/poison, foreground waiters and
refresh cadence are later sub-increments.
"""
from __future__ import annotations

from tradinglab.data.prefetch.buckets import UNLIMITED_RATE, SourceBucketRegistry
from tradinglab.data.prefetch.priority import FOREGROUND_BAND, FetchJob
from tradinglab.data.prefetch.scheduler import DispatchDecision, PrefetchScheduler
from tradinglab.data.prefetch.tiers import (
    TIER_ACTIVE,
    TIER_COMPARE,
    TIER_FOCUSED_WL,
    TIER_UNIVERSE,
    PrefetchContext,
    standard_tiers,
)


def _reg(**rates):
    base = {"a": UNLIMITED_RATE, "b": UNLIMITED_RATE}
    base.update(rates)
    return SourceBucketRegistry(defaults=base)


def _sched(reg=None, **kw):
    base = dict(max_inflight_global=8, max_inflight_per_source=4)
    base.update(kw)
    return PrefetchScheduler(standard_tiers(), buckets=reg or _reg(), **base)


def _job(source="a", symbol="AMD", interval="5m", band_index=0,
         tier_rank=TIER_ACTIVE, interval_rank=0, generation=0):
    return FetchJob(source=source, symbol=symbol, interval=interval,
                    band_index=band_index, tier_rank=tier_rank,
                    interval_rank=interval_rank, generation=generation)


def _ctx(**kw):
    base = dict(source="a", active_symbol="AMD", active_interval="5m",
                compare_symbol="SPY", focused_watchlist=("NVDA",),
                other_watchlists=(), universe=("AMD", "SPY", "TSLA"))
    base.update(kw)
    return PrefetchContext(**base)


# --------------------------------------------------------------- basics
def test_dispatch_decision_fields():
    d = DispatchDecision(job=None, retry_after_s=1.5)
    assert d.job is None and d.retry_after_s == 1.5


def test_enqueue_increments_pending():
    s = _sched()
    s.enqueue(_job())
    assert s.pending_count == 1


def test_next_dispatch_band_major_order():
    s = _sched()
    s.enqueue(_job(band_index=1, tier_rank=TIER_ACTIVE))       # deeper band
    s.enqueue(_job(band_index=0, tier_rank=TIER_UNIVERSE, symbol="TSLA"))
    first = s.next_dispatch().job
    assert first.band_index == 0 and first.symbol == "TSLA"     # band 0 wins


def test_dedup_same_key_once():
    s = _sched()
    s.enqueue(_job())
    s.enqueue(_job())
    assert s.pending_count == 1


def test_promote_to_higher_tier():
    s = _sched()
    s.enqueue(_job(tier_rank=TIER_UNIVERSE, symbol="TSLA"))
    s.enqueue(_job(tier_rank=TIER_FOCUSED_WL, symbol="TSLA"))  # same series, better tier
    job = s.next_dispatch().job
    assert job.tier_rank == TIER_FOCUSED_WL
    assert s.pending_count == 0  # the stale universe entry is a lazy-deleted dup


def test_no_demote_to_lower_tier():
    s = _sched()
    s.enqueue(_job(tier_rank=TIER_ACTIVE, symbol="AMD"))
    s.enqueue(_job(tier_rank=TIER_UNIVERSE, symbol="AMD"))  # worse tier — ignored
    assert s.next_dispatch().job.tier_rank == TIER_ACTIVE


# --------------------------------------------------------------- rebuild / gen
def test_rebuild_expands_all_tiers():
    s = _sched()
    s.rebuild(_ctx())
    syms = set()
    while (job := s.next_dispatch().job) is not None:
        syms.add(job.symbol)
        s.complete(job)
    assert {"AMD", "SPY", "NVDA", "TSLA"} <= syms


def test_generation_bump_drops_stale_jobs():
    s = _sched()
    s.rebuild(_ctx())                       # gen 1 everywhere
    s.rebuild(_ctx(active_symbol="AMD"))    # bump all again -> gen 2; old dropped
    # Only current-generation jobs survive: dispatch drains without stale error.
    seen = 0
    while (dd := s.next_dispatch()).job is not None:
        s.complete(dd.job)
        seen += 1
    assert seen >= 4


def test_scoped_rebuild_keeps_active_deep_bands():
    s = _sched()
    s.rebuild(_ctx())
    # simulate a deepened active band already queued
    s.enqueue(_job(symbol="AMD", band_index=1, tier_rank=TIER_ACTIVE, generation=s.generation_of(TIER_ACTIVE)))
    # watchlist-only change: bump only tier 30
    s.rebuild(_ctx(focused_watchlist=("INTC",)), changed_ranks=[TIER_FOCUSED_WL])
    jobs = []
    while (job := s.next_dispatch().job) is not None:
        jobs.append(job)
        s.complete(job)
    # the deep active band survived (not dropped by the watchlist re-arm)
    assert any(j.symbol == "AMD" and j.band_index == 1 for j in jobs)
    assert any(j.symbol == "INTC" for j in jobs)


def test_scoped_rebuild_reassigns_dropped_active_symbol_to_watchlist():
    # Review fix: old active ticker that is still in a watchlist must reappear
    # as a watchlist job, even on a scoped active-only re-arm (enqueue-all).
    s = _sched()
    s.rebuild(_ctx(active_symbol="AMD", focused_watchlist=("AMD", "NVDA")))
    # AMD claimed by active tier only (dedup) so far.
    s.rebuild(_ctx(active_symbol="NVDA", focused_watchlist=("AMD", "NVDA")),
              changed_ranks=[TIER_ACTIVE, TIER_COMPARE])
    tiers_by_sym: dict[str, set[int]] = {}
    while (job := s.next_dispatch().job) is not None:
        tiers_by_sym.setdefault(job.symbol, set()).add(job.tier_rank)
        s.complete(job)
    assert TIER_ACTIVE in tiers_by_sym["NVDA"]          # new active
    assert TIER_FOCUSED_WL in tiers_by_sym.get("AMD", set())  # demoted to watchlist


# --------------------------------------------------------------- caps / rate
def test_global_inflight_cap():
    s = _sched(max_inflight_global=1)
    s.enqueue(_job(symbol="AMD"))
    s.enqueue(_job(symbol="NVDA", tier_rank=TIER_FOCUSED_WL))
    assert s.next_dispatch().job is not None      # 1 inflight
    dd = s.next_dispatch()
    assert dd.job is None and dd.retry_after_s is not None  # at cap


def test_per_source_cap_lets_other_source_through():
    s = _sched(max_inflight_per_source=1)
    s.enqueue(_job(source="a", symbol="AMD"))
    s.enqueue(_job(source="a", symbol="NVDA", tier_rank=TIER_FOCUSED_WL))
    s.enqueue(_job(source="b", symbol="MSFT", tier_rank=TIER_UNIVERSE))
    d1 = s.next_dispatch().job
    d2 = s.next_dispatch().job
    got = {d1.source, d2.source}
    assert got == {"a", "b"}                      # source 'a' capped at 1, 'b' runs


def test_rate_gate_skips_to_ready_source():
    reg = _reg(slow=1.0)  # ~1 req/min bucket, burst 1
    # drain the 'slow' bucket
    while reg.bucket_for("slow").try_acquire(1):
        pass
    s = _sched(reg=reg)
    s.enqueue(_job(source="slow", symbol="AMD", tier_rank=TIER_ACTIVE))
    s.enqueue(_job(source="b", symbol="MSFT", tier_rank=TIER_UNIVERSE))
    # top priority (active/slow) is rate-blocked → dispatch the ready 'b' job.
    job = s.next_dispatch().job
    assert job is not None and job.source == "b"


def test_rate_gate_all_blocked_returns_retry_after():
    reg = _reg(slow=1.0)
    while reg.bucket_for("slow").try_acquire(1):
        pass
    s = _sched(reg=reg)
    s.enqueue(_job(source="slow", symbol="AMD"))
    dd = s.next_dispatch()
    assert dd.job is None and dd.retry_after_s and dd.retry_after_s > 0


def test_complete_clears_inflight():
    s = _sched()
    s.enqueue(_job())
    job = s.next_dispatch().job
    assert s.inflight_count == 1
    s.complete(job)
    assert s.inflight_count == 0


# --------------------------------------------------------------- foreground
def test_foreground_dispatched_first():
    s = _sched()
    s.enqueue(_job(band_index=0, tier_rank=TIER_ACTIVE, symbol="AMD"))
    s.enqueue(_job(band_index=FOREGROUND_BAND, tier_rank=TIER_ACTIVE, symbol="NVDA"))
    assert s.next_dispatch().job.symbol == "NVDA"


def test_foreground_survives_generation_bump():
    s = _sched()
    s.enqueue(_job(band_index=FOREGROUND_BAND, symbol="NVDA", generation=0))
    s.rebuild(_ctx())  # bumps generations; foreground must NOT be dropped
    seen = set()
    while (job := s.next_dispatch().job) is not None:
        seen.add((job.symbol, job.band_index))
        s.complete(job)
    assert ("NVDA", FOREGROUND_BAND) in seen
