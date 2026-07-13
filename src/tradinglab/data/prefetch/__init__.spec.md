# data/prefetch/__init__.py — Spec

## Purpose
Package root for the background **prefetch scheduler** — a priority-queue,
rate-gated, breadth-first preloader that warms the disk + working-set caches so
tangential user actions (enable compare, drill into a recent day, click a
watchlist row) feel instant. Full design: session doc
`PREFETCH_SCHEDULER_DESIGN.md`.

## Public API
Re-exports the stable primitives as they land (bottom-up build):
- `dual_interval` (from `.intervals`).

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
