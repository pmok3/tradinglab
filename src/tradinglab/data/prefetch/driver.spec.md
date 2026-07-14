# data/prefetch/driver.py — Spec

## Purpose
The thin orchestration layer over `PrefetchScheduler` — the seam `ChartApp`
wires into. Turns the scheduler's decisions into fetches and routes results
back, staying headless-testable via injected side-effects.

## Public API
- `PrefetchDriver(scheduler, *, submit, apply_result=None, clock=time.monotonic,
  shadow=False, max_dispatch_per_pump=256)`.
- `submit: (job) -> None` — start the async fetch (caller later calls `complete`).
- `apply_result: (job, bars, memory_allowed) -> None` — cache write.
- Methods: `set_context(ctx, changed_ranks=None)`, `request_foreground(job, *,
  cancel=None)`, `pump() -> float | None`, `complete(job, *, bars=None,
  bars_count=None, oldest_ts=None, error=None, latency_s=None, retry_after_s=None)`.
- Props: `scheduler`, `shadow`, `shadow_log`.

## Contract
- `set_context` → `scheduler.rebuild`. `request_foreground` → `enqueue` a band
  `-1` job with an optional cancel predicate.
- `pump` drains `next_dispatch` until blocked (returns `retry_after_s`) or the
  `max_dispatch_per_pump` bound (returns `0.0` → re-pump soon). **Live:** each
  job → `submit`. **Shadow:** each job appended to `shadow_log` + immediately
  `scheduler.complete(bars_count=0)` (band-0 plan only, no fetch/cache side
  effects) — the observation path for the flagged cut-over (Decision 6 revised).
- `complete` on success (`error is None` and a non-zero count) writes via
  `apply_result` **only when `bars` is provided** with
  `memory_allowed = cache_policy_for(job) == CACHE_MEMORY_AND_DISK`
  (Decision 5), then calls `scheduler.complete(bars_count=…, …)` which
  drives deepening / retry / poison / AIMD. On error it skips the cache write but
  still runs `scheduler.complete`.
- **`bars` vs `bars_count`**: pass `bars` (the fetched page) when the driver owns
  the cache write via `apply_result`. The **live app seam** does the merge+save
  on the worker thread (`apply_result=None`) and passes only `bars_count` (the
  page length) so a large page isn't marshalled back to Tk just for its length;
  deepening reads the count either way. `bars` takes precedence over `bars_count`
  when both are given; with `apply_result=None`, `apply_result` is never called.
  still feeds the scheduler (retry/poison).

## Design Decisions
- Pure/headless: `submit` + `apply_result` are injected, so the driver is
  unit-tested without Tk/network; `ChartApp` provides the real implementations
  (worker-pool submit + the `fetch_service.apply_prefetch_result` memory/disk
  split) during the flagged integration.

## Testing
`tests/unit/data/prefetch/test_driver.py` — priority-ordered dispatch, retry_after
when blocked, foreground-first, shadow records-without-submitting, complete
applies with the right memory policy (active→memory, universe→disk), error
skips-apply, complete routes bars_count for deepening.
