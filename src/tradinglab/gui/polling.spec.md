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
  grace_daily_s=300) -> int` — anchors on last bar + interval +
  grace so session-aligned intraday bars (e.g. 1h bars closing at
  10:30/11:30 NYSE) are honored. For daily / weekly / monthly,
  always schedules to 16:05 ET next weekday (daily timestamps
  don't encode close time).
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
  data redraw), `card_stash` (chartstack card cache fill).
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
  `_prefetched_raw`. Bumps `_fetch_token` before submission so
  stale results from a superseding ticker switch can drop.

### Class attributes expected on the host

`_MIN_POLL_BACKOFF_MS`, `_POLL_RETRY_DELAY_MS`, `_POLL_RETRY_MAX`.

## Dependencies

- Internal: `..constants.interval_minutes`, `..constants.is_intraday`,
  `..data.DATA_SOURCES`. Reads (no writes) many `ChartApp` attrs.
- External: `tkinter` (TclError class only), `zoneinfo` (lazy),
  `datetime`, `queue`, `time`, `contextlib`. No matplotlib.

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
