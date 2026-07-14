"""Drill-down (1d → 5m double-click) controller for ChartApp.

Hosts the request-tracking dataclass and all eight callbacks that
implement the deferred / retargetable / context-aware drill-down flow:
double-click on a 1d candle either drills down immediately (cache hit)
or queues a request that gets retried after prefetch grace, falls back
to a sync fetch, and is invalidated if the user changes context before
it lands.

Mixin rules (see decomposition plan):
* No ``__init__``.
* No cooperative ``super()`` — method resolution relies on plain MRO.
* No name collisions with other mixins or ``ChartApp``.

Required instance state on ChartApp (initialised in ``__init__``):

* ``source_var``, ``ticker_var``, ``interval_var`` — Tk vars.
* ``_full_cache``, ``_prefetch_futures``, ``_executor``, ``_after_jobs``,
  ``_panel_state``, ``_primary``, ``_canvas``, ``_status``.
* ``_drilldown_request`` (Optional[_DrilldownRequest]),
  ``_drilldown_request_seq`` (int), ``_drilldown_day`` (date | None).
* ``_fetch_token`` (int) — used to invalidate stale completions.
* ``_preserve_xlim_on_render``, ``_poll_retry_count``,
  ``_poll_retry_expected_min_ts``.
* class attrs ``_DRILLDOWN_PREFETCH_GRACE_MS``,
  ``_DRILLDOWN_SYNC_UI_TIMEOUT_MS``.

Methods delegated back to ChartApp (called via ``self.``):
``_track_after``, ``_disk_load``, ``_trim_full_cache``,
``_await_future_on_tk``, ``_load_data``, ``_ensure_rendered_for_view``,
``_autoscale_y_to_visible``, ``_render``.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date as _date_t
from datetime import datetime as _datetime_t
from datetime import timedelta
from datetime import timezone as _timezone
from typing import Any

from .. import disk_cache
from ..constants import provider_lookback_days, targeted_window
from ..data import DATA_SOURCES, coverage, fetch_range, source_supports_range
from ..models import Candle


def _day_to_ts(day) -> int:
    """Midnight-UTC epoch seconds for a ``datetime.date`` centering anchor.

    Used only to *center* the targeted page-span window on the clicked day;
    the exact wall-clock reference is unimportant (the window spans ~one
    API page around it), so UTC midnight is a stable, tz-library-free
    choice that never raises on a headless / missing-tzdata host.
    """
    return int(
        _datetime_t(day.year, day.month, day.day, tzinfo=_timezone.utc).timestamp()
    )

# --- drill-down request tracking ----------------------------------------

@dataclass
class _DrilldownRequest:
    """Outstanding drill-down (1d → 5m) for a specific (src, ticker, day).

    Created on a click that arrives before the 5m cache is ready. A request
    is **valid** iff (a) it is the current ``ChartApp._drilldown_request``
    and (b) its ``(src, ticker)`` still matches the active selection.
    Every callback (timer fire, future complete, UI timeout) re-validates
    before acting, so a ticker/source switch silently drops stale work
    instead of drilling into the wrong chart.

    ``fetch_token`` is captured at creation for diagnostic logging only —
    it is **not** part of the validity check. A bare token-mismatch was
    used in earlier revisions, but it produced a real race: any in-flight
    ``_load_data`` (e.g. the async kickoff from typing a ticker, or a
    periodic ``_next_bar_fetch_tick``) bumps the token even when (src,
    ticker) is unchanged, and would silently invalidate a perfectly
    legitimate pending drill. The (src, ticker) check is sufficient — if
    those still match, the user's intent has not changed.

    Mutating ``day`` is the latest-click-wins retarget path: a second
    double-click on the same ticker updates ``day`` in place rather than
    spawning a new request, so the in-flight fetch's eventual completion
    drills to the most recently requested day.
    """

    request_id: int
    fetch_token: int
    src: str
    ticker: str
    day: Any  # datetime.date
    timer_job: str | None = None          # _track_after handle for 1.5s deadline
    ui_timeout_job: str | None = None     # _track_after handle for 5s UI deadline
    future: Future | None = None          # the in-flight fetch future, if any
    cursor_set: bool = False              # whether we set the wait cursor
    compare_ticker: str = ""              # active compare symbol (targeted fetch)


class DrilldownMixin:
    """Drill-down (1d → 5m) request lifecycle for ChartApp."""

    def _zoom_5m_for_date(self, day) -> bool:
        """Switch to 5m and zoom the primary panel to bars on ``day``.

        Triggered by a double-click on a 1d candle (handled in
        :class:`InteractionMixin`). Three branches:

        1. **Cache hit + day covered**: drill down immediately, return
           True. Any outstanding pending request is superseded.
        2. **Cache hit, day not covered**: the cache can be stale or only
           partially companion-prefetched, so if the day is within the
           provider's intraday window (a fresh 5m fetch could reach it —
           the same data a manual 5m toggle loads) fall through to the
           fetch path (branch 3). Only when the day predates the window
           (genuinely beyond yfinance's ~60d intraday limit) emit a status
           WARN and return False.
        3. **Cache missing** (race vs companion prefetch): create or
           retarget a :class:`_DrilldownRequest`, schedule a 1.5s grace
           timer to wait for the in-flight prefetch, then fall back to
           a synchronous fetch (with a 5s UI deadline) if it's still
           missing. Latest-click-wins: a second click on the same ticker
           updates the pending day in place rather than spawning a new
           fetch. Returns False (the actual drill-down happens later).
        """
        if not isinstance(day, _date_t):
            return False
        src = self.source_var.get()
        ticker = self.ticker_var.get().strip().upper()
        if not src or not ticker:
            return False

        cached = self._full_cache.get((src, ticker, "5m"))
        if cached:
            # Verify the cache actually covers the clicked day.
            has_day = False
            oldest_5m_day = None
            for c in cached:
                try:
                    if not getattr(c, "is_gap", False):
                        cd = c.date.date()
                        if oldest_5m_day is None or cd < oldest_5m_day:
                            oldest_5m_day = cd
                        if cd == day:
                            has_day = True
                            break
                except Exception:  # noqa: BLE001
                    continue
            if has_day:
                # Branch 1: success path. Cancel any outstanding pending
                # request — this drill supersedes it.
                req = self._drilldown_request
                if req is not None:
                    self._finish_drilldown_request(req)
                return self._do_drilldown(day)
            # Branch 2: the clicked day isn't in this 5m cache. The cache
            # may be stale or only partially companion-prefetched (the
            # user is sitting on the 1d chart), so a fresh fetch can often
            # still reach the day — exactly what a manual 5m toggle does.
            # Only treat it as genuinely unavailable when the day predates
            # the provider's intraday window; otherwise fall through to
            # the fetch path (Branch 3) below.
            if not self._day_within_intraday_fetch_window(day, "5m"):
                try:
                    self._status.warn(
                        f"Drill-down no-op: 5m data only available from "
                        f"{oldest_5m_day} onward — requested {day}")
                except Exception:  # noqa: BLE001
                    pass
                req = self._drilldown_request
                if req is not None:
                    self._finish_drilldown_request(req)
                return False
            # In-window but not in this cache → fetch it (fall through to
            # Branch 3, identical to a cold cache miss).

        # Branch 3: cache missing, or cached-but-incomplete within the
        # provider window. Queue or retarget a request.
        existing = self._drilldown_request
        if (
            existing is not None
            and existing.src == src
            and existing.ticker == ticker
        ):
            # Latest-click-wins: retarget the pending day in place.
            # If the sync fetch is already in flight, the future's
            # completion handler will pick up the new day. Otherwise
            # cancel and reschedule the grace timer from "now".
            old_day = existing.day
            self._drilldown_request_seq += 1
            existing.request_id = self._drilldown_request_seq
            existing.day = day
            if existing.future is None:
                # Still waiting on the grace timer — reset its deadline.
                if existing.timer_job is not None:
                    try:
                        self.after_cancel(existing.timer_job)
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        self._after_jobs.discard(existing.timer_job)
                    except Exception:  # noqa: BLE001
                        pass
                req_id = existing.request_id
                existing.timer_job = self._track_after(
                    self._DRILLDOWN_PREFETCH_GRACE_MS,
                    self._retry_drilldown_after_prefetch,
                    req_id,
                )
            try:
                self._status.info(
                    f"Drill-down retargeted: {old_day} → {day} "
                    f"({ticker} 5m still loading)")
            except Exception:  # noqa: BLE001
                pass
            return False

        # If a different ticker has a pending request, finish it (the
        # ticker change will have advanced the fetch token, so it'd be
        # invalidated anyway — clean up explicitly).
        if existing is not None:
            self._finish_drilldown_request(existing)

        self._drilldown_request_seq += 1
        req = _DrilldownRequest(
            request_id=self._drilldown_request_seq,
            fetch_token=self._fetch_token,
            src=src,
            ticker=ticker,
            day=day,
        )
        self._drilldown_request = req
        try:
            self._status.info(
                f"Drill-down queued: {ticker} 5m for {day} — "
                f"waiting up to {self._DRILLDOWN_PREFETCH_GRACE_MS}ms "
                "for prefetch to land")
        except Exception:  # noqa: BLE001
            pass
        req.timer_job = self._track_after(
            self._DRILLDOWN_PREFETCH_GRACE_MS,
            self._retry_drilldown_after_prefetch,
            req.request_id,
        )
        return False

    def _day_within_intraday_fetch_window(
        self, day, interval: str = "5m",
    ) -> bool:
        """True if a fresh ``interval`` fetch could plausibly include ``day``.

        Two regimes, by provider capability:

        * **Range-capable providers** (Alpaca — see
          :func:`tradinglab.data.source_supports_range`) fetch any historical
          day on demand via a targeted page-span window, so the reachable set
          is "any day at or after the provider's data start". That floor is
          the learned coverage watermark
          (:func:`tradinglab.data.coverage.data_start`); when unknown we
          assume reachable and let the first fetch discover the true floor.
        * **Trailing-window providers** (yfinance) are capped to
          :func:`tradinglab.constants.provider_lookback_days` for the active
          ``source_var`` (~60-day intraday); a day inside that window is
          fetchable even when the current cache doesn't contain it — the
          cache may be stale or only partially companion-prefetched while the
          user sits on the 1d chart. A day that predates the window is
          genuinely beyond the provider's reach.

        Deliberately generous: a one-week buffer is added (both regimes) so
        boundary days / provider jitter never produce a false "unavailable".
        A day that turns out to be just out of reach simply triggers a fetch
        that returns no coverage, after which
        :meth:`_on_drilldown_fetch_done` warns with the accurate range.
        """
        if not isinstance(day, _date_t):
            return False
        try:
            src = self.source_var.get()
        except Exception:  # noqa: BLE001
            src = ""
        # Range-capable providers (Alpaca) fetch any historical day on
        # demand — the reachable set is not a trailing window but "any day
        # at or after the provider's data start". Use the learned coverage
        # watermark when we have one; otherwise assume reachable and let the
        # fetch itself discover the true floor (record_fetch then learns it).
        try:
            range_capable = source_supports_range(src)
        except Exception:  # noqa: BLE001
            range_capable = False
        if range_capable:
            try:
                sym = self.ticker_var.get().strip().upper()
                cov = coverage.load(src, sym, interval)
                ds = coverage.data_start(cov)
            except Exception:  # noqa: BLE001
                ds = None
            if ds is None:
                return True
            try:
                day_ts = _day_to_ts(day)
            except Exception:  # noqa: BLE001
                return True
            # One-week buffer mirrors the trailing-window generosity below.
            return day_ts >= ds - 7 * 86400
        window_days = provider_lookback_days(src, interval)
        cutoff = _date_t.today() - timedelta(days=window_days + 7)
        return day >= cutoff

    def _drilldown_request_is_valid(
        self, req: _DrilldownRequest | None,
    ) -> bool:
        """True iff ``req`` is still the current, context-matching request.

        Note: deliberately does NOT compare ``req.fetch_token`` against
        ``self._fetch_token``. The token bumps every ``_load_data`` /
        ``_load_data_async`` / ``_next_bar_fetch_tick`` call, including
        ones that don't change (src, ticker) — e.g. the async load
        completion that fires after a fresh ticker entry. Checking it
        here used to invalidate legitimate pending drill-downs whenever
        such an internal load happened to complete in the window between
        click and grace-timer-fire (test d38 sub-test C reproduced this
        under CPU contention). The (src, ticker) check below is the real
        invariant: as long as the active selection still matches, the
        user's drill-down intent is still meaningful.
        """
        if req is None or req is not self._drilldown_request:
            return False
        try:
            cur_src = self.source_var.get()
            cur_ticker = self.ticker_var.get().strip().upper()
        except Exception:  # noqa: BLE001
            return False
        return req.src == cur_src and req.ticker == cur_ticker

    def _retry_drilldown_after_prefetch(self, request_id: int) -> None:
        """Grace-period timer fired: prefetch had 1.5s; check the cache.

        Re-validates the request so a stale timer (ticker changed, etc.)
        is silently dropped. If the cache is now ready, drill down. If
        the day isn't covered, surface the limit. Otherwise fall through
        to the sync-fetch fallback.
        """
        req = self._drilldown_request
        if req is None or req.request_id != request_id:
            return  # superseded
        req.timer_job = None
        if not self._drilldown_request_is_valid(req):
            self._finish_drilldown_request(req)
            return
        cached = self._full_cache.get((req.src, req.ticker, "5m"))
        if cached:
            has_day = False
            oldest_5m_day = None
            for c in cached:
                try:
                    if not getattr(c, "is_gap", False):
                        cd = c.date.date()
                        if oldest_5m_day is None or cd < oldest_5m_day:
                            oldest_5m_day = cd
                        if cd == req.day:
                            has_day = True
                            break
                except Exception:  # noqa: BLE001
                    continue
            if has_day:
                day = req.day
                self._finish_drilldown_request(req)
                self._do_drilldown(day)
                return
            # Day still not in the prefetched cache. If a fresh fetch can
            # reach it (within the provider's intraday window), fall back
            # to the sync fetch — same as a cold cache miss. Only warn
            # when the day genuinely predates the window.
            if self._day_within_intraday_fetch_window(req.day, "5m"):
                self._drilldown_sync_fetch(req)
                return
            try:
                self._status.warn(
                    f"Drill-down no-op: 5m data only available from "
                    f"{oldest_5m_day} onward — requested {req.day}")
            except Exception:  # noqa: BLE001
                pass
            self._finish_drilldown_request(req)
            return
        # Still no cache after grace period — fall back to a sync fetch.
        self._drilldown_sync_fetch(req)

    def _drilldown_sync_fetch(self, req: _DrilldownRequest) -> None:
        """Fallback: fetch 5m bars for ``req.ticker`` and drill on completion.

        Reuses an existing companion-prefetch future if one is already
        running for the same key (rubber-duck concern #5: don't duplicate).
        Otherwise submits a new fetch on the shared executor. The 5s UI
        deadline restores the cursor and emits an ERROR so the user
        isn't left staring at a stuck cursor; the underlying fetch is
        NOT cancelled (yfinance synchronous HTTP) — if it eventually
        returns and the request is still valid, the drill-down still
        happens.
        """
        if not self._drilldown_request_is_valid(req):
            self._finish_drilldown_request(req)
            return
        if self._executor is None:
            try:
                self._status.error(
                    "Drill-down sync fetch failed: executor unavailable")
            except Exception:  # noqa: BLE001
                pass
            self._finish_drilldown_request(req)
            return
        key = (req.src, req.ticker, "5m")
        # Range-capable providers (Alpaca) drill into arbitrarily old days,
        # so a trailing-window companion prefetch won't contain the target
        # day — do NOT attach to one. Instead fetch a targeted page-span
        # window centered on req.day.
        try:
            range_capable = source_supports_range(req.src)
        except Exception:  # noqa: BLE001
            range_capable = False
        existing_fut = None if range_capable else self._prefetch_futures.get(key)
        if existing_fut is not None and not existing_fut.done():
            req.future = existing_fut
            try:
                self._status.info(
                    f"Drill-down attaching to in-flight prefetch: "
                    f"{req.ticker} 5m")
            except Exception:  # noqa: BLE001
                pass
        else:
            fetcher = DATA_SOURCES.get(req.src)
            if fetcher is None and not range_capable:
                try:
                    self._status.error(
                        "Drill-down sync fetch failed: data source "
                        "is unavailable")
                except Exception:  # noqa: BLE001
                    pass
                self._finish_drilldown_request(req)
                return
            try:
                self._status.info(
                    f"Fetching 5m for {req.ticker}… (drill-down)")
            except Exception:  # noqa: BLE001
                pass

            if range_capable:
                # Capture the active compare symbol on the Tk thread — a
                # worker must never read a Tk variable. The compare range is
                # fetched + merged to disk inside the worker so the drill's
                # re-render shows an aligned RS line.
                compare = ""
                try:
                    if bool(self.compare_var.get()):
                        compare = (
                            self.compare_ticker_var.get() or ""
                        ).strip().upper()
                except Exception:  # noqa: BLE001
                    compare = ""
                req.compare_ticker = compare
                now_ts = int(time.time())

                def _work(
                    src=req.src, t=req.ticker, cmp=compare,
                    day=req.day, now=now_ts,
                ):
                    if cmp and cmp != t:
                        # Fetch primary + compare CONCURRENTLY. Both are
                        # network-bound (the HTTP call releases the GIL), so a
                        # tiny 2-worker pool roughly halves the drill fetch
                        # vs. the old sequential primary-then-compare form —
                        # the single dominant cost on a slow range provider.
                        # A LOCAL pool (not self._executor) avoids contending
                        # with / deadlocking against the shared fetch workers.
                        with ThreadPoolExecutor(
                            max_workers=2, thread_name_prefix="drill-fetch",
                        ) as pool:
                            f_primary = pool.submit(
                                self._targeted_range_fetch,
                                src, t, "5m", day, now, merge_to_disk=False,
                            )
                            f_compare = pool.submit(
                                self._targeted_range_fetch,
                                src, cmp, "5m", day, now, merge_to_disk=True,
                            )
                            try:
                                primary = f_primary.result()
                            except Exception:  # noqa: BLE001
                                primary = []
                            try:
                                f_compare.result()
                            except Exception:  # noqa: BLE001
                                pass
                        return primary
                    return self._targeted_range_fetch(
                        src, t, "5m", day, now, merge_to_disk=False,
                    )
            else:
                def _work(t=req.ticker):
                    try:
                        return fetcher(t, "5m") or []
                    except Exception:  # noqa: BLE001
                        return []

            try:
                req.future = self._executor.submit(_work)
                self._prefetch_futures[key] = req.future
                req.future.add_done_callback(
                    lambda _f, _key=key: self._prefetch_futures.pop(_key, None),
                )
            except Exception:  # noqa: BLE001
                try:
                    self._status.error(
                        "Drill-down sync fetch failed: executor rejected")
                except Exception:  # noqa: BLE001
                    pass
                self._finish_drilldown_request(req)
                return
        # Wait cursor + UI timeout. The cursor is restored either when
        # the future completes or when the UI deadline fires, whichever
        # comes first. _finish_drilldown_request is the single restore
        # path so the cursor cannot get stuck.
        try:
            self.config(cursor="watch")
            req.cursor_set = True
        except Exception:  # noqa: BLE001
            pass
        req_id = req.request_id
        req.ui_timeout_job = self._track_after(
            self._DRILLDOWN_SYNC_UI_TIMEOUT_MS,
            self._on_drilldown_sync_ui_timeout,
            req_id,
        )
        # Marshal completion back to the Tk thread via the existing
        # poll-based helper (raw after() from worker threads is unsafe
        # on this Tk build — see _await_future_on_tk docstring).
        self._await_future_on_tk(
            req.future,
            lambda result, _rid=req_id: self._on_drilldown_fetch_done(
                _rid, result,
            ),
        )

    def _targeted_range_fetch(
        self, src: str, sym: str, interval: str, day, now_ts: int,
        *, merge_to_disk: bool,
    ) -> list[Candle]:
        """Worker-thread: fetch ~1 API page around ``day`` for ``sym``.

        Computes a page-span window centered on ``day``
        (:func:`tradinglab.constants.targeted_window`, clamped to ``now_ts``
        and the learned provider data-start), records the attempt in the
        coverage sidecar, and returns the fetched bars. When the window is
        already covered, returns the on-disk series so the caller's merge
        still finds the requested day without a network round-trip.

        ``merge_to_disk`` persists the fetched bars into the JSONL cache
        here — used for the **compare** symbol, whose result is not
        otherwise written by :meth:`_on_drilldown_fetch_done` (that handler
        only merges the primary ``result``).

        Never raises and never touches Tk state — safe on ``self._executor``.
        On an unsupported / error status it degrades to the plain
        trailing-window fetcher so the drill still lands with recent bars
        (matching non-range provider behavior).
        """
        try:
            cov = coverage.load(src, sym, interval)
            if not cov.segments:
                cov = coverage.bootstrap_from_cache(src, sym, interval)
            data_start = coverage.data_start(cov)
            day_ts = _day_to_ts(day)
            start_ts, end_ts = targeted_window(
                interval, day_ts, now_ts=now_ts, data_start_ts=data_start,
            )
        except Exception:  # noqa: BLE001
            return self._trailing_fetch(src, sym, interval)
        try:
            already = coverage.covered(cov, start_ts, end_ts)
        except Exception:  # noqa: BLE001
            already = False
        if already:
            try:
                return disk_cache.load(src, sym, interval) or []
            except Exception:  # noqa: BLE001
                return []
        try:
            bars, status = fetch_range(src, sym, interval, start_ts, end_ts)
        except Exception:  # noqa: BLE001
            bars, status = None, "error"
        if status == "ok" and bars:
            try:
                returned_start = int(bars[0].date.timestamp())
                returned_end = int(bars[-1].date.timestamp()) + 1
                coverage.record_fetch(
                    src, sym, interval, start_ts, end_ts,
                    returned_start, returned_end,
                )
            except Exception:  # noqa: BLE001
                pass
            if merge_to_disk:
                try:
                    existing = disk_cache.load(src, sym, interval) or []
                    merged = (
                        disk_cache.merge_candles(existing, bars)
                        if existing else list(bars)
                    )
                    disk_cache.save(src, sym, interval, merged)
                except Exception:  # noqa: BLE001
                    pass
            return list(bars)
        if status == "empty":
            # Provider genuinely has no bars in this window — remember the
            # attempt so we don't refetch it on the next drill.
            try:
                coverage.record_fetch(
                    src, sym, interval, start_ts, end_ts, None, None,
                )
            except Exception:  # noqa: BLE001
                pass
            return []
        # "unsupported" / "error" → trailing-window fallback.
        return self._trailing_fetch(src, sym, interval)

    def _trailing_fetch(
        self, src: str, sym: str, interval: str,
    ) -> list[Candle]:
        """Worker-thread: plain trailing-window fetch via the source fetcher.

        The graceful-degradation path for :meth:`_targeted_range_fetch` when
        a range fetch is unsupported or errors. Never raises.
        """
        fetcher = DATA_SOURCES.get(src)
        if fetcher is None:
            return []
        try:
            return fetcher(sym, interval) or []
        except Exception:  # noqa: BLE001
            return []

    def _on_drilldown_sync_ui_timeout(self, request_id: int) -> None:
        """5s UI deadline fired: restore cursor + status, but keep waiting."""
        req = self._drilldown_request
        if req is None or req.request_id != request_id:
            return
        if req.future is None or req.future.done():
            # Already completed; the fetch-done handler will (or did)
            # restore the cursor.
            return
        req.ui_timeout_job = None
        if req.cursor_set:
            try:
                self.config(cursor="")
            except Exception:  # noqa: BLE001
                pass
            req.cursor_set = False
        try:
            self._status.error(
                f"5m fetch for {req.ticker} taking >"
                f"{self._DRILLDOWN_SYNC_UI_TIMEOUT_MS // 1000}s — will "
                "land if it eventually completes")
        except Exception:  # noqa: BLE001
            pass

    def _on_drilldown_fetch_done(
        self, request_id: int, result: list[Candle] | dict | None,
    ) -> None:
        """Future completed: drill if request still valid, else discard."""
        req = self._drilldown_request
        if req is None or req.request_id != request_id:
            try:
                self._status.info(
                    "5m fetch returned but drill-down was superseded; "
                    "result merged into cache only")
            except Exception:  # noqa: BLE001
                pass
            # The companion-prefetch _done handler (if this was an
            # attached prefetch) will merge the result into the cache.
            # If this was our own _drilldown_sync_fetch submission, we
            # didn't wire up a cache-write callback — but a stale
            # request implies the user moved on, so dropping it is fine.
            return
        if not self._drilldown_request_is_valid(req):
            try:
                self._status.info(
                    "5m fetch returned but context changed; discarding")
            except Exception:  # noqa: BLE001
                pass
            self._finish_drilldown_request(req)
            return
        if isinstance(result, dict):
            bars = result.get("merged") or []
        else:
            bars = result or []
        if not bars:
            try:
                self._status.error(
                    f"5m fetch for {req.ticker} returned no bars")
            except Exception:  # noqa: BLE001
                pass
            self._finish_drilldown_request(req)
            return
        # Merge into cache (mirrors _ensure_prefetched._apply logic).
        key = (req.src, req.ticker, "5m")
        try:
            current = self._full_cache.get(key)
            merged = disk_cache.merge_candles(self._disk_load(key), bars)
            if current:
                merged = disk_cache.merge_candles(current, merged)
            self._full_cache[key] = merged
            self._trim_full_cache()
            try:
                disk_cache.save(*key, merged)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            merged = bars
            try:
                self._full_cache[key] = bars
            except Exception:  # noqa: BLE001
                pass
        # Targeted-fetch compare alignment: the worker fetched + merged the
        # compare symbol's matching range into the on-disk cache, but the
        # in-memory _full_cache still holds the old (short) series. Reload it
        # from disk here (Tk thread) so the drill's re-render draws an
        # aligned RS/compare line over the same window.
        cmp_sym = getattr(req, "compare_ticker", "") or ""
        if cmp_sym and cmp_sym != req.ticker:
            cmp_key = (req.src, cmp_sym, "5m")
            try:
                cmp_disk = self._disk_load(cmp_key) or []
                if cmp_disk:
                    cmp_cur = self._full_cache.get(cmp_key)
                    cmp_merged = (
                        disk_cache.merge_candles(cmp_cur, cmp_disk)
                        if cmp_cur else cmp_disk
                    )
                    self._full_cache[cmp_key] = cmp_merged
                    self._trim_full_cache()
            except Exception:  # noqa: BLE001
                pass
        # Re-check coverage of the latest pending day (which may have
        # been retargeted by clicks during the wait).
        target_day = req.day
        has_day = False
        oldest_5m_day = None
        for c in merged:
            try:
                if not getattr(c, "is_gap", False):
                    cd = c.date.date()
                    if oldest_5m_day is None or cd < oldest_5m_day:
                        oldest_5m_day = cd
                    if cd == target_day:
                        has_day = True
                        break
            except Exception:  # noqa: BLE001
                continue
        self._finish_drilldown_request(req)
        if has_day:
            self._do_drilldown(target_day)
        else:
            try:
                self._status.warn(
                    f"5m data fetched but does not cover {target_day} "
                    f"(oldest: {oldest_5m_day})")
            except Exception:  # noqa: BLE001
                pass

    def _finish_drilldown_request(
        self, req: _DrilldownRequest, *, restore_cursor: bool = True,
    ) -> None:
        """Single exit path: cancel timers, restore cursor, clear slot."""
        if req.timer_job is not None:
            try:
                self.after_cancel(req.timer_job)
            except Exception:  # noqa: BLE001
                pass
            try:
                self._after_jobs.discard(req.timer_job)
            except Exception:  # noqa: BLE001
                pass
            req.timer_job = None
        if req.ui_timeout_job is not None:
            try:
                self.after_cancel(req.ui_timeout_job)
            except Exception:  # noqa: BLE001
                pass
            try:
                self._after_jobs.discard(req.ui_timeout_job)
            except Exception:  # noqa: BLE001
                pass
            req.ui_timeout_job = None
        if restore_cursor and req.cursor_set:
            try:
                self.config(cursor="")
            except Exception:  # noqa: BLE001
                pass
            req.cursor_set = False
        if self._drilldown_request is req:
            self._drilldown_request = None

    def _do_drilldown(self, day) -> bool:
        """Inner drill-down: switch interval if needed, set xlim, render.

        Extracted from the original _zoom_5m_for_date body so the new
        dispatch can reuse it from both the synchronous-cache-hit path
        and the deferred sync-fetch completion path.
        """
        # Avoid re-render churn if we're already on 5m for this day.
        already_5m = self.interval_var.get() == "5m"
        if not already_5m:
            self.interval_var.set("5m")
        self._preserve_xlim_on_render = True
        try:
            if not already_5m:
                self._load_data()
        except Exception:  # noqa: BLE001
            self._preserve_xlim_on_render = False
            return False
        return self._zoom_primary_to_date(day)

    def _zoom_primary_to_date(self, day) -> bool:
        """Set primary price/volume xlim to span just the bars on ``day``.

        Sources candles from ``self._primary`` — the full series authored
        by ``_load_data()`` — rather than ``_panel_state["primary"]["candles"]``,
        which is the rendered slice and may still hold the *previous*
        interval's bars when this is called from the 1d→5m drill-down
        path before the next render commits. Reading from the stale
        panel slice caused the day filter to match indices in the old
        140-bar 1d list, so the resulting xlim ended up at default
        right-edge of the new 5m series instead of the clicked day.
        """
        ps = self._panel_state.get("primary") or {}
        candles = list(self._primary or [])
        if not candles:
            return False
        lo: int = -1
        hi: int = -1
        for i, c in enumerate(candles):
            try:
                if c.date.date() == day and not getattr(c, "is_gap", False):
                    if lo < 0:
                        lo = i
                    hi = i
            except Exception:  # noqa: BLE001
                continue
        if lo < 0:
            return False
        # Pad by half a bar each side so the first/last candles aren't
        # clipped by the axis spine.
        ax_p = ps.get("price_ax")
        ax_v = ps.get("vol_ax")
        try:
            if ax_p is not None:
                ax_p.set_xlim(lo - 0.5, hi + 0.5)
            if ax_v is not None and ax_v is not ax_p:
                ax_v.set_xlim(lo - 0.5, hi + 0.5)
        except Exception:  # noqa: BLE001
            return False
        # Refill the virtualized render window for the new xlim. Critical
        # when drill-down lands far outside the previous render slice:
        # without this, _render() under _preserve_xlim_on_render=True
        # keeps the OLD (1d-derived) render window, and our new xlim
        # points at indices that have no drawn artists -> chart looks
        # empty until something else (e.g. compare toggle) forces a
        # fresh _render. _ensure_rendered_for_view also populates the
        # compare slot when present.
        try:
            for s in list(self._panel_state.keys()):
                self._ensure_rendered_for_view(s)
        except Exception:  # noqa: BLE001
            pass
        # Recompute Y from the now-visible slice via the existing helper
        # so users see the day's range, not the whole 60-day series scale.
        try:
            self._autoscale_y_to_visible()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass
        # Lock this day in so later ticker changes (typing, watchlist
        # double-click) reload at the same calendar day instead of
        # snapping back to the right edge. Cleared by _reset_view, by
        # explicit interval/source changes, or by the latest-day
        # fallback in _reload_preserving_drilldown when the new ticker
        # has no bars on this day.
        try:
            if isinstance(day, _date_t):
                self._drilldown_day = day
        except Exception:  # noqa: BLE001
            pass
        # Future renders should preserve this explicit zoom, not snap back.
        # _preserve_xlim_on_render stays True until the user explicitly
        # changes interval/ticker (which clears it via _do_scheduled_reload)
        # or hits "reset view".
        try:
            self._status.info(
                f"Drilled down to {day} ({hi - lo + 1} bars on 5m)")
        except Exception:  # noqa: BLE001
            pass
        return True

    def _reload_preserving_drilldown(self, load_fn) -> None:
        """Reload while keeping the drill-down day-zoom intact.

        Used by ticker-change paths (typing-driven `_do_scheduled_reload`
        and watchlist double-click) so the user can switch tickers
        without losing the day they were inspecting. After the reload,
        attempts to re-zoom to ``_drilldown_day``; if the new ticker has
        no bars on that exact day, falls back to its most-recent day at
        the same interval. Only abandons drill-down (clears flag and
        right-edge snaps) when the new series has no real bars at all.
        """
        target = self._drilldown_day
        # Hold xlim during _render so we can re-zoom precisely after.
        self._preserve_xlim_on_render = True
        self._poll_retry_count = 0
        self._poll_retry_expected_min_ts = None
        try:
            load_fn()
        except Exception:  # noqa: BLE001
            pass
        if not isinstance(target, _date_t):
            return
        if self._zoom_primary_to_date(target):
            return
        # Fallback: most recent calendar day in the new primary series.
        ps = self._panel_state.get("primary") or {}
        cs = ps.get("candles") or []
        latest = None
        for c in cs:
            if getattr(c, "is_gap", False):
                continue
            try:
                d = c.date.date()
                if latest is None or d > latest:
                    latest = d
            except Exception:  # noqa: BLE001
                continue
        if latest is not None and self._zoom_primary_to_date(latest):
            self._drilldown_day = latest
            return
        # No real bars at all — abandon drill-down cleanly.
        self._drilldown_day = None
        self._preserve_xlim_on_render = False
        try:
            self._render()
        except Exception:  # noqa: BLE001
            pass
