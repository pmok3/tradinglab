# data/prefetch/scheduler.py — Spec

## Purpose
The pure, headless **policy state machine** of the prefetch scheduler: priority
heap, per-tier generations, dedup/promotion, and the dispatch decision
(generation gate + inflight caps + rate-gate). No IO, no threads — a driver
loops `next_dispatch` → submit → `complete` on the Tk thread (single-threaded).

Built in TDD sub-increments. **6a:** core state machine. **6b:** deepening.
**6c:** retry/poison/AIMD. **6d:** foreground waiters + reserve, refresh cadence,
cache-policy (this update — the scheduler core is now complete).

## Public API (6a–6d)
- `@dataclass(frozen=True) DispatchDecision(job: FetchJob | None,
  retry_after_s: float | None = None)`.
- Cache-policy constants: `CACHE_MEMORY_AND_DISK`, `CACHE_DISK_ONLY`.
- `PrefetchScheduler(tiers, *, buckets: SourceBucketRegistry,
  max_inflight_global=8, max_inflight_per_source=3,
  supports_range=lambda s: False, max_retries=3, retry_base_s=1.0,
  poison_cooldown_s=300.0, aimd_by_source=None, memory_tiers=None,
  clock=time.monotonic)`:
  - `rebuild(ctx, changed_ranks=None)`
  - `enqueue(job, *, cancel=None)`; `enqueue_at(job, not_before, *, cancel=None)`
  - `next_dispatch() -> DispatchDecision`
  - `complete(job, *, oldest_ts=None, bars_count=0, error=None, latency_s=None,
    retry_after_s=None)`
  - `generation_of(rank)`; `is_exhausted(source, symbol, interval)`;
    `is_poison(source, symbol)`; `foreground_pending(source)`;
    `next_wakeup() -> float | None`; `cache_policy_for(job) -> str`;
    `window_for(job) -> FetchWindow | None`; `pending_count`; `inflight_count`.

## Contract
- **Heap** stores `(PriorityKey, seq, FetchJob)`; the monotonic `seq` (also in
  the key) keeps entries unique so heapq never compares `FetchJob`s.
- **rebuild**: bumps generation of `changed_ranks` (or ALL when `None`), then
  **always** `expand_all` + enqueue-all. Enqueue-all (not just changed tiers) is
  the review fix — a high-tier input change can move a symbol to a lower tier;
  full expand + dedup/promotion reassigns it. Only the generation bump is scoped.
- **enqueue / dedup / promote** (`_should_replace`): keep one canonical
  `FetchJob` per `dedup_key`. Replace the existing entry when it is **stale**
  (its tier generation moved on — reassigns ownership-shifted symbols), OR the
  new job has a **better (lower) tier/interval**, OR the **same slot with a
  newer generation** (refresh). Otherwise skip. In-flight `dedup_key`s are not
  re-enqueued (attach-to-in-flight lands later).
- **generation gate**: a background job is stale when `job.generation !=
  generation_of(job.tier_rank)`; foreground (`band -1`) is exempt.
- **next_dispatch**: drop superseded (`_queued[dk] is not job`) + stale jobs;
  enforce global + per-source inflight caps; rate-gate via `bucket.try_acquire`.
  A cap/rate-blocked job is **set aside + re-pushed** so a ready job for a
  DIFFERENT source runs (no head-of-line spin). Returns the dispatched job (with
  a token consumed + marked in-flight) or `(None, retry_after_s)` where
  `retry_after_s = min bucket.time_until_available` of the blocked sources.
- **complete** (6a): clears in-flight bookkeeping. **(6b) deepening:** for a
  non-foreground, non-error completion, enqueue the next band of the
  `(source, symbol, interval)` series via the source's window planner
  (`planner_for(supports_range(source))`, cached). Scheduler-owned per-series
  state: `_series_oldest` (last accepted oldest bar) + `_exhausted`. Deepen only
  when the fetch returned bars AND `oldest_ts` advanced older than the previous
  band by ≥ `_DEEPEN_EPS`; otherwise mark the series exhausted. A `None` window
  from the planner (period providers past band 0) also exhausts. Foreground /
  errored fetches never deepen. `is_exhausted(source, symbol, interval)` exposes
  the flag. Deepened jobs keep the tier/interval rank and `band+1`, so
  band-major ordering places them after every band-0 job.
