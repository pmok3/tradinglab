"""Prefetch scheduler core — the pure, headless policy state machine.

Owns the priority heap, per-tier generations, dedup/promotion, and the dispatch
decision (generation gate + inflight caps + rate-gate). It performs NO IO and
starts NO threads: a thin driver (integration) loops ``next_dispatch`` → submit
to a worker pool → ``complete`` from the marshalled result. Everything here runs
on the Tk main thread, so it is single-threaded and lock-free by construction.

This module lands in TDD sub-increments; **6a** (this commit) is the core state
machine — enqueue/dedup/promote, per-tier generation with the *enqueue-all*
rebuild (so an ownership shift to a lower tier is never lost), and
``next_dispatch -> DispatchDecision`` that skips a rate/cap-blocked source to run
a ready job for a different source instead of spinning. Deepening, retry/poison,
foreground waiters and refresh cadence follow in later sub-increments.

See ``PREFETCH_SCHEDULER_DESIGN.md`` + the principal-SWE review actions.
"""
from __future__ import annotations

import heapq
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .buckets import AIMDRateController, SourceBucketRegistry, looks_throttled
from .planner import PeriodWindowPlanner, RangeWindowPlanner, planner_for
from .priority import FetchJob
from .tiers import (
    TIER_ACTIVE,
    TIER_COMPARE,
    PrefetchContext,
    TierProvider,
    expand_all,
)

#: Series whose newest bar advances by less than this (epoch seconds) between
#: consecutive bands are treated as exhausted (no older data returned).
_DEEPEN_EPS = 1.0

#: Cache-destination policy (Decision 5). Active/compare band-0 (the working set)
#: enter the bounded in-memory cache; everything else is disk-only.
CACHE_MEMORY_AND_DISK = "memory_and_disk"
CACHE_DISK_ONLY = "disk_only"


def _cancelled(predicate: Callable[[], bool]) -> bool:
    """Safely evaluate a cancel predicate. A raising probe = keep running
    (safe-default), matching the AIMD cancel-token policy."""
    try:
        return bool(predicate())
    except Exception:  # noqa: BLE001
        return False


@dataclass(frozen=True)
class DispatchDecision:
    """Result of :meth:`PrefetchScheduler.next_dispatch`.

    Exactly one of the two is meaningful: ``job`` is the job to run now (already
    marked in-flight + a token consumed), or ``job is None`` with
    ``retry_after_s`` = seconds until the best-blocked source frees a token
    (``None`` when the queue is simply empty).
    """

    job: FetchJob | None
    retry_after_s: float | None = None


