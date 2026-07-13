"""Unit tests for scheduler deepening (6b).

Contract (Decision 8 + review): on a successful background fetch the scheduler
enqueues the NEXT band of that ``(source, symbol, interval)`` series via the
source's window planner, until the provider is exhausted. Exhaustion is
scheduler-owned and result-driven:

* period providers (yfinance) have no band > 0 → exhausted after band 0;
* range providers (Alpaca) deepen while ``oldest_ts`` keeps advancing (older),
  and stop when a fetch returns no bars / no older data.

Foreground jobs and errored / empty fetches never deepen.
"""
from __future__ import annotations

from tradinglab.data.prefetch.buckets import UNLIMITED_RATE, SourceBucketRegistry
from tradinglab.data.prefetch.priority import FOREGROUND_BAND, FetchJob
from tradinglab.data.prefetch.scheduler import PrefetchScheduler
from tradinglab.data.prefetch.tiers import TIER_ACTIVE, standard_tiers


def _reg():
    return SourceBucketRegistry(defaults={"range": UNLIMITED_RATE,
                                          "period": UNLIMITED_RATE})


def _sched(**kw):
    base = dict(max_inflight_global=16, max_inflight_per_source=8,
                supports_range=lambda s: s == "range")
    base.update(kw)
    return PrefetchScheduler(standard_tiers(), buckets=_reg(), **base)


def _job(source, band_index=0, symbol="AMD", interval="5m",
         tier_rank=TIER_ACTIVE, interval_rank=0):
    return FetchJob(source=source, symbol=symbol, interval=interval,
                    band_index=band_index, tier_rank=tier_rank,
                    interval_rank=interval_rank, generation=0)


def _dispatch_one(s):
    return s.next_dispatch().job


def _pending_jobs(s):
    """Drain the queue (dispatch+complete with no deepen) to inspect it."""
    out = []
    # snapshot by peeking: dispatch then re-note. We instead just dispatch all.
    while (job := s.next_dispatch().job) is not None:
        out.append(job)
        s.complete(job)  # bars_count=0 default -> no deepen while draining
    return out


# ------------------------------------------------------------- range deepening
def test_range_provider_deepens_next_band():
    s = _sched()
    s.enqueue(_job("range", band_index=0))
    job = _dispatch_one(s)
    assert job.band_index == 0
    s.complete(job, oldest_ts=1000.0, bars_count=50)
    # band 1 for the same series is now queued.
    nxt = _dispatch_one(s)
    assert nxt is not None
    assert nxt.band_index == 1 and nxt.symbol == "AMD" and nxt.interval == "5m"
    assert nxt.tier_rank == TIER_ACTIVE and nxt.interval_rank == 0


def test_range_deepens_repeatedly_while_advancing():
    s = _sched()
    s.enqueue(_job("range", band_index=0))
    j0 = _dispatch_one(s)
    s.complete(j0, oldest_ts=1000.0, bars_count=50)
    j1 = _dispatch_one(s)
    assert j1.band_index == 1
    s.complete(j1, oldest_ts=900.0, bars_count=50)   # advanced older
    j2 = _dispatch_one(s)
    assert j2.band_index == 2


def test_range_stops_when_oldest_not_advancing():
    s = _sched()
    s.enqueue(_job("range", band_index=0))
    j0 = _dispatch_one(s)
    s.complete(j0, oldest_ts=1000.0, bars_count=50)
    j1 = _dispatch_one(s)
    s.complete(j1, oldest_ts=1000.0, bars_count=50)  # NO older data
    assert _dispatch_one(s) is None
    assert s.is_exhausted("range", "AMD", "5m")


def test_range_stops_on_zero_bars():
    s = _sched()
    s.enqueue(_job("range", band_index=0))
    j0 = _dispatch_one(s)
    s.complete(j0, oldest_ts=None, bars_count=0)
    assert _dispatch_one(s) is None
    assert s.is_exhausted("range", "AMD", "5m")


# ------------------------------------------------------------ period providers
def test_period_provider_no_deeper_band():
    s = _sched()
    s.enqueue(_job("period", band_index=0))
    j0 = _dispatch_one(s)
    s.complete(j0, oldest_ts=1000.0, bars_count=50)
    assert _dispatch_one(s) is None                  # yfinance: one band, done
    assert s.is_exhausted("period", "AMD", "5m")


# -------------------------------------------------------------- no-deepen cases
def test_error_does_not_deepen():
    s = _sched()
    s.enqueue(_job("range", band_index=0))
    j0 = _dispatch_one(s)
    s.complete(j0, oldest_ts=1000.0, bars_count=0, error=RuntimeError("net"))
    assert _dispatch_one(s) is None


def test_foreground_does_not_deepen():
    s = _sched()
    s.enqueue(_job("range", band_index=FOREGROUND_BAND))
    j = _dispatch_one(s)
    assert j.is_foreground
    s.complete(j, oldest_ts=1000.0, bars_count=50)
    assert _dispatch_one(s) is None                  # foreground never deepens


def test_deepened_job_sorts_after_band0_of_other_tiers():
    s = _sched()
    s.enqueue(_job("range", band_index=0, symbol="AMD", tier_rank=TIER_ACTIVE))
    from tradinglab.data.prefetch.tiers import TIER_UNIVERSE
    s.enqueue(_job("range", band_index=0, symbol="TSLA", tier_rank=TIER_UNIVERSE))
    a = _dispatch_one(s)                              # AMD active band0
    assert a.symbol == "AMD"
    s.complete(a, oldest_ts=1000.0, bars_count=50)    # queues AMD band1
    # universe band0 (TSLA) must still come before AMD band1 (band-major).
    nxt = _dispatch_one(s)
    assert nxt.symbol == "TSLA" and nxt.band_index == 0
    after = _dispatch_one(s)
    assert after.symbol == "AMD" and after.band_index == 1
