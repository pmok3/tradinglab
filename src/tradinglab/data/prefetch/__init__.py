"""Background prefetch scheduler subpackage.

A priority-queue, rate-gated, breadth-first data preloader that warms the disk
(and, for the working set, in-memory) cache so tangential user actions —
enabling compare, drilling into a recent day, clicking a watchlist row — feel
instant. See the session design doc ``PREFETCH_SCHEDULER_DESIGN.md`` for the
full architecture, the relevance ladder, and the 15 approved decisions.

Built bottom-up as pure, headless, unit-tested primitives (this module tree)
that the ``FetchService`` / ``ChartApp`` wiring later composes. Nothing here
imports Tk or ``ChartApp``.
"""
from __future__ import annotations

from .appglue import (
    bucket_registry_for_mode,
    build_context,
    partition_watchlists,
    scheduler_enabled,
    scheduler_mode,
)
from .buckets import (
    AIMDRateController,
    SourceBucketRegistry,
    global_bucket_registry,
    looks_throttled,
    set_global_bucket_registry,
    unlimited_bucket_registry,
)
from .driver import PrefetchDriver
from .intervals import dual_interval
from .live import fetch_window, oldest_ts
from .planner import (
    FetchWindow,
    PeriodWindowPlanner,
    RangeWindowPlanner,
    planner_for,
)
from .priority import FOREGROUND_BAND, FetchJob, PriorityKey
from .scheduler import (
    CACHE_DISK_ONLY,
    CACHE_MEMORY_AND_DISK,
    DispatchDecision,
    PrefetchScheduler,
)
from .tiers import (
    PrefetchContext,
    TierProvider,
    expand_all,
    standard_tiers,
)

__all__ = [
    "dual_interval",
    "FOREGROUND_BAND", "FetchJob", "PriorityKey",
    "PrefetchContext", "TierProvider", "expand_all", "standard_tiers",
    "FetchWindow", "PeriodWindowPlanner", "RangeWindowPlanner", "planner_for",
    "SourceBucketRegistry", "AIMDRateController", "looks_throttled",
    "global_bucket_registry", "set_global_bucket_registry",
    "unlimited_bucket_registry",
    "DispatchDecision", "PrefetchScheduler", "PrefetchDriver",
    "CACHE_MEMORY_AND_DISK", "CACHE_DISK_ONLY",
    "fetch_window", "oldest_ts",
    "scheduler_enabled", "scheduler_mode", "bucket_registry_for_mode",
    "partition_watchlists", "build_context",
]