class PrefetchScheduler:
    def __init__(
        self,
        tiers: Iterable[TierProvider],
        *,
        buckets: SourceBucketRegistry,
        max_inflight_global: int = 8,
        max_inflight_per_source: int = 3,
        supports_range: Callable[[str], bool] = lambda source: False,
        max_retries: int = 3,
        retry_base_s: float = 1.0,
        poison_cooldown_s: float = 300.0,
        aimd_by_source: dict[str, AIMDRateController] | None = None,
        memory_tiers: frozenset[int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tiers = list(tiers)
        self._buckets = buckets
        self.max_inflight_global = int(max_inflight_global)
        self.max_inflight_per_source = int(max_inflight_per_source)
        self._supports_range = supports_range
        self.max_retries = int(max_retries)
        self.retry_base_s = float(retry_base_s)
        self.poison_cooldown_s = float(poison_cooldown_s)
        self._aimd = dict(aimd_by_source or {})
        self._memory_tiers = (
            frozenset({TIER_ACTIVE, TIER_COMPARE})
            if memory_tiers is None else frozenset(memory_tiers)
        )
        self._clock = clock
        # (PriorityKey, seq, FetchJob) min-heap; seq (in the key AND the tuple)
        # keeps entries unique so heapq never compares FetchJobs.
        self._heap: list[tuple] = []
        self._seq = 0
        # dedup: dedup_key -> the CANONICAL FetchJob currently queued for it.
        # A popped job whose stored entry is not identical is a lazy-deleted dup.
        self._queued: dict[tuple, FetchJob] = {}
        self._inflight: set[tuple] = set()
        self._inflight_by_source: dict[str, int] = {}
        self._gen: dict[int, int] = {}
        # Deepening (6b): per-source planner cache + per-series progress state.
        self._planners: dict[str, PeriodWindowPlanner | RangeWindowPlanner] = {}
        self._series_oldest: dict[tuple, float] = {}
        self._exhausted: set[tuple] = set()
        # Retry / poison (6c): per-dedup-key attempt count + not-before gate,
        # and per-(source, symbol) quarantine expiry.
        self._attempts: dict[tuple, int] = {}
        self._not_before: dict[tuple, float] = {}
        self._poison: dict[tuple, float] = {}
        # Foreground / refresh (6d): queued-foreground dks (for the per-source
        # reserve) + optional per-dk cancel predicates (drop a no-longer-waited
        # foreground job).
        self._fg_keys: set[tuple] = set()
        self._cancel: dict[tuple, Callable[[], bool]] = {}

    # ------------------------------------------------------------- accessors
    @property
    def pending_count(self) -> int:
        return len(self._queued)

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    def generation_of(self, rank: int) -> int:
        return self._gen.get(rank, 0)

    def is_exhausted(self, source: str, symbol: str, interval: str) -> bool:
        return (source, symbol, interval) in self._exhausted

    def is_poison(self, source: str, symbol: str) -> bool:
        expiry = self._poison.get((source, symbol))
        return expiry is not None and expiry > self._clock()

    def foreground_pending(self, source: str) -> bool:
        """True while a foreground job for ``source`` is still queued (not yet
        dispatched) — background dispatch yields the source's tokens to it."""
        return any(k[0] == source for k in self._fg_keys)

    def next_wakeup(self) -> float | None:
        """Earliest time at which a currently time-gated job becomes eligible
        (refresh cadence / retry backoff), or ``None`` if nothing is gated."""
        times = [t for dk, t in self._not_before.items() if dk in self._queued]
        return min(times) if times else None

    def cache_policy_for(self, job: FetchJob) -> str:
        """Decision 5: active/compare band-0 (incl. foreground) → memory + disk;
        every deeper band / lower tier → disk-only."""
        if job.tier_rank in self._memory_tiers and job.band_index <= 0:
            return CACHE_MEMORY_AND_DISK
        return CACHE_DISK_ONLY

    def _planner(self, source: str):
        planner = self._planners.get(source)
        if planner is None:
            planner = planner_for(supports_range=bool(self._supports_range(source)))
            self._planners[source] = planner
        return planner

    # --------------------------------------------------------------- rebuild
    def rebuild(
        self, ctx: PrefetchContext, changed_ranks: list[int] | None = None,
    ) -> None:
        """Re-arm tiers for a new context.

        Bumps the generation of ``changed_ranks`` (or ALL tiers when ``None``),
        then ALWAYS re-expands every tier and enqueues the result. Enqueue-all
        (not just the changed tiers) is the review fix: a high-tier input change
        can move a symbol's ownership to a lower tier (e.g. the old active ticker
        that is still in a watchlist), and only a full expand + dedup/promotion
        reassigns it correctly. Unchanged tiers' jobs simply dedup against what
        is already queued.
        """
        ranks = ([t.rank for t in self._tiers]
                 if changed_ranks is None else list(changed_ranks))
        for r in ranks:
            self._gen[r] = self._gen.get(r, 0) + 1
        for job in expand_all(self._tiers, ctx, gen_of=self.generation_of):
            self.enqueue(job)

    # --------------------------------------------------------------- enqueue
    def enqueue(self, job: FetchJob, *, cancel: Callable[[], bool] | None = None) -> None:
        dk = job.dedup_key
        if dk in self._inflight:
            return  # already fetching this exact band (attach-to-inflight: later)
        if not job.is_foreground and self.is_poison(job.source, job.symbol):
            return  # quarantined symbol — skip until the cooldown expires (6c)
        existing = self._queued.get(dk)
        if existing is not None and not self._should_replace(existing, job):
            return
        self._seq += 1
        job = job.with_seq(self._seq)
        self._queued[dk] = job
        if job.is_foreground:
            self._fg_keys.add(dk)
        if cancel is not None:
            self._cancel[dk] = cancel
        heapq.heappush(self._heap, (job.priority(), self._seq, job))

    def enqueue_at(
        self, job: FetchJob, not_before: float,
        *, cancel: Callable[[], bool] | None = None,
    ) -> None:
        """Enqueue a job that must not dispatch before ``not_before`` (a future
        clock time) — the refresh-cadence primitive (Decision 7)."""
        dk = job.dedup_key
        self.enqueue(job, cancel=cancel)
        if dk in self._queued:
            self._not_before[dk] = float(not_before)

    def _forget(self, dk: tuple) -> None:
        """Remove all per-dk bookkeeping when a job leaves the queue."""
        self._queued.pop(dk, None)
        self._fg_keys.discard(dk)
        self._cancel.pop(dk, None)
        self._not_before.pop(dk, None)

    def _should_replace(self, existing: FetchJob, new: FetchJob) -> bool:
        # A stale existing entry (its tier generation moved on) is always
        # replaceable — this is what reassigns an ownership-shifted symbol.
        if self._is_stale(existing):
            return True
        ex = (existing.band_index, existing.tier_rank, existing.interval_rank)
        nw = (new.band_index, new.tier_rank, new.interval_rank)
        if nw < ex:
            return True  # better (lower) tier / interval — promote
        if nw == ex and new.generation > existing.generation:
            return True  # same slot, fresher generation — refresh
        return False

    def _is_stale(self, job: FetchJob) -> bool:
        if job.is_foreground:
            return False  # foreground bypasses tier generation
        return job.generation != self._gen.get(job.tier_rank, job.generation)

    # --------------------------------------------------------------- dispatch
    def next_dispatch(self) -> DispatchDecision:
        """Pop the best dispatchable job, or report why nothing can run.

        Drops stale-generation / superseded jobs; enforces the global + per-source
        inflight caps; rate-gates via the per-source bucket. A job blocked by a
        cap or an empty bucket is set aside (re-pushed) so a ready job for a
        *different* source can run — no head-of-line spin.
        """
        if len(self._inflight) >= self.max_inflight_global:
            return DispatchDecision(None, retry_after_s=0.05)
        set_aside: list[tuple] = []
        min_retry: float | None = None
        chosen: FetchJob | None = None
        while self._heap:
            key, seq, job = heapq.heappop(self._heap)
            dk = job.dedup_key
            if self._queued.get(dk) is not job:
                continue  # superseded/promoted duplicate — drop
            if self._is_stale(job):
                self._forget(dk)
                continue
            cancel = self._cancel.get(dk)
            if cancel is not None and _cancelled(cancel):
                self._forget(dk)  # waiter gone — drop the no-longer-wanted job
                continue
            nb = self._not_before.get(dk)
            if nb is not None and nb > self._clock():
                ra = nb - self._clock()
                min_retry = ra if min_retry is None else min(min_retry, ra)
                set_aside.append((key, seq, job))
                continue
            if not job.is_foreground and self.foreground_pending(job.source):
                # Reserve the source's tokens for the pending foreground request.
                set_aside.append((key, seq, job))
                continue
            if self._inflight_by_source.get(job.source, 0) >= self.max_inflight_per_source:
                set_aside.append((key, seq, job))
                continue
            bucket = self._buckets.bucket_for(job.source)
            if not bucket.try_acquire(1):
                ra = bucket.time_until_available(1)
                min_retry = ra if min_retry is None else min(min_retry, ra)
                set_aside.append((key, seq, job))
                continue
            chosen = job
            self._forget(dk)
            self._inflight.add(dk)
            self._inflight_by_source[job.source] = (
                self._inflight_by_source.get(job.source, 0) + 1
            )
            break
        for item in set_aside:
            heapq.heappush(self._heap, item)
        if chosen is not None:
            return DispatchDecision(chosen, retry_after_s=None)
        return DispatchDecision(None, retry_after_s=min_retry)

    # --------------------------------------------------------------- complete
    def complete(
        self,
        job: FetchJob,
        *,
        oldest_ts: float | None = None,
        bars_count: int = 0,
        error: BaseException | None = None,
        latency_s: float | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        """Record a finished fetch: in-flight bookkeeping, AIMD, retry/poison,
        then deepening.

        * **AIMD (6c):** a throttle signal (``looks_throttled``) backs the
          source's rate controller off; a clean success nudges it up.
        * **Retry / poison (6c):** a background error re-enqueues the same job
          with a backoff delay (honoring ``retry_after_s``) up to
          ``max_retries``; after that the ``(source, symbol)`` is quarantined
          for ``poison_cooldown_s``. Success clears the attempt count + poison.
          Foreground errors are the driver's UX concern — no retry/poison.
        * **Deepening (6b):** on a successful background fetch, enqueue the next
          band via the source's planner until exhausted.
        """
        dk = job.dedup_key
        self._inflight.discard(dk)
        src = job.source
        if self._inflight_by_source.get(src, 0) > 0:
            self._inflight_by_source[src] -= 1

        throttled = looks_throttled(error, latency_s=latency_s)
        aimd = self._aimd.get(src)
        if aimd is not None:
            if throttled:
                aimd.on_throttle()
            elif error is None:
                aimd.on_success()

        if job.is_foreground:
            return  # foreground: driver owns retry/UX; no deepen/quarantine

        if error is not None:
            self._handle_error(job, retry_after_s)
            return

        # Success: clear failure bookkeeping for this key/symbol, then deepen.
        self._attempts.pop(dk, None)
        self._poison.pop((src, job.symbol), None)
        self._maybe_deepen(job, oldest_ts, bars_count)

    def _handle_error(self, job: FetchJob, retry_after_s: float | None) -> None:
        dk = job.dedup_key
        attempts = self._attempts.get(dk, 0) + 1
        if attempts <= self.max_retries:
            self._attempts[dk] = attempts
            self._not_before[dk] = self._clock() + self._backoff_s(
                attempts, retry_after_s,
            )
            self.enqueue(job)
        else:
            # Retries exhausted → quarantine the symbol for a cooldown.
            self._attempts.pop(dk, None)
            self._poison[(job.source, job.symbol)] = (
                self._clock() + self.poison_cooldown_s
            )

    def _backoff_s(self, attempts: int, retry_after_s: float | None) -> float:
        if retry_after_s is not None and retry_after_s > 0:
            return float(retry_after_s)  # honor a provider Retry-After
        return self.retry_base_s * (2 ** max(0, attempts - 1))

    def _maybe_deepen(
        self, job: FetchJob, oldest_ts: float | None, bars_count: int,
    ) -> None:
        sk = job.series_key
        if sk in self._exhausted:
            return
        # No older data reached -> the series is exhausted.
        if oldest_ts is None or bars_count <= 0:
            self._exhausted.add(sk)
            return
        prev = self._series_oldest.get(sk)
        if prev is not None and oldest_ts >= prev - _DEEPEN_EPS:
            # This band did not advance older than the previous one -> exhausted.
            self._exhausted.add(sk)
            return
        self._series_oldest[sk] = oldest_ts
        next_band = job.band_index + 1
        window = self._planner(job.source).band(
            job.symbol, job.interval, next_band, oldest_ts=oldest_ts,
        )
        if window is None:
            # Provider has no deeper band (e.g. yfinance intraday) -> done.
            self._exhausted.add(sk)
            return
        self.enqueue(FetchJob(
            source=job.source, symbol=job.symbol, interval=job.interval,
            band_index=next_band, tier_rank=job.tier_rank,
            interval_rank=job.interval_rank,
            generation=self._gen.get(job.tier_rank, job.generation),
        ))


__all__ = [
    "DispatchDecision", "PrefetchScheduler",
    "CACHE_MEMORY_AND_DISK", "CACHE_DISK_ONLY",
]
