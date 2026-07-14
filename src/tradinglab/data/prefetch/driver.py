"""Scheduler orchestration driver.

The :class:`PrefetchScheduler` is a pure decision engine; the driver is the thin
orchestration layer that turns its decisions into action and routes results
back — the seam ``ChartApp`` wires into. It stays headless-testable by injecting
the two side-effecting operations:

* ``submit(job)`` — kick off the actual (async) fetch for a job. When the fetch
  finishes, the caller invokes :meth:`complete` with the bars.
* ``apply_result(job, bars, memory_allowed)`` — write the merged bars to the
  cache (the ``memory_allowed`` flag comes from the scheduler's
  ``cache_policy_for``, Decision 5).

**Shadow mode** (Decision 6 revised): :meth:`pump` records the jobs the scheduler
WOULD dispatch into :attr:`shadow_log` and does NOT call ``submit`` — the
no-side-effect observation path used to validate the scheduler against the live
reactive paths before the flag flip.

Pure — no Tk / network. See ``PREFETCH_SCHEDULER_DESIGN.md`` and the review
sequencing.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from .priority import FetchJob
from .scheduler import CACHE_MEMORY_AND_DISK, PrefetchScheduler
from .tiers import PrefetchContext

SubmitFn = Callable[[FetchJob], None]
#: ``(job, bars, memory_allowed) -> None`` — write merged bars to the cache.
ApplyFn = Callable[[FetchJob, Sequence[Any], bool], None]


class PrefetchDriver:
    def __init__(
        self,
        scheduler: PrefetchScheduler,
        *,
        submit: SubmitFn,
        apply_result: ApplyFn | None = None,
        clock: Callable[[], float] = time.monotonic,
        shadow: bool = False,
        max_dispatch_per_pump: int = 256,
    ) -> None:
        self._scheduler = scheduler
        self._submit = submit
        self._apply_result = apply_result
        self._clock = clock
        self._shadow = bool(shadow)
        self._max = int(max_dispatch_per_pump)
        #: In shadow mode, the ordered jobs the scheduler would have dispatched.
        self.shadow_log: list[FetchJob] = []

    @property
    def scheduler(self) -> PrefetchScheduler:
        return self._scheduler

    @property
    def shadow(self) -> bool:
        return self._shadow

    def set_context(
        self, ctx: PrefetchContext, changed_ranks: list[int] | None = None,
    ) -> None:
        """Re-arm the scheduler for a new app context (ticker/interval/… change)."""
        self._scheduler.rebuild(ctx, changed_ranks)

    def request_foreground(
        self, job: FetchJob, *, cancel: Callable[[], bool] | None = None,
    ) -> None:
        """Enqueue a user-blocking (band ``-1``) fetch; ``cancel`` drops it if
        the user moves on before it runs."""
        self._scheduler.enqueue(job, cancel=cancel)

    def pump(self) -> float | None:
        """Dispatch ready jobs until blocked; return ``retry_after_s`` (or None).

        In LIVE mode each dispatched job is handed to ``submit`` (the async
        fetch). In SHADOW mode it is recorded and immediately marked done so the
        queue keeps flowing for observation (no fetch, no cache write). The loop
        is bounded by ``max_dispatch_per_pump`` so a single pump can't starve the
        Tk event loop; the caller re-pumps on the next tick.
        """
        dispatched = 0
        while dispatched < self._max:
            decision = self._scheduler.next_dispatch()
            if decision.job is None:
                return decision.retry_after_s
            dispatched += 1
            if self._shadow:
                self.shadow_log.append(decision.job)
                # Release the slot; band-0 plan only (no synthetic deepening).
                self._scheduler.complete(decision.job, bars_count=0)
            else:
                self._submit(decision.job)
        return 0.0  # hit the per-pump bound → caller should pump again soon

    def complete(
        self,
        job: FetchJob,
        *,
        bars: Sequence[Any] | None = None,
        bars_count: int | None = None,
        oldest_ts: float | None = None,
        error: BaseException | None = None,
        latency_s: float | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        """Route a finished fetch: write to the cache (respecting the job's
        cache policy) on success, then feed the scheduler for
        deepening/retry/AIMD.

        Pass ``bars`` (the fetched page) when the driver owns the cache write
        via ``apply_result``. The **live app seam** instead does the merge+save
        on the worker thread and passes only ``bars_count`` (the page length) so
        a large page isn't marshalled back to Tk just for its length; deepening
        reads the count either way. ``bars`` takes precedence over ``bars_count``
        when both are given.
        """
        rows = bars if bars is not None else []
        count = len(rows) if bars is not None else int(bars_count or 0)
        if error is None and count and bars is not None and self._apply_result is not None:
            memory_allowed = (
                self._scheduler.cache_policy_for(job) == CACHE_MEMORY_AND_DISK
            )
            self._apply_result(job, rows, memory_allowed)
        self._scheduler.complete(
            job, oldest_ts=oldest_ts, bars_count=count, error=error,
            latency_s=latency_s, retry_after_s=retry_after_s,
        )


__all__ = ["PrefetchDriver", "SubmitFn", "ApplyFn"]
