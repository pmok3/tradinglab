# data/prefetch/scheduler.py — Spec

## Purpose
The pure, headless **policy state machine** of the prefetch scheduler: priority
heap, per-tier generations, dedup/promotion, and the dispatch decision
(generation gate + inflight caps + rate-gate). No IO, no threads — a driver
loops `next_dispatch` → submit → `complete` on the Tk thread (single-threaded).

Built in TDD sub-increments. **6a (this spec):** core state machine. Later:
deepening (per-series state), retry/poison/cooldown, foreground waiters +
`foreground_pending`, `enqueue_at`/`next_wakeup` (refresh cadence), cache-policy.

## Public API (6a)
- `@dataclass(frozen=True) DispatchDecision(job: FetchJob | None,
  retry_after_s: float | None = None)`.
- `PrefetchScheduler(tiers, *, buckets: SourceBucketRegistry,
  max_inflight_global=8, max_inflight_per_source=3, clock=time.monotonic)`:
  - `rebuild(ctx, changed_ranks=None)`
  - `enqueue(job)`
  - `next_dispatch() -> DispatchDecision`
  - `complete(job, *, oldest_ts=None, bars_count=0, error=None, latency_s=None)`
  - `generation_of(rank) -> int`; `pending_count`; `inflight_count`.

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
- **complete** (6a): clears in-flight bookkeeping only.

## Testing
`tests/unit/data/prefetch/test_scheduler.py` (17) — band-major dispatch, dedup,
promote / no-demote, rebuild expand-all, generation drop, scoped rebuild keeps
active deep bands + reassigns dropped active symbol to watchlist, global +
per-source caps, rate-gate skip-to-ready-source + retry_after, complete clears
inflight, foreground-first + foreground-survives-generation-bump.
