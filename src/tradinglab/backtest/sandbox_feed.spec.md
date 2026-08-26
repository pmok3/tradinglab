# backtest/sandbox_feed.py — Spec

## Purpose
`SandboxFeedWarmer` registers a replay session's *observable universe* —
the union of pinned watchlist tickers and the prepared "Download Replay
Data…" universe — with the running `SandboxController`, reading tapes
from the disk cache off the Tk thread. It exists because a session only
advances symbols it has registered, so without a warm the replay clock
moved a two-symbol market: the watchlist showed nothing and a scan had
nothing to scan.

## Public API
- `SandboxFeedWarmer(*, app, controller)` — one per session; created by
  `sandbox_app.SandboxAppController.start_feed` and dropped by
  `stop_feed`.
- `request(symbols=None) -> int` — warm `symbols`, or the resolved
  universe when omitted. Returns the count actually scheduled; symbols
  already registered with the session (or already requested) are skipped,
  so repeat calls are top-ups rather than reloads.
- `cancel()` — stop the warm; in-flight disk reads are discarded on
  arrival.
- `active -> bool`, `progress() -> tuple[int, int]` — `(registered, total)`.

## Dependencies
- Internal: `disk_cache.load_window`, `backtest/replay.SandboxController`
  (`register_ticker`, `register_daily_for`, `session_date`,
  `lookback_days`, `interval`, `data_source`, `daily_lookback_bars`),
  and on the app: `_fetch_executor`, `_await_future_on_tk`,
  `_track_after`, `_pinned_ticker_union`, `_sandbox_universe`, `_status`.
- External: none.

## Design Decisions
- **Registration is cheap; scanning is not.** Per symbol a warm costs one
  windowed disk read, a session-window trim and a small NumPy
  `BarSeries` build, then microseconds per tick (one `searchsorted` in
  `_sync_visible_to_clock`, one in the engine's `_index_by_symbol_at`).
  Re-scanning them is the expensive part: measured on the ARM64 dev box,
  `ScanRunner.run` over 500 symbols × 400 bars costs ~96 ms for one scan
  and ~423 ms for three, per tick, on the Tk thread. This module
  therefore never triggers a scan — the decision to scan lives with the
  consumer in [sandbox_app](sandbox_app.spec.md).
- **Cache-only, never the network.** Both universe sources are on disk by
  construction: the prepared universe was downloaded by
  `preload.service`, and pinned watchlist tickers are chart history. A
  warm that fetched would turn Start into a several-hundred-request
  stall and would violate strict-offline sessions outright.
- **Windowed reads, not `disk_cache.load`.** `load` materialises
  everything ever fetched for a key (~4,700 records for a 60-day 5m
  file); at universe scale that is ~2.3M JSON objects parsed and held.
  `load_window` bounds the read to the session window — see
  [disk_cache](../disk_cache.spec.md).
- **Disk off-thread, registration on the Tk thread.** `register_ticker`
  mutates engine state, so it cannot run on a worker. Batches of
  `_REGISTER_BATCH` (25) symbols are registered per `_track_after` hop so
  a 500-symbol warm never blocks the event loop for more than one batch.
  Futures cross back via `app._await_future_on_tk`, never
  `self.after` from a worker (`tk.createcommand` blocks off the main
  thread on this Tk build — see [app](../app.spec.md)).
- **`prefetch_events=False` for warmed symbols.** One event fetch per
  symbol would mean hundreds of network round-trips for glyphs nobody is
  looking at. `SandboxController.set_focus` tops the bundle up lazily
  when a symbol is actually charted.
- **The session's pinned `data_source` wins.** Reading a different vendor
  than the reference timeline would replay one symbol against another's
  tape (audit `sandbox-data-source`). Falls back to `source_var` only
  when nothing is pinned.
- **Date-string window bounds, padded.** `load_window` compares ISO date
  prefixes, which carry whatever UTC offset the vendor wrote, so the
  request pads `_WINDOW_PAD_DAYS` either side and lets the controller's
  `filter_candles_to_session` make the exact cut. Lookback is in
  *trading* days, so the calendar window is padded for weekends too:
  over-reading a few sessions is free, under-reading silently starves
  indicator warmup.
- **A `ValueError` from `register_ticker` is skipped, not retried.**
  That means the symbol is already registered with different content —
  the live registration wins, because replacing a `BarSeries` mid-session
  would retroactively change open-position accounting.

## Invariants
- `request()` never registers a symbol already in
  `controller.full_candles_by_symbol`.
- The warm issues zero network requests.
- No `register_ticker` call happens off the Tk thread.
- After `cancel()`, no further symbol is registered.
- Symbols with no cached data are counted in `progress()` and reported
  once, not retried in a loop.

## Data Flow / Algorithm
```text
request()
  resolve universe  = pinned watchlists ∪ _sandbox_universe
  pending           = universe − already-registered − already-requested
  for each chunk of _LOAD_BATCH (40):
      executor.submit(_work)              # worker: disk_cache.load_window
      _await_future_on_tk(fut, _on_loaded)
_on_loaded  (Tk thread)
  _register_slice(loaded, 0)
      register up to _REGISTER_BATCH (25) via register_ticker(prefetch_events=False)
      + register_daily_for when daily bars came back
      more left?  -> _track_after(1, _register_slice, loaded, end)
      done?       -> status line + _refresh_watchlist_for_sandbox
                                 + _refresh_scanner_for_sandbox
```

## Testing
- `tests/unit/backtest/test_sandbox_feed.py` — universe resolution
  (watchlists ∪ prepared universe), pinned-source selection, batching
  hops, `prefetch_events=False`, cancel, idempotent top-up,
  no-cached-data reporting, and the off-thread executor path (the
  callback receives the future's *resolved value*, not the future).
- `check_d85_sandbox_feed_warms_watchlist` — end-to-end wiring through a
  real `ChartApp`: the feed registers a pinned watchlist ticker under a
  session source that differs from `source_var`, the watchlist Last
  populates from the replay clock, and `next_bar` advances it.
- `tests/unit/test_disk_cache_window.py` — the windowed read this module
  depends on.

## Known limitations / Future work
- A symbol missing from the disk cache is reported, not fetched. The user
  is directed to `Sandbox → Download Replay Data…`, which is the
  deliberate offline contract.
- The warm has no partial-progress UI beyond the status line.

## Recent history
- Added with the sandbox market-feed work: replay ticks now advance the
  whole watchlist and scan universe, not just the focused chart.
