# backtest/replay_events.py — Spec

## Purpose
Earnings / dividends / corporate-actions bridge for `SandboxController`. The headless backtest kernel (`engine.py`, `bars.py`, `clock.py`, `portfolio.py`, …) intentionally never imports from `tradinglab.events`; this mixin is the explicit boundary that translates per-symbol `EventBundle` data into engine-shaped `CorporateAction` records and exposes clock-gated read accessors for the GUI render path and journal proximity tags.

Mixed into `SandboxController` (which lives in `replay.py`). Relies on attributes the controller's `__init__` initialises — see "Mixin contract" below.

## Public API
- `class EventsControllerMixin`:
  - `set_event_bundle(symbol, bundle) -> None` — install (or overwrite) a per-symbol `EventBundle`. Idempotent on identity; on engine-side different-content re-register, the original bundle's actions stay queued (engine refuses content-different re-register).
  - `prefetch_events_for(symbol) -> None` — schedule a background fetch via `app._fetch_executor`. Token-gated by `_events_fetch_token` so a session restart / `cycle_to_next` discards in-flight results. Result marshalling uses `app._await_future_on_tk` (a Tk-thread polled future) — **never** `fut.add_done_callback` + `app.after` from a worker thread (would block `tk.createcommand` on this Python/Tk build). Falls back to a sync inline fetch when no executor / no await-helper is available (smoke tests, headless callers).
  - `_register_corporate_actions_from_bundle(symbol, bundle) -> int` — translate the bundle's `DividendRecord`s into engine `CorporateAction`s and register them. Returns the number of actions queued. Skips events whose `ex_ts` falls outside the engine's master timeline (would never fire).
  - `events_visible_for(symbol) -> Optional[EventsView]` — clock-gated view at the current `clock_ts()`. Returns `None` when no bundle is installed, when the clock is pre-tick, or when the events package isn't importable. Engine clock is epoch seconds; events module is ms — the bridge converts.
  - `_compute_event_proximity(symbol, ts) -> Dict[str, Any]` — snapshot for a `PreTradeEntry`. Returns the six journal proximity fields (`next_earnings_ts`, `last_earnings_ts`, `last_dividend_ts`, `last_split_ts`, `earnings_proximity_tag`, `dividend_proximity_tag`). All-zero / empty-string fallback on missing data — never raises. Forward fields are zeroed in blind mode (delegated to `events.gating.events_visible_for`).

## Mixin contract
- **No `__init__`**: relies on attributes initialised by `SandboxController.__init__`: `_raw_full_events`, `_events_fetch_token`, `engine`, `app`, `active`, `blind`, plus the `clock_ts()` method.
- **No cooperative `super()`** — plain MRO.
- **No name collisions** with other controller mixins or `SandboxController` itself.

## Dependencies
- Internal (lazy-imported): `..events.EVENT_SOURCES`, `..events.gating.events_visible_for`, `..defaults.get`, `..defaults.TUNABLES`, `.actions.CorporateAction`.
- External: `numpy` (for `searchsorted` aligning ex-dates onto the engine's int64 timeline).
- All `events`-package imports are deferred to call sites so the kernel's headless-import contract is preserved even when the mixin file is imported.

## Design Decisions
- **Explicit boundary, not a transitive import**: the engine never depends on `events`; the controller does the translation once at install time. Future Phase 2 batch runners can skip events entirely without touching engine code.
- **Tk-thread await via `app._await_future_on_tk`**: documented in `app.spec.md` "Worker-inbox queue" — calling `self.after` from a worker thread blocks `tk.createcommand` on this Python/Tk build, which would saturate `_fetch_executor` after a handful of prefetches.
- **Idempotent `set_event_bundle`**: re-installs simply overwrite the cached bundle. Engine refuses content-different re-register (raises `ValueError`) — the mixin swallows that so a later refresh affects only the gated display, not the already-queued actions. Rationale: the engine has already consumed corporate actions at `start_session` / `register_ticker` time; retroactively swapping them would break determinism.
- **Skip out-of-timeline ex-dates**: events whose `ex_ts` falls outside `[timeline[0], timeline[-1]]` are dropped. They'd accumulate as inert queue entries that never fire and complicate the engine's idempotency check.
- **`_compute_event_proximity` reads `earnings_window_days` from `TUNABLES`** with a default of 10. Soft import on `defaults` so a bare-events smoke test that bypasses the defaults loader still works.
- **Engine-kind map collapses the events taxonomy** (`cash` / `special` / `spinoff` / `stock_split`) onto the four engine kinds (`cash_dividend` / `special_dividend` / `spinoff_cash` / `stock_split`). Unknown kinds default to `cash_dividend` — safest fallback (credit cash, don't rescale shares).
- **`searchsorted` aligns `ex_ts` onto the timeline**: the engine's corporate-action phase only fires when the timeline cursor lands exactly on a registered `ts`. Floor-to-next-bar via `searchsorted(side="left")` ensures the action triggers on the ex-date's bar (or the next available one if the ex-date isn't a trading day).

## Invariants
- The kernel headless contract (`import tradinglab.backtest` works without a display / Tk runtime) is preserved — every `events` import is lazy and inside a function body.
- `prefetch_events_for` never blocks the Tk main thread: it either submits to the fetch executor or short-circuits to an inline sync fetch (smoke path only — those callers have already chosen sync semantics).
- Token gating: a `set_event_bundle` callback fired with a stale `_events_fetch_token` is a silent no-op. A session restart bumps the token and any in-flight result is discarded.
- `_register_corporate_actions_from_bundle` returns 0 (not raise) when the engine is `None`, when the bundle has no dividends, or when the timeline is empty.

## Testing
- Covered indirectly via sandbox smoke tests (`test_smoke_sandbox.py`) and the events-feature smoke `check_b64_events_save_load_roundtrip` (held-through-ex-div round-trip).

