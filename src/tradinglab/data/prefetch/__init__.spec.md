# data/prefetch/__init__.py — Spec

## Purpose
Package root for the background **prefetch scheduler** — a priority-queue,
rate-gated, breadth-first preloader that warms the disk + working-set caches so
tangential user actions (enable compare, drill into a recent day, click a
watchlist row) feel instant. Full design: session doc
`PREFETCH_SCHEDULER_DESIGN.md`.

## Public API
Re-exports the stable primitives used by the ChartApp / FetchService wiring:
- interval policy: `dual_interval`.
- priority / job types: `FOREGROUND_BAND`, `FetchJob`, `PriorityKey`.
- tier expansion: `PrefetchContext`, `TierProvider`, `expand_all`,
  `standard_tiers`.
- window planning: `FetchWindow`, `PeriodWindowPlanner`,
  `RangeWindowPlanner`, `planner_for`.
- rate limiting: `SourceBucketRegistry`, `AIMDRateController`,
  `looks_throttled`, `global_bucket_registry`, `set_global_bucket_registry`,
  `unlimited_bucket_registry`.
- scheduler / driver: `DispatchDecision`, `PrefetchScheduler`,
  `PrefetchDriver`, `CACHE_MEMORY_AND_DISK`, `CACHE_DISK_ONLY`.
- live-mode translation + app glue: `fetch_window`, `oldest_ts`,
  `scheduler_enabled`, `scheduler_mode`, `bucket_registry_for_mode`,
  `partition_watchlists`, `build_context`.

## Design Decisions
- **Pure, headless primitives first.** Every module here is unit-testable
  without Tk / network; the `FetchService` / `ChartApp` wiring composes them
  later. No module in this package imports Tk or `ChartApp`.
- **Bottom-up, low-risk-first.** Build order: intervals → priority types →
  tier providers → window planners → per-source buckets → scheduler core →
  integration/cut-over.

## Invariants
- Importing the package has no side effects beyond re-exporting pure helpers.

## Testing
- Per-module unit tests under `tests/unit/data/prefetch/`.
