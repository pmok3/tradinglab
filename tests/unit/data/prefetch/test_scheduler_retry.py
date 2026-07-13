"""Unit tests for scheduler retry / poison / AIMD (6c).

Contract (Decision 12 + review): a background fetch error re-enqueues the same
job with a backoff delay (honoring a provider Retry-After) up to ``max_retries``
times; after that the ``(source, symbol)`` is quarantined ("poison") for a
cooldown, and future enqueues of it are skipped. Success clears the attempt
count + poison. A throttle signal drives the source's AIMD controller down; a
clean success nudges it up. Foreground errors are the driver's UX concern — no
scheduler retry/poison.
"""
from __future__ import annotations

from tradinglab.data.prefetch.buckets import (
    UNLIMITED_RATE,
    AIMDRateController,
    SourceBucketRegistry,
)
from tradinglab.data.prefetch.priority import FOREGROUND_BAND, FetchJob
from tradinglab.data.prefetch.scheduler import PrefetchScheduler
from tradinglab.data.prefetch.tiers import TIER_ACTIVE, standard_tiers


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _sched(clock, **kw):
    reg = SourceBucketRegistry(defaults={"a": UNLIMITED_RATE}, clock=clock)
    base = dict(max_inflight_global=16, max_inflight_per_source=8,
                max_retries=2, retry_base_s=10.0, poison_cooldown_s=300.0)
    base.update(kw)
    return PrefetchScheduler(standard_tiers(), buckets=reg, clock=clock, **base)


def _job(band_index=0, symbol="AMD", interval="5m", tier_rank=TIER_ACTIVE):
    return FetchJob(source="a", symbol=symbol, interval=interval,
                    band_index=band_index, tier_rank=tier_rank,
                    interval_rank=0, generation=0)


def _dispatch(s):
    return s.next_dispatch().job


# ------------------------------------------------------------------ retry
def test_error_reenqueues_with_backoff():
    c = _Clock()
    s = _sched(c)
    s.enqueue(_job())
    j = _dispatch(s)
    s.complete(j, error=RuntimeError("net"))
    # re-enqueued but gated by backoff -> not dispatchable yet
    dd = s.next_dispatch()
    assert dd.job is None and dd.retry_after_s and dd.retry_after_s > 0
    c.advance(11.0)                     # past the 10s base backoff
    assert _dispatch(s) is not None     # now retriable


def test_retry_after_header_honored():
    c = _Clock()
    s = _sched(c)
    s.enqueue(_job())
    j = _dispatch(s)
    s.complete(j, error=RuntimeError("429"), retry_after_s=50.0)
    c.advance(11.0)                     # past base backoff but NOT the 50s header
    assert _dispatch(s) is None
    c.advance(40.0)
    assert _dispatch(s) is not None


def test_poison_after_max_retries():
    c = _Clock()
    s = _sched(c, max_retries=1)
    s.enqueue(_job())
    j = _dispatch(s)
    s.complete(j, error=RuntimeError("net"))   # attempt 1 -> retry
    c.advance(11.0)
    j = _dispatch(s)
    s.complete(j, error=RuntimeError("net"))   # attempt 2 > max_retries -> poison
    assert s.is_poison("a", "AMD")
    assert _dispatch(s) is None


def test_poisoned_symbol_enqueue_skipped():
    c = _Clock()
    s = _sched(c, max_retries=0)
    s.enqueue(_job())
    j = _dispatch(s)
    s.complete(j, error=RuntimeError("net"))   # 0 retries -> immediate poison
    assert s.is_poison("a", "AMD")
    before = s.pending_count
    s.enqueue(_job())                          # poisoned -> skipped
    assert s.pending_count == before


def test_poison_cooldown_expires():
    c = _Clock()
    s = _sched(c, max_retries=0, poison_cooldown_s=300.0)
    s.enqueue(_job())
    s.complete(_dispatch(s), error=RuntimeError("net"))
    assert s.is_poison("a", "AMD")
    c.advance(301.0)
    assert not s.is_poison("a", "AMD")
    s.enqueue(_job())                          # cooldown expired -> allowed
    assert s.pending_count == 1


def test_success_resets_attempts_and_poison():
    c = _Clock()
    s = _sched(c, max_retries=2)
    s.enqueue(_job())
    s.complete(_dispatch(s), error=RuntimeError("net"))   # attempt 1
    c.advance(11.0)
    s.complete(_dispatch(s), oldest_ts=1000.0, bars_count=10)  # success -> reset
    c.advance(11.0)
    # a fresh failure now starts the counter over (not immediately poisoned)
    s.enqueue(_job())
    s.complete(_dispatch(s), error=RuntimeError("net"))
    assert not s.is_poison("a", "AMD")


def test_foreground_error_no_retry_no_poison():
    c = _Clock()
    s = _sched(c, max_retries=0)
    s.enqueue(_job(band_index=FOREGROUND_BAND))
    j = _dispatch(s)
    s.complete(j, error=RuntimeError("net"))
    assert not s.is_poison("a", "AMD")
    assert _dispatch(s) is None                # not re-enqueued


# ------------------------------------------------------------------- AIMD
def test_aimd_throttle_on_throttle_error():
    c = _Clock()
    reg = SourceBucketRegistry(defaults={"a": UNLIMITED_RATE}, clock=c)
    aimd = AIMDRateController(initial=100.0, min_rate=20.0, max_rate=300.0,
                             decrease_factor=0.5, bucket=reg.bucket_for("a"))
    s = PrefetchScheduler(standard_tiers(), buckets=reg, clock=c,
                          aimd_by_source={"a": aimd}, max_retries=3)
    s.enqueue(_job())
    j = _dispatch(s)
    s.complete(j, error=RuntimeError("HTTP 429"), latency_s=0.1)
    assert aimd.rate == 50.0                   # multiplicative decrease


def test_aimd_success_nudges_up():
    c = _Clock()
    reg = SourceBucketRegistry(defaults={"a": UNLIMITED_RATE}, clock=c)
    aimd = AIMDRateController(initial=100.0, min_rate=20.0, max_rate=300.0,
                             increase_every=1, increase_step=10.0,
                             bucket=reg.bucket_for("a"))
    s = PrefetchScheduler(standard_tiers(), buckets=reg, clock=c,
                          aimd_by_source={"a": aimd})
    s.enqueue(_job())
    j = _dispatch(s)
    s.complete(j, oldest_ts=1000.0, bars_count=10)
    assert aimd.rate == 110.0
