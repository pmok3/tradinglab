"""Unit tests for ``data.prefetch.driver`` — the scheduler orchestration layer.

The driver pumps the scheduler's dispatch decisions to an injected ``submit``
(async fetch) and routes completions back into ``scheduler.complete`` + the cache
via an injected ``apply_result`` (with the memory-vs-disk flag from
``cache_policy_for``). Shadow mode records the planned jobs WITHOUT executing —
the no-side-effect observation path for the flagged cut-over.

Pure/headless: ``submit`` + ``apply_result`` are injected, so no Tk / network.
"""
from __future__ import annotations

from tradinglab.data.prefetch.buckets import UNLIMITED_RATE, SourceBucketRegistry
from tradinglab.data.prefetch.driver import PrefetchDriver
from tradinglab.data.prefetch.priority import FOREGROUND_BAND, FetchJob
from tradinglab.data.prefetch.scheduler import PrefetchScheduler
from tradinglab.data.prefetch.tiers import (
    TIER_ACTIVE,
    TIER_UNIVERSE,
    PrefetchContext,
    standard_tiers,
)


def _scheduler(**kw):
    reg = SourceBucketRegistry(defaults={"a": UNLIMITED_RATE, "b": UNLIMITED_RATE})
    base = dict(max_inflight_global=64, max_inflight_per_source=32,
                supports_range=lambda s: False)
    base.update(kw)
    return PrefetchScheduler(standard_tiers(), buckets=reg, **base)


def _ctx(**kw):
    base = dict(source="a", active_symbol="AMD", active_interval="5m",
                compare_symbol="SPY", focused_watchlist=("NVDA",),
                other_watchlists=(), universe=("TSLA",))
    base.update(kw)
    return PrefetchContext(**base)


def _job(source="a", symbol="AMD", interval="5m", band_index=0,
         tier_rank=TIER_ACTIVE):
    return FetchJob(source=source, symbol=symbol, interval=interval,
                    band_index=band_index, tier_rank=tier_rank,
                    interval_rank=0, generation=0)


def _candles(n):
    return list(range(n))  # opaque bars; driver only needs len()


# --------------------------------------------------------------------- live
def test_pump_dispatches_to_submit_in_priority_order():
    submitted = []
    sch = _scheduler()
    drv = PrefetchDriver(sch, submit=submitted.append)
    drv.set_context(_ctx())
    drv.pump()
    # active AMD (both intervals) come before compare, watchlist, universe
    order = [(j.symbol, j.tier_rank) for j in submitted]
    assert order[0][0] == "AMD" and order[0][1] == TIER_ACTIVE
    assert {"AMD", "SPY", "NVDA", "TSLA"} <= {s for s, _ in order}


def test_pump_returns_retry_after_when_blocked():
    reg = SourceBucketRegistry(defaults={"slow": 1.0})
    while reg.bucket_for("slow").try_acquire(1):
        pass
    sch = PrefetchScheduler(standard_tiers(), buckets=reg)
    drv = PrefetchDriver(sch, submit=lambda j: None)
    sch.enqueue(_job(source="slow"))
    ra = drv.pump()
    assert ra is not None and ra > 0


def test_request_foreground_enqueues_band_minus_one_first():
    submitted = []
    sch = _scheduler()
    drv = PrefetchDriver(sch, submit=submitted.append)
    sch.enqueue(_job(symbol="AMD", band_index=0))
    drv.request_foreground(_job(symbol="NVDA", band_index=FOREGROUND_BAND))
    drv.pump()
    assert submitted[0].symbol == "NVDA" and submitted[0].is_foreground


# ------------------------------------------------------------------- shadow
def test_shadow_records_without_submitting():
    submitted = []
    sch = _scheduler()
    drv = PrefetchDriver(sch, submit=submitted.append, shadow=True)
    drv.set_context(_ctx())
    drv.pump()
    assert submitted == []                       # no execution in shadow
    assert len(drv.shadow_log) >= 4              # planned band-0 jobs recorded
    assert any(j.symbol == "AMD" for j in drv.shadow_log)


# --------------------------------------------------------------- completion
def test_complete_applies_to_cache_with_memory_policy():
    applied = []
    sch = _scheduler()
    drv = PrefetchDriver(sch, submit=lambda j: None,
                         apply_result=lambda job, bars, mem: applied.append((job.symbol, mem)))
    active = _job(symbol="AMD", tier_rank=TIER_ACTIVE, band_index=0)
    universe = _job(symbol="TSLA", tier_rank=TIER_UNIVERSE, band_index=0)
    sch.enqueue(active)
    sch.enqueue(universe)
    drv.pump()
    drv.complete(active, bars=_candles(50), oldest_ts=1000.0)
    drv.complete(universe, bars=_candles(50), oldest_ts=1000.0)
    assert ("AMD", True) in applied              # active band-0 → memory+disk
    assert ("TSLA", False) in applied            # universe → disk-only


def test_complete_error_does_not_apply():
    applied = []
    sch = _scheduler()
    drv = PrefetchDriver(sch, submit=lambda j: None,
                         apply_result=lambda *a: applied.append(a))
    job = _job()
    sch.enqueue(job)
    drv.pump()
    drv.complete(job, bars=[], error=RuntimeError("net"))
    assert applied == []
    assert sch.inflight_count == 0               # scheduler.complete still ran


def test_complete_routes_bars_count_for_deepening():
    sch = _scheduler(supports_range=lambda s: s == "a")
    drv = PrefetchDriver(sch, submit=lambda j: None, apply_result=lambda *a: None)
    job = _job(source="a", band_index=0)
    sch.enqueue(job)
    drv.pump()
    drv.complete(job, bars=_candles(50), oldest_ts=1000.0)
    # deepened: band 1 now queued (range provider)
    assert sch.pending_count == 1


def test_complete_accepts_explicit_bars_count_without_bars():
    """The live seam does merge+save on the worker and marshals only the count
    back to Tk — ``bars_count`` drives deepening without a bars list."""
    applied = []
    sch = _scheduler(supports_range=lambda s: s == "a")
    drv = PrefetchDriver(sch, submit=lambda j: None,
                         apply_result=lambda *a: applied.append(a))
    job = _job(source="a", band_index=0)
    sch.enqueue(job)
    drv.pump()
    drv.complete(job, bars_count=50, oldest_ts=1000.0)
    assert sch.pending_count == 1        # deepened on the count alone
    assert applied == []                 # no bars list → apply_result not called


def test_complete_bars_takes_precedence_over_count():
    sch = _scheduler()
    drv = PrefetchDriver(sch, submit=lambda j: None, apply_result=lambda *a: None)
    job = _job(band_index=0)
    sch.enqueue(job)
    drv.pump()
    # bars given → its len wins; bars_count ignored.
    drv.complete(job, bars=_candles(3), bars_count=999, oldest_ts=1000.0)
    assert sch.is_exhausted("a", "AMD", "5m")   # period provider, one band
