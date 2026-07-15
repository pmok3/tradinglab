"""Polling / scheduling mixin for :class:`tradinglab.app.ChartApp`.

Owns three concerns that share Tk ``after()`` plumbing:

* **After-job tracking** — :meth:`PollingMixin._track_after` wraps
  ``self.after`` so job ids self-evict from ``self._after_jobs`` when
  they fire. ``_on_close`` cancels whatever ids remain.
* **Periodic drains** — pulls events from the streaming queue and the
  cross-thread worker inbox onto the Tk main loop, then re-arms.
* **Bar-close polling** — debounced reloads and exchange-close-aligned
  next-bar fetches (intraday + daily/weekly/monthly).

Also home to the pure scheduler helpers
(``_market_window_et``/``_postpone_past_closed_market``/
``_next_daily_close_epoch``/``_compute_fetch_delay_ms``) that
:class:`ChartApp` formerly carried at module scope. They live here
because the polling code is their only caller — the back-compat
re-export from :mod:`tradinglab.app` keeps the existing
``tests/smoke/test_smoke_full.py`` import path working.

Mixin rules (see decomposition plan):
* No ``__init__``. The mixin relies on attributes that
  :class:`ChartApp.__init__` already initialises:
  ``_after_jobs``, ``_stream_queue``, ``_worker_inbox``, ``_poll_job``,
  ``_reload_job``, ``_poll_retry_count``, ``_poll_retry_expected_min_ts``,
  ``_full_cache``, ``_primary``, ``_fetch_executor``, ``_fetch_token``,
  ``_stream_active``, ``_stream_token``, ``_indicator_cache``,
  ``_prefetched_raw``, ``_preserve_xlim_on_render``,
  ``_slide_xlim_to_right_edge``, ``_drilldown_day``, plus Tk vars
  (``source_var``, ``interval_var``, ``ticker_var``, ``compare_var``,
  ``compare_ticker_var``, ``prepost_var``).
* No cooperative ``super()`` — plain MRO.
* No name collisions with other mixins or :class:`ChartApp`.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import queue
import time
import tkinter as tk

from .. import disk_cache as _disk_cache
from ..constants import interval_minutes, is_intraday
from ..core.view_intent import ViewMode
from ..data import DATA_SOURCES

# Adaptive live-tick repaint coalescing (audit ``tick-repaint-coalesce``).
# A sub-minute LEVELONE stream (Schwab) can enqueue many ticks/second; the
# 50 ms drain already collapses a burst to one ``_refresh_view_after_tick``,
# but on a heavy multi-pane chart a single repaint can itself cost tens of
# ms, so an unthrottled 20 Hz repaint saturates the Tk thread. We gate
# repaints to an adaptive minimum interval derived from the measured paint
# cost: ``interval = clamp(paint_ms * _TICK_PAINT_INTERVAL_FACTOR,
# _TICK_PAINT_MIN_INTERVAL_MS, _TICK_PAINT_MAX_INTERVAL_MS)``. A cheap chart
# repaints at the 30 Hz floor; a heavy one backs off toward 4 Hz rather than
# falling behind. The first tick after an idle gap always paints immediately.
_TICK_PAINT_MIN_INTERVAL_MS = 33     # ~30 fps ceiling on repaint frequency
_TICK_PAINT_MAX_INTERVAL_MS = 250    # ~4 fps floor under a heavy paint load
_TICK_PAINT_INTERVAL_FACTOR = 1.5    # headroom over the measured paint cost


# ---------------------------------------------------------------------------
# Local ``_silent_tcl`` clone.
#
# Mirrors the same-named helper in :mod:`tradinglab.app` and
# :mod:`tradinglab.backtest.replay`. Kept module-local rather than
# shared to avoid a ``gui.polling`` → ``app`` import cycle (the mixin
# is imported BY app during class-creation time).
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def _silent_tcl(*extra_excs: type[BaseException]):
    excs = (tk.TclError,) + extra_excs
    try:
        yield
    except excs:
        pass


# ---------------------------------------------------------------------------
# Pure scheduler helpers — unit-testable with injected ``now`` timestamps
# instead of sleeping or patching ``time.time``.
# ---------------------------------------------------------------------------
def _market_window_et(include_extended: bool) -> tuple[_dt.time, _dt.time]:
    """Return (open, close) ET ``time`` pair for a regular weekday.

    Delegates to :func:`tradinglab.core.session_calendar.market_window`
    (the single owner of the session boundaries). Extended hours on
    NYSE/NASDAQ run 04:00-20:00 ET; regular hours 09:30-16:00 ET.
    """
    from ..core.session_calendar import market_window

    return market_window(include_extended)


def _postpone_past_closed_market(target_epoch: float,
                                  include_extended: bool = True) -> float:
    """If ``target_epoch`` lands outside NYSE hours (ET), return the
    epoch for the next market-open moment; else return it unchanged.

    Returns the input unchanged if the ET timezone cannot be loaded
    (e.g. no ``tzdata`` on Windows without it installed) — conservative
    behavior so scheduling still produces *some* fetch rather than
    silently breaking.
    """
    from ..core.timezones import ET
    if ET is None:
        return target_epoch
    t = _dt.datetime.fromtimestamp(target_epoch, tz=ET)
    open_t, close_t = _market_window_et(include_extended)
    is_weekday = t.weekday() < 5
    in_hours = is_weekday and open_t <= t.time() < close_t
    if in_hours:
        return target_epoch
    if is_weekday and t.time() < open_t:
        target_date = t.date()
    else:
        d = t.date() + _dt.timedelta(days=1)
        while d.weekday() >= 5:
            d += _dt.timedelta(days=1)
        target_date = d
    nxt = _dt.datetime.combine(target_date, open_t, tzinfo=ET)
    return nxt.timestamp()


def _next_daily_close_epoch(now_epoch: float, grace_s: int = 300) -> float:
    """Return epoch for ``grace_s`` after the next 16:00 ET weekday close.

    Used for daily/weekly/monthly intervals where bar timestamps don't
    encode close times. Schedules every weekday at 16:05 ET — callers
    can decide to drop fetches for non-boundary days (e.g. 1wk only on
    Fridays) as an optimization; here we poll every weekday so
    in-progress weekly/monthly candles also refresh.
    """
    from ..core.timezones import ET
    if ET is None:
        return now_epoch + 24 * 3600
    now = _dt.datetime.fromtimestamp(now_epoch, tz=ET)
    close_today = now.replace(hour=16, minute=0, second=0, microsecond=0)
    target = close_today + _dt.timedelta(seconds=grace_s)
    if now >= target or now.weekday() >= 5:
        d = now.date() + _dt.timedelta(days=1)
        while d.weekday() >= 5:
            d += _dt.timedelta(days=1)
        target = _dt.datetime.combine(
            d, _dt.time(16, 0), tzinfo=ET
        ) + _dt.timedelta(seconds=grace_s)
    return target.timestamp()


def _compute_fetch_delay_ms(
    interval: str,
    last_bar_epoch: float | None,
    now_epoch: float,
    include_extended: bool,
    min_backoff_ms: int,
    grace_intraday_s: int = 5,
    grace_daily_s: int = 300,
    intraday_refresh_on_daily: bool = False,
) -> int:
    """Pure scheduler: return Tk ``after()`` delay (ms) for the next fetch.

    Anchors on the last bar's epoch timestamp + interval + grace so
    session-aligned intraday bars (e.g. 1h bars that close at
    10:30/11:30 ET on NYSE) are honored by the exchange's own
    boundaries rather than re-derived from midnight. If the target
    lands outside market hours (intraday only), postpones to next open.

    For daily/weekly/monthly intervals, always schedules for 16:05 ET
    next weekday because daily bar timestamps don't represent close
    times, so last_bar + 86400s would skip ~17 hours of real time.

    When ``intraday_refresh_on_daily=True`` and ``interval == "1d"``,
    schedules at intraday cadence (every 5 minutes, market-hours-only)
    instead so the daily chart's synthetic today-bar can refresh
    continuously from the live intraday feed. Audit
    ``daily-today-upsample``. Outside market hours we fall through to
    the standard daily 16:05 ET schedule — no point burning fetches.
    """
    if not is_intraday(interval):
        if intraday_refresh_on_daily and interval == "1d":
            target_s = now_epoch + 5 * 60 + grace_intraday_s
            postponed = _postpone_past_closed_market(
                target_s, include_extended=False,
            )
            if postponed <= target_s + 60:
                # Still inside market hours: intraday cadence.
                delay_s = max(min_backoff_ms / 1000.0, target_s - now_epoch)
                return int(delay_s * 1000)
            # Outside market hours: defer to next daily close.
        target_s = _next_daily_close_epoch(now_epoch, grace_s=grace_daily_s)
        delay_s = max(min_backoff_ms / 1000.0, target_s - now_epoch)
        return int(delay_s * 1000)

    iv_sec = interval_minutes(interval) * 60
    if last_bar_epoch is not None:
        target_s = last_bar_epoch + iv_sec + grace_intraday_s
        if target_s <= now_epoch:
            target_s = now_epoch + grace_intraday_s
    else:
        target_s = now_epoch + iv_sec + grace_intraday_s

    target_s = _postpone_past_closed_market(target_s, include_extended)
    delay_s = max(min_backoff_ms / 1000.0, target_s - now_epoch)
    return int(delay_s * 1000)


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------
class PollingMixin:
    """After-job tracking + periodic drains + bar-close polling."""

    # Re-declared on ChartApp as class attributes. Listed here only so
    # static analysers know the mixin expects them on ``cls``.
    _MIN_POLL_BACKOFF_MS: int
    _POLL_RETRY_DELAY_MS: int
    _POLL_RETRY_MAX: int

    # ------------------------------------------------------------------
    # After-job tracking
    # ------------------------------------------------------------------
    def _track_after(self, delay_ms, fn, *args):
        """Schedule ``fn`` via Tk ``after()`` and auto-remove the job
        id from ``self._after_jobs`` when it fires.

        Replaces the unbounded ``self._after_jobs.append(self.after(...))``
        pattern: jobs that fire normally pop themselves out of the set,
        so long-running sessions don't accumulate stale ids. ``_on_close``
        iterates whatever's left and cancels them.

        Returns the Tk job id (string) so callers can store it for
        ``after_cancel`` if they need to cancel before fire.
        """
        # Mutable cell so the wrapper can see the id assigned below.
        cell: list[str | None] = [None]

        def _wrapped():
            try:
                jid = cell[0]
                if jid is not None:
                    self._after_jobs.discard(jid)
            except Exception:  # noqa: BLE001
                pass
            return fn(*args)

        job_id = self.after(max(0, int(delay_ms)), _wrapped)
        cell[0] = job_id
        self._after_jobs.add(job_id)
        return job_id

    # ------------------------------------------------------------------
    # Periodic drains (stream queue + worker inbox)
    # ------------------------------------------------------------------
    def _schedule_drain(self) -> None:
        """Re-arm the stream-drain timer."""
        try:
            job = self._track_after(50, self._drain_stream_queue)
        except tk.TclError:
            return
        self._stream_drain_after = job

    def _schedule_worker_inbox_drain(self) -> None:
        """Re-arm the worker-inbox drain timer (Tk thread).

        Workers cannot call ``self.after`` on this Python/Tk build —
        ``tk.createcommand`` blocks the worker thread instead of
        raising. Hence preload jobs deposit completion items on
        ``self._worker_inbox`` and this periodic Tk-thread tick drains
        and applies them. Period chosen at ~80ms so a Space-cycle
        watchlist refresh feels instant without burning idle CPU.
        """
        try:
            job = self._track_after(80, self._drain_worker_inbox)
        except tk.TclError:
            return
        self._worker_inbox_after = job

    def _drain_worker_inbox(self) -> None:
        refresh_pending = False
        reference_pending = False
        # Process only the items already queued when this drain STARTS.
        # A handler may synchronously enqueue MORE work: most importantly
        # the prefetch-arrival branch below calls
        # ``_refresh_daily_synth_for_active_view`` →
        # ``_maybe_upsample_today_daily``, which — during RTH, when the
        # daily-today synth can't be satisfied — re-submits a companion
        # prefetch (audit ``daily-today-upsample``). With a stub / fast
        # fetcher that completion re-arrives on ``_worker_inbox`` before an
        # unbounded ``while True`` drains it empty, so a single Tk
        # ``update()`` never returns: ``_pump`` livelocks and the smoke
        # suite times out (120s) on fast CI runners that happen to run
        # during US market hours. Bounding the drain to the entry snapshot
        # defers freshly-enqueued items to the next 80ms tick (imperceptible)
        # and makes the loop provably terminate regardless of the feedback.
        # Audit ``inbox-drain-livelock``.
        try:
            budget = self._worker_inbox.qsize()
        except Exception:  # noqa: BLE001
            budget = 0
        try:
            while budget > 0:
                budget -= 1
                kind, payload = self._worker_inbox.get_nowait()
                if kind == "stash":
                    try:
                        key, bars = payload
                        self._stash_full_cache(key, bars)
                    except Exception:  # noqa: BLE001
                        pass
                elif kind == "prefetch":
                    try:
                        key, bars = payload
                        self._apply_prefetch_result(key, bars)
                        # If we're viewing a daily-class chart and the
                        # arriving prefetch is an intraday companion
                        # for the active symbol, re-render so today's
                        # synthetic daily bar picks up the freshly-
                        # warmed intraday data. Cheap (no network) —
                        # see :meth:`_refresh_daily_synth_for_active_view`.
                        # Audit ``daily-today-upsample``.
                        try:
                            _src, _sym, _iv = key
                            if is_intraday(_iv):
                                self._refresh_daily_synth_for_active_view(
                                    prefetched_symbol=_sym,
                                )
                                refresh_volume_tod = getattr(
                                    self, "_refresh_volume_tod_for_prefetch", None,
                                )
                                if callable(refresh_volume_tod):
                                    refresh_volume_tod(
                                        prefetched_source=_src,
                                        prefetched_symbol=_sym,
                                        prefetched_interval=_iv,
                                    )
                        except Exception:  # noqa: BLE001
                            pass
                    except Exception:  # noqa: BLE001
                        pass
                elif kind == "refresh":
                    refresh_pending = True
                elif kind == "reference":
                    reference_pending = True
                elif kind == "card_stash":
                    try:
                        slot_index, token, symbol, bars = payload
                        cs = getattr(self, "_chartstack", None)
                        if cs is not None and hasattr(cs, "apply_card_stash"):
                            cs.apply_card_stash(slot_index, token, symbol, bars)
                    except Exception:  # noqa: BLE001
                        pass
        except queue.Empty:
            pass
        if refresh_pending:
            try:
                wt = getattr(self, "watchlist_tab", None)
                if wt is not None and hasattr(wt, "_schedule_watchlist_tab_refresh"):
                    wt._schedule_watchlist_tab_refresh()
                elif hasattr(self, "_schedule_watchlist_tab_refresh"):
                    self._schedule_watchlist_tab_refresh()
            except Exception:  # noqa: BLE001
                pass
        if reference_pending:
            try:
                self._reference_data_redraw()
            except Exception:  # noqa: BLE001
                pass
        # Perf item (b) auto-detect: if a fetch worker observed the Alpaca
        # X-RateLimit-Limit header revealing a FREE-tier key while "Paid" was
        # selected, it recorded a one-shot notice (and already downgraded the
        # limiter + feed). Surface it HERE on the Tk thread as an error popup —
        # cross-thread Tk from the worker is unsafe on this build (see this
        # module's docstring). Rare (fires once per process, only on the
        # mismatch), so the per-tick poll is a cheap lock + None check.
        try:
            from ..data import alpaca_source as _alpaca
            _notice = _alpaca.pop_pending_downgrade_notice()
        except Exception:  # noqa: BLE001
            _notice = None
        if _notice:
            try:
                from tkinter import messagebox
                messagebox.showerror("Alpaca plan", _notice, parent=self)
            except Exception:  # noqa: BLE001 - headless / no display: log instead
                try:
                    self._status.warn(_notice.replace("\n\n", " ").replace("\n", " "))
                except Exception:  # noqa: BLE001
                    pass
        self._schedule_worker_inbox_drain()

    def _drain_stream_queue(self) -> None:
        """Pop all queued events and dispatch to tick/rollover handlers.

        Rollovers may append a new bar, which requires a slice refresh
        (via :meth:`_refresh_view_after_append`). Ticks mutate in place
        and route through :meth:`_refresh_view_after_tick` — no topology
        rebuild, which means hover/crosshair stay alive (spec §5.8).

        ChartStack (M3) shares the same queue but uses a ``"card:N"``
        slot prefix; events with that prefix are routed to
        ``self._chartstack.apply_stream_event`` instead of the main
        chart pipeline.
        """
        ticked = False
        rolled = False
        drain = getattr(getattr(self, "_stream_ctrl", None), "drain", None)
        if callable(drain):
            try:
                events = drain()
            except Exception:  # noqa: BLE001
                events = []
        else:
            events = []
            try:
                while True:
                    events.append(self._stream_queue.get_nowait())
            except queue.Empty:
                pass
        for evt in events:
            kind = evt[5] if len(evt) > 5 else ""
            slot = evt[1] if len(evt) > 1 else ""
            # ChartStack branch: slot string starts with "card:".
            # Format: (token, "card:N", src, ticker, interval, kind, bar)
            if isinstance(slot, str) and slot.startswith("card:"):
                cs = getattr(self, "_chartstack", None)
                if cs is None or not hasattr(cs, "apply_stream_event"):
                    continue
                try:
                    slot_index = int(slot.split(":", 1)[1])
                except (ValueError, IndexError):
                    continue
                token = evt[0] if len(evt) > 0 else 0
                bar = evt[6] if len(evt) > 6 else None
                try:
                    cs.apply_stream_event(slot_index, token, kind, bar)
                except Exception:  # noqa: BLE001
                    pass
                continue
            if kind == "tick":
                if self._apply_stream_tick(evt):
                    ticked = True
            elif kind == "rollover":
                if self._apply_stream_rollover(evt):
                    rolled = True
        if rolled:
            try:
                # Rewire the slot so _panel_state['candles'] sees the
                # now-grown list (object identity is preserved by tick
                # but not by rollover's potential new-list creation).
                src = self.source_var.get()
                interval = self.interval_var.get()
                tic = self.ticker_var.get().strip().upper()
                raw = self._full_cache.get((src, tic, interval))
                if raw is not None:
                    self._primary = raw
                    self.candles = raw
                    self._rewire_slot_candles("primary", raw)
                self._refresh_view_after_append("primary")
            except Exception:  # noqa: BLE001
                # Fallback to full render on any refresh-path failure.
                try:
                    self._render()
                except Exception:  # noqa: BLE001
                    pass
        elif ticked:
            self._request_tick_repaint("primary")
        self._schedule_drain()

    def _request_tick_repaint(self, slot: str = "primary") -> None:
        """Rate-limit live-tick repaints so a fast stream can't saturate Tk.

        The first tick after an idle gap paints immediately. Ticks arriving
        inside the adaptive minimum interval (see the module constants) are
        coalesced: a single trailing repaint is scheduled and further ticks
        are dropped until it fires. Dropping is safe — the forming bar is
        mutated in place in ``_full_cache`` / ``_primary`` before we get
        here, so the deferred paint always renders the freshest state.
        """
        now = time.monotonic()
        if now >= getattr(self, "_tick_paint_next_allowed", 0.0):
            self._do_tick_repaint(slot)
            return
        if getattr(self, "_tick_repaint_pending", False):
            return
        self._tick_repaint_pending = True
        delay_ms = max(1, int((self._tick_paint_next_allowed - now) * 1000.0))
        self._tick_repaint_job = self._track_after(
            delay_ms, self._do_tick_repaint, slot)

    def _do_tick_repaint(self, slot: str = "primary") -> None:
        """Run one tick repaint, measure its cost, and re-arm the gate.

        The measured wall-clock cost feeds an EWMA that drives the next
        allowed-paint deadline, so the effective repaint rate adapts to how
        expensive the current chart is to paint (pane count, indicators).
        """
        self._tick_repaint_pending = False
        t0 = time.monotonic()
        try:
            self._refresh_view_after_tick(slot)
        except Exception:  # noqa: BLE001
            pass
        paint_ms = (time.monotonic() - t0) * 1000.0
        prev = getattr(self, "_tick_paint_ewma_ms", 0.0)
        # Seed the EWMA with the first real sample so a cold 0.0 doesn't drag
        # the first interval down to the floor before we know the true cost.
        self._tick_paint_ewma_ms = (
            paint_ms if prev <= 0.0 else 0.5 * prev + 0.5 * paint_ms)
        interval_ms = min(
            _TICK_PAINT_MAX_INTERVAL_MS,
            max(_TICK_PAINT_MIN_INTERVAL_MS,
                self._tick_paint_ewma_ms * _TICK_PAINT_INTERVAL_FACTOR),
        )
        self._tick_paint_next_allowed = time.monotonic() + interval_ms / 1000.0

    # ------------------------------------------------------------------
    # Debounced reload + next-bar scheduler (spec §9.3)
    # ------------------------------------------------------------------
    def _schedule_reload(self, delay_ms: int = 700) -> None:
        """Debounced reload: after ``delay_ms`` ms, runs ``_do_scheduled_reload``."""
        if self._reload_job is not None:
            try:
                self.after_cancel(self._reload_job)
            except Exception:  # noqa: BLE001
                pass
            self._reload_job = None
        with _silent_tcl():
            self._reload_job = self._track_after(
                int(delay_ms), self._do_scheduled_reload)

    def _do_scheduled_reload(self) -> None:
        self._reload_job = None
        # Preserve drill-down state when typing/click-to-type swaps the
        # ticker mid-zoom: keep the same calendar day if the new ticker
        # has data there, else fall back to its most recent day.
        if self._drilldown_day is not None and self.interval_var.get() == "5m":
            self._reload_preserving_drilldown(self._load_data)
            return
        # Default path: explicit user intent clears bar-index pan
        # (spec §9.3). Preserve the timestamp window so switching to a new
        # symbol keeps the same calendar day in view — but ONLY when the
        # user has panned/zoomed back into history. At the default
        # right-edge view (e.g. after Reset View), preserving would impose
        # the previous ticker's window on the new one, shifting its left
        # edge to an unexpected earlier date when the two have different
        # history depth/density; show the new ticker's own default instead.
        # Audit ``ticker-switch-default-view-align``.
        self._preserve_xlim_on_render = False
        self._preserve_xlim_by_time_on_render = (
            self._ticker_change_should_time_preserve())
        # Explicit reload also clears any in-flight poll-retry bookkeeping
        # so the retry budget doesn't carry across an interval/ticker switch.
        self._poll_retry_count = 0
        self._poll_retry_expected_min_ts = None
        try:
            self._load_data_async()
        except Exception:  # noqa: BLE001
            pass

    def _live_updates_delayed_for_source(self) -> bool:
        """True when the active data source can't provide real-time live bars.

        Alpaca's **free** tier streams 15-minute-delayed IEX data, so arming
        the live bar-close poll would present stale bars as live. Live polling
        is suppressed in that case — the chart still loads and refreshes on
        demand; only the automatic "it's live" cadence is turned off. Paid
        Alpaca (SIP) and every other source are live-capable.

        ``"Auto"`` is resolved to its effective concrete source first (in
        practice Auto never resolves to free-Alpaca — yfinance always outranks
        it — so Auto is live-capable, but this stays correct if the priority
        ever changes). Never raises.
        """
        try:
            src = self.source_var.get()
        except Exception:  # noqa: BLE001
            return False
        if src == "Auto":
            try:
                from ..data.auto_source import resolve_auto_source
                src = resolve_auto_source()
            except Exception:  # noqa: BLE001
                return False
        if src == "alpaca":
            try:
                from ..data.alpaca_source import is_live_capable
                return not is_live_capable()
            except Exception:  # noqa: BLE001
                return False
        return False

    def _schedule_next_bar_fetch(self) -> None:
        """Arm an after() timer aligned to the next bar-close boundary.

        Sandbox guard: refuse to arm a poll while a replay session is
        active (the engine drives clock advancement, not the live poll).

        Uses ``_compute_fetch_delay_ms`` (pure helper) to derive the
        delay from the last bar's timestamp + interval + grace, then
        postpones past known-closed market hours. Suppressed while
        streaming is active.

        Retry path: if the previous tick expected a newer bar and the
        fetch did not advance the last-bar timestamp, schedule a short
        retry at ``_POLL_RETRY_DELAY_MS`` until ``_POLL_RETRY_MAX`` is
        exhausted. Retries bypass the aligned-bar scheduler because
        they're specifically trying to catch a published-late bar.
        """
        if self._is_sandbox_active():
            return
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:  # noqa: BLE001
                pass
            self._poll_job = None
        if self._stream_active:
            return
        if self._live_updates_delayed_for_source():
            # Free-tier Alpaca (and any 15-min-delayed feed): don't arm the
            # live poll — delayed bars must not masquerade as live updates.
            return

        interval = self.interval_var.get()
        last_ts = None
        try:
            if self._primary:
                d = self._primary[-1].date
                last_ts = d.timestamp() if hasattr(d, "timestamp") else float(d)
        except Exception:  # noqa: BLE001
            last_ts = None

        expected = self._poll_retry_expected_min_ts
        if (expected is not None
                and is_intraday(interval)
                and (last_ts is None or last_ts < expected)
                and self._poll_retry_count < self._POLL_RETRY_MAX):
            # Previous tick didn't bring in the expected new bar.
            # Retry soon without going through the aligned scheduler.
            self._poll_retry_count += 1
            delay_ms = self._POLL_RETRY_DELAY_MS
        else:
            # Success (or non-retriable path): reset retry state.
            self._poll_retry_count = 0
            self._poll_retry_expected_min_ts = None
            try:
                include_ext = bool(self.prepost_var.get())
            except Exception:  # noqa: BLE001
                include_ext = True
            delay_ms = _compute_fetch_delay_ms(
                interval=interval,
                last_bar_epoch=last_ts,
                now_epoch=time.time(),
                include_extended=include_ext,
                min_backoff_ms=self._MIN_POLL_BACKOFF_MS,
                intraday_refresh_on_daily=True,
            )
        with _silent_tcl():
            self._poll_job = self._track_after(
                delay_ms, self._next_bar_fetch_tick)

    def _next_bar_fetch_tick(self) -> None:
        """Fire a background reload when a new bar should have closed.

        Sandbox guard: if a replay session is active, drop this tick on
        the floor — the engine drives clock advancement, not the live
        poll.

        Runs the provider fetch on ``_fetch_executor`` so the Tk main
        thread stays responsive — a slow yfinance HTTP call no longer
        freezes the GUI mid-pan/hover. When the fetch resolves, the
        result is marshalled back to the main thread via
        ``self.after(0, …)`` and handed to ``_load_data`` through the
        ``_prefetched_raw`` slot. Stale results (superseded by a newer
        ticker/interval load) are dropped via ``_fetch_token`` gating
        before ``_load_data`` is even invoked.

        Also preserves xlim and records retry bookkeeping (see
        ``_schedule_next_bar_fetch``).
        """
        if self._is_sandbox_active():
            self._poll_job = None
            return
        if self._live_updates_delayed_for_source():
            # A tier auto-downgrade (paid→free) can land after a poll was
            # already armed; drop the tick so delayed data never renders live.
            self._poll_job = None
            return
        self._poll_job = None
        # Race guard (audit ``source-switch-view-preserve``): while an
        # explicit source/interval switch is loading a new series, do NOT
        # re-arm index-based preservation or launch a competing fetch — the
        # new series can be a different length (e.g. yfinance 60d-5m vs
        # Alpaca 120d-5m), so reusing the stale bar-index window would jump
        # the view to a different calendar day. The switch load owns the
        # render and re-arms polling via _schedule_next_bar_fetch when done.
        if self._view.load_pending:
            return
        # Live tick: keep the current width and (unless the user has panned
        # away from the right edge) shift to the newest bar — SNAP_RIGHT;
        # a panned view holds its bars (KEEP_BARS). Audit
        # ``view-intent-controller``.
        self._view.request(
            ViewMode.KEEP_BARS if self._user_has_panned_x() else ViewMode.SNAP_RIGHT
        )

        src = self.source_var.get()
        interval = self.interval_var.get()
        raw_primary = self.ticker_var.get().strip().upper()
        compare_on = bool(self.compare_var.get())
        raw_compare = (self.compare_ticker_var.get().strip().upper()
                       if compare_on else "")

        # Retry bookkeeping (intraday only): expected-min-ts = last_bar +
        # interval. Used by _schedule_next_bar_fetch to decide between a
        # short retry and the aligned schedule.
        if is_intraday(interval):
            last_ts = None
            try:
                if self._primary:
                    d = self._primary[-1].date
                    last_ts = (d.timestamp() if hasattr(d, "timestamp")
                               else float(d))
            except Exception:  # noqa: BLE001
                last_ts = None
            if last_ts is not None:
                try:
                    iv_sec = interval_minutes(interval) * 60
                    self._poll_retry_expected_min_ts = last_ts + iv_sec
                except Exception:  # noqa: BLE001
                    self._poll_retry_expected_min_ts = None
        else:
            self._poll_retry_expected_min_ts = None

        # Daily-view live-refresh path: on 1d, instead of refetching the
        # daily series (which lags by ~1 day so nothing changes mid-
        # session), warm the 5m intraday cache for the active symbols.
        # The prefetch-arrival handler will redraw the daily chart with
        # an updated synthetic today-bar (see
        # :meth:`_refresh_daily_synth_for_active_view`). Outside market
        # hours the daily-cadence branch in ``_compute_fetch_delay_ms``
        # routes us to 16:05 ET instead, so this tick only fires
        # mid-session. Audit ``daily-today-upsample``.
        if interval == "1d":
            try:
                for tic in (raw_primary, raw_compare):
                    if tic:
                        self._ensure_prefetched(tic, "5m", force=True)
            except Exception:  # noqa: BLE001
                pass
            try:
                self._schedule_next_bar_fetch()
            except Exception:  # noqa: BLE001
                pass
            return

        # Drop the active primary/compare entries so fresh data is fetched.
        for tic in (raw_primary, raw_compare):
            if tic:
                self._full_cache.pop((src, tic, interval), None)

        fetcher = DATA_SOURCES.get(src)
        executor = getattr(self, "_fetch_executor", None)
        if fetcher is None or executor is None:
            # No async infrastructure available — fall back to sync path.
            try:
                self._load_data()
            except Exception:  # noqa: BLE001
                pass
            return

        # Bump token BEFORE submitting so a ticker-switch that happens
        # while this fetch is in-flight supersedes it cleanly.
        self._fetch_token += 1
        token = self._fetch_token

        def _work():
            p: list = []
            c: list = []
            try:
                p = fetcher(raw_primary, interval) or []
            except Exception:  # noqa: BLE001
                p = []
            if raw_compare:
                try:
                    c = fetcher(raw_compare, interval) or []
                except Exception:  # noqa: BLE001
                    c = []
            # H2: piggy-back disk-cache reads onto the worker.
            p_disk: list | None = None
            c_disk: list | None = None
            try:
                if raw_primary:
                    p_disk = _disk_cache.load(src, raw_primary, interval)
            except Exception:  # noqa: BLE001
                p_disk = None
            try:
                if raw_compare:
                    c_disk = _disk_cache.load(src, raw_compare, interval)
            except Exception:  # noqa: BLE001
                c_disk = None
            return p, c, p_disk, c_disk

        try:
            fut = executor.submit(_work)
        except Exception:  # noqa: BLE001
            # Executor rejected submission (e.g. shutdown): fallback.
            try:
                self._load_data()
            except Exception:  # noqa: BLE001
                pass
            return

        def _on_result(result) -> None:
            # Stale-token guard: a newer fetch (or explicit ticker
            # change) has superseded us — silently drop.
            if token != self._fetch_token:
                return
            if result is None:
                p_raw, c_raw = None, None
                p_disk, c_disk = None, None
            else:
                p_raw, c_raw, p_disk, c_disk = result
            self._prefetched_raw = {
                "token": token,
                "src": src,
                "interval": interval,
                "primary_ticker": raw_primary,
                "compare_ticker": raw_compare,
                "primary": p_raw,
                "compare": c_raw,
                "primary_disk": p_disk,
                "compare_disk": c_disk,
                "disk_preloaded": True,
            }
            try:
                self._load_data()
            finally:
                self._prefetched_raw = None

        # Marshal back to Tk via a main-loop poll (see
        # `_await_future_on_tk` for why `add_done_callback` +
        # `self.after()` from the worker thread is unsafe).
        self._await_future_on_tk(fut, _on_result)


__all__ = [
    "PollingMixin",
    "_compute_fetch_delay_ms",
    "_market_window_et",
    "_next_daily_close_epoch",
    "_postpone_past_closed_market",
    "_silent_tcl",
]
