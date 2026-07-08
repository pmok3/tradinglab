# gui/polling.py — Spec

## Purpose

`PollingMixin` extracted from `ChartApp`. Owns three concerns sharing
Tk `after()` plumbing:

1. **After-job tracking** — `after()` jobs auto-evict from
   `self._after_jobs` on fire; `_on_close` cancels remaining ids.
2. **Periodic drains** — pull events from the streaming queue and
   the cross-thread worker inbox onto the Tk main loop.
3. **Bar-close polling** — debounced reload + exchange-aligned
   next-bar fetch.

Also hosts the pure scheduler helpers (only caller is here).

## Public API

### Module-level (pure scheduler helpers — unit-testable)

- `_market_window_et(include_extended) -> (time, time)` — `(open,
  close)` ET for a regular weekday. Extended = 04:00–20:00 ET,
  regular = 09:30–16:00 ET.
- `_postpone_past_closed_market(target_epoch, include_extended=True)
  -> float` — if `target_epoch` is outside NYSE hours, return next
  market-open epoch. Returns input unchanged if `zoneinfo` / NY tz
  unavailable.
- `_next_daily_close_epoch(now_epoch, grace_s=300) -> float` —
  epoch for grace_s after next 16:00 ET weekday close.
- `_compute_fetch_delay_ms(interval, last_bar_epoch, now_epoch,
  include_extended, min_backoff_ms, grace_intraday_s=5,
  grace_daily_s=300, intraday_refresh_on_daily=False) -> int` —
  anchors on last bar + interval + grace so session-aligned
  intraday bars (e.g. 1h bars closing at 10:30/11:30 NYSE) are
  honored. For daily / weekly / monthly, normally schedules to
  16:05 ET next weekday (daily timestamps don't encode close
  time). When `intraday_refresh_on_daily=True` AND `interval ==
  "1d"` AND market is open, schedules ~5-min cadence instead so
  the daily chart's synthetic today-bar can refresh continuously
  from cached intraday data (audit `daily-today-upsample`);
  outside RTH falls through to the standard daily 16:05 path.
- `_silent_tcl(*extra_excs)` — context manager swallowing
  `tk.TclError` + extras. Module-local clone to avoid a
  `gui.polling → app` import cycle.

`__all__`: `PollingMixin`, `_compute_fetch_delay_ms`,
`_market_window_et`, `_next_daily_close_epoch`,
`_postpone_past_closed_market`, `_silent_tcl`. Re-exported from
`tradinglab.app` for legacy test imports.

### `PollingMixin` methods (bound on `ChartApp`)

- `_track_after(delay_ms, fn, *args) -> str` — wraps `self.after()`
  so the id auto-evicts from `self._after_jobs` on fire. Returns
  the Tk job id.
- `_schedule_drain()` — re-arm 50ms streaming-queue drain.
- `_schedule_worker_inbox_drain()` — re-arm 80ms worker-inbox
  drain. Workers can't call `self.after` on this Tk build (it
  blocks the worker), so they post to `self._worker_inbox`.
- `_drain_worker_inbox()` — pop items: `stash` (cache fetched
  bars), `refresh` (watchlist refresh), `reference` (reference-
  data redraw), `card_stash` (chartstack card cache fill). When
  a `prefetch` event arrives for an intraday interval, also
  calls `_refresh_daily_synth_for_active_view` so a daily chart
  picks up the freshly-warmed intraday data without a round-trip
  (audit `daily-today-upsample`) and `_refresh_volume_tod_for_prefetch`
  so volume time-of-day shading repaints after a cold 5m cache warms.
  **Bounded drain (audit `inbox-drain-livelock`):** each call processes
  only the items queued at entry (`qsize()` snapshot), not `while True`
  until empty. A `prefetch` handler can re-enqueue work — during RTH
  `_refresh_daily_synth_for_active_view` re-submits a companion prefetch
  when the daily-today synth can't be satisfied — and with a fast/stub
  fetcher that completion re-arrives before an unbounded loop drains,
  livelocking a single Tk `update()` (smoke 120s timeout on fast CI
  runners during market hours). Bounding defers freshly-enqueued items to
  the next 80ms tick. **Alpaca free-tier popup:** each tick also polls
  `data.alpaca_source.pop_pending_downgrade_notice()` and, if a fetch
  worker auto-detected a free-tier key while "Paid" was selected (perf
  item (b) auto-detect), shows a one-shot `messagebox.showerror` on the Tk
  thread (falls back to `_status.warn` when headless). The worker only
  *records* the notice — cross-thread Tk from the worker is unsafe here.
