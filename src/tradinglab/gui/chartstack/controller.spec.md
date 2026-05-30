# `chartstack/controller.py` — Per-card FSM + subscription refcounting

## Purpose
Owns the data-lifecycle state machine for one card (idle → fetching
→ ready → live → halted → error) and the refcount table that lets
two cards bound to the same `(src, ticker, interval)` share a
single upstream stream subscription (the §5.3 "no 5× broker-quota
explosion" guarantee).

## Public API
- `CardState` enum: `IDLE`, `FETCHING`, `READY`, `LIVE`, `HALTED`,
  `ERROR`.
- `CardController(slot_index, owner_app=None)`:
  - `state` (read-only property).
  - `binding` (read-only property).
  - `token` (read-only property) — int that bumps on every
    `bind`/`start`/`stop`. Worker payloads carry the token they
    observed at submit time; the panel drops payloads whose token
    is older than the controller's current `token`.
  - `stream_key` (read-only property) — `(src, ticker, interval)`
    tuple of the active stream subscription, or `None` if no
    stream is held.
  - `bind(binding)` — replace held binding; resets to `IDLE`, bumps `token`,
    releases any held stream subscription, clears any held halt.
  - `start()` — resolves source on the Tk thread, pins the card interval to
    `"1d"`, bumps `token`, transitions to `FETCHING`, and submits a worker on
    `owner_app._fetch_executor`. The worker calls `DATA_SOURCES[src](symbol,
    "1d")` and pushes `("card_stash", (slot_index, token, symbol, bars))`
    onto `owner_app._worker_inbox`. When called on the Tk main thread
    (test-shim path), result is dispatched synchronously to
    `owner_app._chartstack.apply_card_stash`. No-op when binding is `None`,
    when `owner_app` lacks `_fetch_executor` / `_worker_inbox`, or when
    `source_var` is unreadable.
  - `start_stream(registry, *, is_intraday=None)` — resolves source on the Tk
    thread, pins the card interval to `"1d"`, then applies the intraday-only
    stream gate. In normal app use `is_intraday("1d")` is `False`, so daily
    ChartStack cards do not subscribe to live streams. If a test override
    allows the daily key through, subscribing is refcount-deduped and the
    upstream callback enqueues `(token, "card:N", src, ticker, "1d", kind,
    bar)` onto `owner_app._stream_queue`. No-op when binding is `None`, owner
    lacks `_stream_queue`, `is_intraday("1d")` is `False`, or
    `STREAM_SOURCES[src]` is missing. Idempotent on the same `stream_key`.
  - `stop_stream()` — release held subscription (decrement registry refcount;
    auto-unsubscribe upstream at zero). Idempotent. Does NOT change FSM
    state — callers wanting IDLE call `stop()`.
  - `mark_ready()` — transition to `READY`. **LIVE-preserving**: skips
    assignment when already `LIVE`, so a late fetch landing after the
    stream went live doesn't demote the card.
  - `mark_error()` — transition to `ERROR` (no bars in fetch).
  - `stop()` — bumps `token`, resets to `IDLE`, calls `stop_stream()`,
    clears any held halt. Drops any in-flight stash that lands post-stop.

  Halt API:
  - `halt_index` (read-only) — `int` index of halt bar, or `None`.
  - `is_halted` (read-only) — convenience bool.
  - `mark_halted(index)` — transition to `HALTED`, record halt bar
    (coerced to `int`, clamped `>= 0`).
  - `clear_halt()` — drop halt index. Returns to `LIVE` if a stream
    subscription is held, else `READY`.

- `SubscriptionRegistry`:
  - `subscribe(src, ticker, interval, callback, *, upstream_factory)` —
    registers `callback` for the `(src, ticker, interval)` key and returns
    a per-consumer release closure. First consumer triggers
    `upstream_factory()` (which receives the registry-owned fan-out
    dispatcher and returns the upstream unsubscribe handle); subsequent
    consumers piggyback. Dispatcher iterates a point-in-time snapshot of
    callbacks **outside** the registry lock (consumer code can't deadlock
    it). Release closures are idempotent (`done` flag). Concurrent-release
    race: if the entry is wiped while `upstream_factory()` is still running,
    the upstream unsub is invoked to avoid a leak.
  - Legacy: `refcount(src, ticker, interval)`,
    `release(src, ticker, interval)`, `count(src, ticker, interval)` —
    kept for original wireframe unit tests.

## Design decisions
- **FSM was finalized early** even though only a few transitions fire — locks
  the surface so later milestones land without touching `card.py`/`panel.py`.
- **Token gating** lives on the controller (payloads carry the token they
  were submitted under) — slow fetches landing after a re-bind are silently
  discarded with no need to cancel the worker.
- **Workers must not touch Tcl/Tk vars.** Source is resolved on the
  calling (Tk) thread and embedded in the worker closure; cards use a fixed
  `"1d"` interval. Touching `owner_app.source_var.get()` from the worker
  thread blocks `tk.createcommand` (same contract as
  `gui/watchlist_tab.py:_preload_one_last`).
- **Synchronous dispatch on Tk thread**: when `start()` is invoked from the
  main thread (e.g. tests with a sync executor), the worker bypasses the
  inbox and dispatches directly to the panel — lets unit tests verify
  rendering without spinning an `after()` poll.
- **Lazy `DATA_SOURCES` import** keeps yfinance out of the module-level
  import graph — tests construct controllers without yfinance installed.
- **`owner_app` is held loosely** (no `TYPE_CHECKING` import of `ChartApp`) —
  module is importable without the full app tree; unit tests pass
  `owner_app=None`.

