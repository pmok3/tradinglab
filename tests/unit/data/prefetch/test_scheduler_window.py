"""Unit tests for ``PrefetchScheduler.window_for`` (live-submit accessor).

The scheduler owns the per-source window planner + the per-series ``oldest_ts``
progress state, so it is the single place that can translate a dispatched
``FetchJob`` back into the concrete :class:`FetchWindow` the live-mode
``submit`` seam must fetch. ``window_for`` is a **pure read** of that state (it
does not mutate the queue), so the driver can call it for any job it is about to
submit — band 0 uses the newest max window; a deepened band *k* uses the
``oldest_ts`` boundary the scheduler recorded when band *k-1* completed.
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


# ---------------------------------------------------------------- band 0 window
def test_window_for_range_band0_is_newest_max_page():
    s = _sched()
    w = s.window_for(_job("range", band_index=0))
    assert w is not None
    assert w.kind == "range"
    assert w.interval == "5m"
    assert w.end is None          # newest page — no upper bound
    assert w.limit and w.limit > 0


def test_window_for_period_band0_is_max_period():
    s = _sched()
    w = s.window_for(_job("period", band_index=0, interval="5m"))
    assert w is not None
    assert w.kind == "period"
    assert w.interval == "5m"
    assert w.period == "60d"      # yfinance 5m trailing cap


def test_window_for_period_daily_is_full_history():
    s = _sched()
    w = s.window_for(_job("period", band_index=0, interval="1d"))
    assert w is not None and w.period == "max"


# ------------------------------------------------------- deepened band window
def test_window_for_deepened_band_uses_recorded_oldest_ts():
    s = _sched()
    s.enqueue(_job("range", band_index=0))
    j0 = _dispatch_one(s)
    s.complete(j0, oldest_ts=1000.0, bars_count=50)   # records oldest for band1
    j1 = _dispatch_one(s)
    assert j1.band_index == 1
    w = s.window_for(j1)
    assert w is not None and w.kind == "range"
    assert w.end == 1000.0        # steps back from band0's oldest bar
    assert w.limit and w.limit > 0


def test_window_for_is_pure_does_not_mutate_queue():
    s = _sched()
    s.enqueue(_job("range", band_index=0))
    before = s.pending_count
    s.window_for(_job("range", band_index=0))
    assert s.pending_count == before          # read-only


def test_window_for_period_deep_band_is_none():
    # yfinance has no band > 0; window_for reflects the planner contract.
    s = _sched()
    assert s.window_for(_job("period", band_index=1)) is None


def test_window_for_foreground_band_uses_band0_window():
    # A foreground job (band -1) fetches the newest window like band 0.
    s = _sched()
    w = s.window_for(_job("range", band_index=FOREGROUND_BAND))
    assert w is not None and w.kind == "range" and w.end is None