- `_drain_stream_queue()` — pop streaming events. Routes
  `"card:N"`-slot events to `self._chartstack.apply_stream_event`;
  routes `tick`/`rollover` for main chart through
  `_apply_stream_tick`/`_apply_stream_rollover`. Rewires
  primary slot's candle list after rollover (may build new list).
- `_schedule_reload(delay_ms=700)` — debounced reload (typing /
  interval flip / source flip).
- `_do_scheduled_reload()` — debounce callback. Preserves
  drilldown state on mid-zoom ticker swaps; else clears bar-index
  pan and preserves timestamp window.
- `_schedule_next_bar_fetch()` — arm aligned next-bar timer.
  No-op in sandbox / while streaming. Retry path: if prev tick
  expected newer bar but fetch didn't advance, schedule retry at
  `_POLL_RETRY_DELAY_MS` until `_POLL_RETRY_MAX` exhausted.
- `_next_bar_fetch_tick()` — actual fetch. Provider HTTP runs on
  `_fetch_executor` (off Tk thread). Result marshaled via
  `_await_future_on_tk` and fed into `_load_data` through
  `_prefetched_raw`. `_load_data` invalidates prior visible
  primary/compare indicator entries when it consumes those fresh bars.
  Bumps `_fetch_token` before submission so stale results from a
  superseding ticker switch can drop. **1d
  ticks redirect to intraday prefetch** (`_ensure_prefetched(sym,
  "5m", force=True)`) for primary + compare instead of refetching
  daily — the prefetch-arrival handler then re-renders the
  daily chart with the updated synthetic today-bar (audit
  `daily-today-upsample`).

### Class attributes expected on the host

`_MIN_POLL_BACKOFF_MS`, `_POLL_RETRY_DELAY_MS`, `_POLL_RETRY_MAX`.

## Dependencies

- Internal: `..constants.interval_minutes`, `..constants.is_intraday`,
  `..data.DATA_SOURCES`, `..core.timezones.ET` (lazy inside scheduler
  helpers). Reads (no writes) many `ChartApp` attrs.
- External: `tkinter` (TclError class only), `datetime`, `queue`,
  `time`, `contextlib`. No matplotlib.

## Design Decisions

- **No `__init__` on the mixin**. All required state initialized
  by `ChartApp.__init__`. Plain MRO, no cooperative super.
- **Pure scheduler helpers take `now_epoch` as arg** rather than
  reading `time.time()`, so tests drive specific clock instants
  without monkey-patching.
- **Local `_silent_tcl` clone** (not shared import) to avoid the
  `gui.polling → app` import cycle.
- **Off-thread fetch** via `_fetch_executor`; `_fetch_token`
  bumped before submission so the in-flight result callback can
  drop stale data via `if token != self._fetch_token: return`.
  Prefetched arrivals reuse `_load_data`'s targeted indicator-cache
  invalidation instead of clearing the whole cache.
- **80ms worker-inbox / 50ms stream-queue** tick rates: balance
  perceived smoothness vs idle CPU.
- **Retry path bypasses aligned scheduler** — only reason to
  retry is to catch a late-published bar.

## Invariants

- `_track_after` doesn't raise if root is alive; callers wrap in
  `_silent_tcl` for tearing-down case.
- Streaming and bar-close polling mutually exclusive:
  `_schedule_next_bar_fetch` is a no-op while `_stream_active`.
- Sandbox-active short-circuits both `_schedule_next_bar_fetch`
  and `_next_bar_fetch_tick` (replay engine drives the clock).
- Retry counter increments only when prev tick declared
  `_poll_retry_expected_min_ts` AND new last bar still older
  AND interval is intraday.
- All scheduler helpers return immediately (no I/O), never raise.
