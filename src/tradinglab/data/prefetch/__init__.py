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

from .buckets import (
    AIMDRateController,
    SourceBucketRegistry,
    looks_throttled,
)
from .intervals import dual_interval
from .planner import (
    FetchWindow,
    PeriodWindowPlanner,
    RangeWindowPlanner,
    planner_for,
)
from .priority import FOREGROUND_BAND, FetchJob, PriorityKey
from .scheduler import DispatchDecision, PrefetchScheduler
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
    "DispatchDecision", "PrefetchScheduler",
]
