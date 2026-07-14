"""PrefetchAppMixin — ChartApp glue for the (flagged) background prefetch scheduler.

A wave-4-style method-bag mixin (no ``__init__``, per AGENTS.md §7.24): the
driver instance lives on ``ChartApp`` (built in ``ChartApp.__init__`` via
:meth:`_maybe_build_prefetch_driver`), but the flag check, context snapshot, and
shadow/live observe logic live here to keep ``app.py`` under its LOC ceiling.

Gated by the ``TRADINGLAB_PREFETCH_SCHEDULER`` env flag (default OFF →
``self._prefetch_driver is None`` → zero behaviour change). In **shadow** mode
``_prefetch_observe`` logs how many jobs the scheduler WOULD dispatch with no
fetch/cache side effects; **live** mode (wired at the cut-over) drives fetches.
Full design: session ``PREFETCH_SCHEDULER_DESIGN.md``.
"""
from __future__ import annotations

import logging

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
            global_bucket_registry,
            scheduler_mode,
            standard_tiers,
        )
        scheduler = PrefetchScheduler(
            standard_tiers(),
            buckets=global_bucket_registry(),
            supports_range=source_supports_range,
        )
        mode = scheduler_mode()
        logger.info("prefetch scheduler enabled (mode=%s)", mode)
        return PrefetchDriver(
            scheduler,
            submit=self._prefetch_submit,
            apply_result=self._prefetch_apply,
            shadow=(mode != "live"),
        )

    def _prefetch_submit(self, job) -> None:
        """Live-mode async fetch submission — wired at the cut-over; unused in
        shadow mode."""

    def _prefetch_apply(self, job, bars, memory_allowed) -> None:
        """Live-mode cache write — wired at the cut-over; unused in shadow mode."""

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
            ctx = self._build_prefetch_context()
            if ctx is None:
                return
            driver.set_context(ctx, changed_ranks)
            driver.pump()
            if driver.shadow and driver.shadow_log:
                logger.info(
                    "prefetch-shadow: %d planned jobs for %s %s",
                    len(driver.shadow_log), ctx.active_symbol, ctx.active_interval,
                )
                driver.shadow_log.clear()
        except Exception:  # noqa: BLE001
            pass
