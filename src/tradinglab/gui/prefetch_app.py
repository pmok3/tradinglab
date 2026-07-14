"""PrefetchAppMixin — ChartApp glue for the (flagged) background prefetch scheduler.

A wave-4-style method-bag mixin (no ``__init__``, per AGENTS.md §7.24): the
driver instance lives on ``ChartApp`` (built in ``ChartApp.__init__`` via
:meth:`_maybe_build_prefetch_driver`), but the flag check, context snapshot, and
shadow/live observe logic live here to keep ``app.py`` under its LOC ceiling.

Gated by the ``TRADINGLAB_PREFETCH_SCHEDULER`` env flag (default OFF →
``self._prefetch_driver is None`` → zero behaviour change). In **shadow** mode
``_prefetch_observe`` logs how many jobs the scheduler WOULD dispatch with no
fetch/cache side effects; **live** mode drives real fetches through the
dedicated prefetch worker pool (worker-side merge/save; Tk-thread stash +
``complete`` + re-pump). The live paths are reachable only when the flag is
``live`` — the atomic default flip + reactive-path removal is a separate
cut-over commit. Full design: session ``PREFETCH_SCHEDULER_DESIGN.md``.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class PrefetchAppMixin:
    """ChartApp glue for the flagged background prefetch scheduler."""

    def _maybe_build_prefetch_driver(self):
        """Return a ``PrefetchDriver`` iff the feature flag is on, else ``None``."""
        try:
            from ..data.prefetch import scheduler_enabled
            if scheduler_enabled():
                return self._build_prefetch_driver()
        except Exception:  # noqa: BLE001
            pass
        return None

    def _build_prefetch_driver(self):
        from ..data.base import source_supports_range
        from ..data.prefetch import (
            PrefetchDriver,
            PrefetchScheduler,
            bucket_registry_for_mode,
            scheduler_mode,
            standard_tiers,
        )
        mode = scheduler_mode()
        scheduler = PrefetchScheduler(
            standard_tiers(),
            buckets=bucket_registry_for_mode(mode),
            supports_range=source_supports_range,
        )
        logger.info("prefetch scheduler enabled (mode=%s)", mode)
        return PrefetchDriver(
            scheduler,
            submit=self._prefetch_submit,
            apply_result=None,  # app owns ALL cache writes (worker-side merge)
            shadow=(mode != "live"),
        )

    def _prefetch_submit(self, job) -> None:
        """Live-mode async fetch for one dispatched job (never called in shadow).

        The fetch + disk merge/save run on the DEDICATED prefetch worker pool
        (`submit_prefetch`) — NOT on the Tk thread (principal-SWE review
        Must-fix: a deep 10k-bar page merge would jank the UI). The Tk-thread
        callback only stashes the merged series into the in-memory working set
        (when the job's cache policy allows), feeds the scheduler via
        `driver.complete`, then re-pumps so deepening/next jobs keep flowing.
        """
        driver = getattr(self, "_prefetch_driver", None)
        if driver is None:
            return
        from ..data.prefetch import CACHE_MEMORY_AND_DISK
        from ..data.prefetch.tiers import TIER_FOCUSED_WL, TIER_OTHER_WL

        scheduler = driver.scheduler
        window = scheduler.window_for(job)
        key = (job.source, job.symbol, job.interval)
        memory_allowed = scheduler.cache_policy_for(job) == CACHE_MEMORY_AND_DISK
        stale_guard = job.band_index <= 0
        if window is None:
            driver.complete(job, bars_count=0)
            self._prefetch_pump()
            return

        fetch_svc = self._fetch_svc
        full_cache = self._full_cache
        stash = self._stash_full_cache
        started = time.monotonic()

        def _work() -> dict:
            from .. import disk_cache
            from ..data.prefetch.live import fetch_window, oldest_ts
            bars, error, retry_after = fetch_window(
                job.source, job.symbol, job.interval, window,
            )
            if error is not None or not bars:
                return {"count": 0, "merged": None, "oldest_ts": None,
                        "error": error, "retry_after_s": retry_after}
            merged = None
            try:
                # Worker-side merge + disk save; memory_allowed=False so the
                # worker never touches the Tk-owned in-memory cache.
                merged = fetch_svc.apply_prefetch_result(
                    key, list(bars), full_cache, disk_cache, stash,
                    memory_allowed=False, stale_guard=stale_guard,
                )
            except Exception:  # noqa: BLE001
                merged = None
            return {"count": len(bars), "merged": merged,
                    "oldest_ts": oldest_ts(bars),
                    "error": None, "retry_after_s": None}

        fut = fetch_svc.submit_prefetch(_work)
        if fut is None:
            driver.complete(job, bars_count=0)
            return

        def _on_done(res) -> None:
            res = res or {}
            merged = res.get("merged")
            if memory_allowed and merged:
                try:
                    stash(key, merged)
                except Exception:  # noqa: BLE001
                    pass
            if job.tier_rank in (TIER_FOCUSED_WL, TIER_OTHER_WL) and merged:
                try:
                    self._apply_watchlist_snapshot_from_bars(
                        job.symbol, job.source, job.interval, merged)
                except Exception:  # noqa: BLE001
                    pass
            driver.complete(
                job,
                bars_count=int(res.get("count", 0)),
                oldest_ts=res.get("oldest_ts"),
                error=res.get("error"),
                latency_s=time.monotonic() - started,
                retry_after_s=res.get("retry_after_s"),
            )
            self._prefetch_pump()

        try:
            self._await_future_on_tk(fut, _on_done)
        except Exception:  # noqa: BLE001
            driver.complete(job, bars_count=0)

    def _prefetch_pump(self) -> None:
        """Dispatch ready jobs; self-reschedule on the Tk thread while gated.

        `driver.pump()` returns ``None`` (queue empty → idle; a context change or
        completion will re-pump), ``0.0`` (hit the per-pump bound → more to do
        now), or a positive ``retry_after_s`` (rate/time-gated). We re-arm a Tk
        `after` accordingly so the loop never busy-spins yet always drains.
        """
        driver = getattr(self, "_prefetch_driver", None)
        if driver is None:
            return
        try:
            retry_after = driver.pump()
        except Exception:  # noqa: BLE001
            retry_after = None
        if retry_after is None:
            return
        delay_ms = 1 if retry_after <= 0 else max(1, int(retry_after * 1000))
        try:
            self._track_after(delay_ms, self._prefetch_pump)
        except Exception:  # noqa: BLE001
            pass

    def _build_prefetch_context(self):
        """Snapshot the current app state into a ``PrefetchContext`` (or None)."""
        from ..data.prefetch import build_context, partition_watchlists
        try:
            src = self.source_var.get()
            active = self.ticker_var.get()
            interval = self.interval_var.get()
            compare = self.compare_ticker_var.get()
        except Exception:  # noqa: BLE001
            return None
        focused: list[str] = []
        other: list[str] = []
        try:
            mgr = getattr(self, "_watchlists", None)
            if mgr is not None:
                try:
                    active_wl = self.watchlist_var.get()
                except Exception:  # noqa: BLE001
                    active_wl = ""

                def _tk(name):
                    wl = mgr.get(name)
                    return getattr(wl, "tickers", ()) if wl is not None else ()

                focused, other = partition_watchlists(
                    active_wl or "", mgr.pinned_names(), _tk,
                )
        except Exception:  # noqa: BLE001
            focused, other = [], []
        return build_context(
            source=src, active_symbol=active, active_interval=interval,
            compare_symbol=compare, focused_watchlist=focused,
            other_watchlists=other, universe=(),
        )

    def _prefetch_observe(self, changed_ranks=None) -> None:
        """Re-arm the (flagged) prefetch scheduler for the current context.

        No-op when the feature is off. In shadow mode logs how many jobs the
        scheduler WOULD dispatch (no fetch, no cache write) — the observation
        path validating the scheduler against the live reactive paths before the
        cut-over.
        """
        driver = getattr(self, "_prefetch_driver", None)
        if driver is None:
            return
        try:
            if self._is_sandbox_active():
                return  # sandbox owns the slots offline — never background-prefetch
        except Exception:  # noqa: BLE001
            pass
        try:
            ctx = self._build_prefetch_context()
            if ctx is None:
                return
            driver.set_context(ctx, changed_ranks)
            self._prefetch_pump()
            if driver.shadow and driver.shadow_log:
                logger.info(
                    "prefetch-shadow: %d planned jobs for %s %s",
                    len(driver.shadow_log), ctx.active_symbol, ctx.active_interval,
                )
                driver.shadow_log.clear()
        except Exception:  # noqa: BLE001
            pass

    def _prefetch_observe_soon(self, changed_ranks=None) -> None:
        """Defer a `_prefetch_observe` to the next Tk idle (via `_track_after`).

        Keeps the re-arm off the perf-critical load path — the chokepoint in
        `_load_data_async` schedules this so a ticker/watchlist/chart-stack
        switch re-arms the scheduler without adding to ticker-switch latency.
        No-op (no timer scheduled) when the feature is off."""
        if getattr(self, "_prefetch_driver", None) is None:
            return
        try:
            self._track_after(0, self._prefetch_observe, changed_ranks)
        except Exception:  # noqa: BLE001
            pass

    def _prefetch_observe_compare(self) -> None:
        """Re-arm only the compare tier (compare toggle / symbol change)."""
        from ..data.prefetch.tiers import TIER_COMPARE
        self._prefetch_observe_soon([TIER_COMPARE])

    def _prefetch_observe_watchlists(self) -> None:
        """Re-arm the focused + other watchlist tiers (subtab / pinned change)."""
        from ..data.prefetch.tiers import TIER_FOCUSED_WL, TIER_OTHER_WL
        self._prefetch_observe_soon([TIER_FOCUSED_WL, TIER_OTHER_WL])