- **retry / poison / AIMD** (6c, in `complete`): a throttle signal
  (`looks_throttled(error, latency_s)`) calls the source's `AIMDRateController
  .on_throttle`; a clean success calls `.on_success`. A background **error**
  re-enqueues the same job with `_not_before = now + backoff` (backoff =
  `retry_after_s` if given, else `retry_base_s * 2^(attempt-1)`) up to
  `max_retries`; beyond that the `(source, symbol)` is quarantined in `_poison`
  until `now + poison_cooldown_s`. `next_dispatch` skips jobs whose `_not_before`
  is in the future (feeding `retry_after_s`); `enqueue` skips a poisoned symbol
  (foreground exempt). A **success** clears the key's attempt count + the
  symbol's poison. Foreground errors do NOT retry/poison (driver-owned UX).
- **foreground / refresh / cache-policy** (6d): `enqueue(job, cancel=…)` stores an
  optional cancel predicate; `next_dispatch` drops a popped job whose predicate
  returns True (`_cancelled` treats a raising probe as "keep running"). A queued
  foreground job registers in `_fg_keys`; `foreground_pending(source)` is True
  while one is queued, and `next_dispatch` **reserves** a source's tokens by
  setting aside background jobs of that source while a foreground is pending.
  `enqueue_at(job, not_before)` gates a job until a future clock time (the
  bar-aligned refresh cadence, Decision 7), reusing the `_not_before` gate;
  `next_wakeup()` reports the earliest gated time. `cache_policy_for(job)` returns
  `CACHE_MEMORY_AND_DISK` for `memory_tiers` (default active+compare) at
  `band ≤ 0` (incl. foreground), else `CACHE_DISK_ONLY` (Decision 5). `_forget`
  clears all per-dk bookkeeping when a job leaves the queue.
- **window_for(job)**: pure read that maps a job to the concrete `FetchWindow`
  the live-mode `submit` seam fetches. Band 0 (and any foreground `band ≤ 0`)
  requests the newest max window; a deepened band `k > 0` steps back from the
  `oldest_ts` boundary recorded in `_series_oldest[series_key]` when band `k-1`
  completed. Returns `None` when the provider has no such band (yfinance
  intraday `band > 0`). Does not mutate the queue.

## Testing
`tests/unit/data/prefetch/test_scheduler.py` (17) — band-major dispatch, dedup,
promote / no-demote, rebuild expand-all, generation drop, scoped rebuild keeps
active deep bands + reassigns dropped active symbol to watchlist, global +
per-source caps, rate-gate skip-to-ready-source + retry_after, complete clears
inflight, foreground-first + foreground-survives-generation-bump.
`tests/unit/data/prefetch/test_scheduler_deepen.py` (8) — range deepen next-band
/ repeat-while-advancing / stop-on-no-progress / stop-on-zero-bars, period
no-deeper-band, error + foreground no-deepen, deepened-band sorts after band-0.
`tests/unit/data/prefetch/test_scheduler_window.py` (7) — `window_for` band-0
newest-window (range + period max-period + daily full-history), deepened-band
uses recorded `oldest_ts`, purity (no queue mutation), period deep-band `None`,
foreground → band-0 window.
`tests/unit/data/prefetch/test_scheduler_retry.py` (9) — error backoff re-enqueue,
Retry-After honored, poison after max_retries, poisoned-enqueue-skipped, cooldown
expiry, success resets attempts/poison, foreground no-retry, AIMD down-on-throttle
+ up-on-success.
`tests/unit/data/prefetch/test_scheduler_foreground.py` (14) — cancel-drop,
foreground_pending transitions, per-source reserve, enqueue_at gating,
next_wakeup earliest, cache_policy (active/compare band-0 memory, deep/low-tier
disk, foreground-active memory).
