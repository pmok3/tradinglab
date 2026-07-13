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

from .buckets import SourceBucketRegistry
from .priority import FetchJob
from .tiers import PrefetchContext, TierProvider, expand_all


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
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tiers = list(tiers)
        self._buckets = buckets
        self.max_inflight_global = int(max_inflight_global)
        self.max_inflight_per_source = int(max_inflight_per_source)
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

    # ------------------------------------------------------------- accessors
    @property
    def pending_count(self) -> int:
        return len(self._queued)

    @property
    def inflight_count(self) -> int:
        return len(self._inflight)

    def generation_of(self, rank: int) -> int:
        return self._gen.get(rank, 0)

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
    def enqueue(self, job: FetchJob) -> None:
        dk = job.dedup_key
        if dk in self._inflight:
            return  # already fetching this exact band (attach-to-inflight: later)
        existing = self._queued.get(dk)
        if existing is not None and not self._should_replace(existing, job):
            return
        self._seq += 1
        job = job.with_seq(self._seq)
        self._queued[dk] = job
        heapq.heappush(self._heap, (job.priority(), self._seq, job))

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
                self._queued.pop(dk, None)
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
            self._queued.pop(dk, None)
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
    ) -> None:
        """Record a finished fetch. 6a: clears in-flight bookkeeping only.

        (Deepening, AIMD, retry/poison land in later sub-increments; the kwargs
        are already in the signature so the driver contract is stable.)
        """
        dk = job.dedup_key
        self._inflight.discard(dk)
        src = job.source
        if self._inflight_by_source.get(src, 0) > 0:
            self._inflight_by_source[src] -= 1


__all__ = ["DispatchDecision", "PrefetchScheduler"]
