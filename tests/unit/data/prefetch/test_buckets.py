"""Unit tests for ``data.prefetch.buckets`` — per-source rate limiting.

Contract (Decisions 1, 10): a single per-source ``TokenBucket`` is the one
accounting gate for every fetch path. yfinance is self-tuning via an AIMD
controller (no rate headers → infer throttle from 429 / Yahoo 999 / rate-limit
text / latency spikes); Alpaca keeps its header-based rate. Internal sources are
effectively unlimited.
"""
from __future__ import annotations

from tradinglab.data.prefetch.buckets import (
    CONSERVATIVE_DEFAULT_RATE,
    UNLIMITED_RATE,
    AIMDRateController,
    SourceBucketRegistry,
    looks_throttled,
)
from tradinglab.data.rate_limiter import TokenBucket


# ------------------------------------------------------ SourceBucketRegistry
def test_bucket_for_uses_default_rate():
    reg = SourceBucketRegistry()
    assert reg.bucket_for("yfinance").rate_per_min == 100.0
    assert reg.bucket_for("alpaca").rate_per_min == 200.0


def test_bucket_for_is_cached_same_instance():
    reg = SourceBucketRegistry()
    assert reg.bucket_for("yfinance") is reg.bucket_for("yfinance")


def test_unknown_source_gets_conservative_default():
    reg = SourceBucketRegistry()
    assert reg.bucket_for("mystery").rate_per_min == CONSERVATIVE_DEFAULT_RATE


def test_internal_sources_effectively_unlimited():
    reg = SourceBucketRegistry()
    b = reg.bucket_for("synthetic")
    assert b.rate_per_min == UNLIMITED_RATE
    # A long burst all succeeds (no practical throttle for offline sources).
    assert all(b.try_acquire(1) for _ in range(2000))


def test_configure_overrides_rate_live():
    reg = SourceBucketRegistry()
    reg.configure("yfinance", 42.0)
    assert reg.bucket_for("yfinance").rate_per_min == 42.0


def test_custom_defaults():
    reg = SourceBucketRegistry(defaults={"foo": 7.0})
    assert reg.bucket_for("foo").rate_per_min == 7.0


def test_source_name_normalized():
    reg = SourceBucketRegistry()
    assert reg.bucket_for(" YFinance ") is reg.bucket_for("yfinance")


# ------------------------------------------------------------ looks_throttled
def test_looks_throttled_explicit_signals():
    assert looks_throttled(Exception("HTTP 429 Too Many Requests"))
    assert looks_throttled(Exception("status 999"))
    assert looks_throttled(Exception("rate limit exceeded"))
    assert looks_throttled(RuntimeError("Too Many Requests"))


def test_looks_throttled_latency_spike():
    assert looks_throttled(None, latency_s=9.0, latency_threshold_s=5.0)
    assert not looks_throttled(None, latency_s=1.0, latency_threshold_s=5.0)


def test_looks_not_throttled_on_ordinary_error():
    assert not looks_throttled(Exception("connection reset"))
    assert not looks_throttled(None)
    assert not looks_throttled(ValueError("no data for delisted ticker"))


# --------------------------------------------------------- AIMDRateController
def _ctl(bucket=None, **kw):
    base = dict(initial=100.0, min_rate=20.0, max_rate=300.0,
                increase_step=10.0, decrease_factor=0.5, increase_every=5)
    base.update(kw)
    return AIMDRateController(bucket=bucket, **base)


def test_aimd_initial_rate():
    assert _ctl().rate == 100.0


def test_aimd_throttle_multiplicative_decrease():
    c = _ctl()
    c.on_throttle()
    assert c.rate == 50.0
    c.on_throttle()
    assert c.rate == 25.0
    c.on_throttle()
    assert c.rate == 20.0  # clamped to min_rate (25*0.5=12.5 -> 20)


def test_aimd_success_additive_increase_after_n():
    c = _ctl(initial=100.0, increase_every=5, increase_step=10.0)
    for _ in range(4):
        c.on_success()
    assert c.rate == 100.0          # not yet
    c.on_success()                  # 5th success → +step
    assert c.rate == 110.0


def test_aimd_increase_clamped_to_max():
    c = _ctl(initial=295.0, max_rate=300.0, increase_every=1, increase_step=10.0)
    c.on_success()
    assert c.rate == 300.0


def test_aimd_throttle_resets_success_streak():
    c = _ctl(increase_every=5, increase_step=10.0, initial=100.0)
    for _ in range(4):
        c.on_success()
    c.on_throttle()                 # resets the streak (and halves)
    assert c.rate == 50.0
    for _ in range(4):
        c.on_success()
    assert c.rate == 50.0           # streak restarted → no bump yet
    c.on_success()
    assert c.rate == 60.0


def test_aimd_applies_to_bucket():
    bucket = TokenBucket(100.0)
    c = _ctl(bucket=bucket)
    c.on_throttle()
    assert bucket.rate_per_min == 50.0
    c2 = _ctl(bucket=bucket, initial=50.0, increase_every=1, increase_step=25.0)
    c2.on_success()
    assert bucket.rate_per_min == 75.0
