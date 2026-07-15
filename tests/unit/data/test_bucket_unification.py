"""Increment 7: the Alpaca rate bucket IS the shared registry bucket.

Decision 1 — a single per-source token bucket is the ONE accounting gate for
every fetch path. Alpaca's process-wide bucket is now sourced from (and is the
same object as) the global ``SourceBucketRegistry`` bucket for ``"alpaca"``, so
the (later) scheduler and the direct Alpaca fetch path share one budget.
"""
from __future__ import annotations

from tradinglab.data.alpaca_source import _alpaca_bucket_for, _reset_tier_detection
from tradinglab.data.credentials import AlpacaCredentials
from tradinglab.data.prefetch.buckets import (
    UNLIMITED_RATE,
    global_bucket_registry,
    set_global_bucket_registry,
)


def test_global_registry_is_a_singleton():
    assert global_bucket_registry() is global_bucket_registry()


def test_alpaca_bucket_is_the_global_registry_bucket():
    try:
        b = _alpaca_bucket_for(AlpacaCredentials(tier="free"))
        assert b is global_bucket_registry().bucket_for("alpaca")
    finally:
        _alpaca_bucket_for(AlpacaCredentials(tier="free"))


def test_registry_rate_reflects_alpaca_tier_reconfigure():
    try:
        _alpaca_bucket_for(AlpacaCredentials(tier="paid"))
        assert global_bucket_registry().rate_for("alpaca") == UNLIMITED_RATE
    finally:
        _alpaca_bucket_for(AlpacaCredentials(tier="free"))
        _reset_tier_detection()
    assert global_bucket_registry().rate_for("alpaca") == 200


def test_set_global_bucket_registry_swaps_instance():
    from tradinglab.data.prefetch.buckets import SourceBucketRegistry
    original = global_bucket_registry()
    try:
        fresh = SourceBucketRegistry()
        set_global_bucket_registry(fresh)
        assert global_bucket_registry() is fresh
    finally:
        set_global_bucket_registry(original)
    assert global_bucket_registry() is original
