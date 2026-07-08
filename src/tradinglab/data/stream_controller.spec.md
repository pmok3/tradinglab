# data/stream_controller.py — Spec

## Purpose
Encapsulates ChartApp's main-chart live-stream lifecycle: subscribe/unsubscribe, token gating, queue draining, tick in-place mutation, and rollover append/upsert persistence.

## Public API
- `StreamController()` — owns `_queue`, `_token`, `_unsubs`, `_subs`, and `_active`.
- `IndicatorCacheLike` — protocol for optional indicator-cache invalidation (`invalidate_for_candles(candles) -> int`).
- `active` / `token` — read-only convenience properties for legacy alias sync.
- `start(source_name, ticker, interval, *, compare_on, compare_ticker, full_cache, stream_sources, is_intraday_fn) -> bool` — transactional subscribe for the primary slot only. Returns `True` when a subscription is committed.
- `stop() -> None` — unsubscribe all main-chart subscriptions, stale the token, and clear queued main-chart events.
- `apply_tick(evt, full_cache, indicator_cache) -> bool` — in-place update of the cached rightmost bar.
- `apply_rollover(evt, full_cache, trim_fn, disk_save_fn, indicator_cache=None) -> bool` — append/upsert rollover handling with disk persistence on sealed-bar appends.
- `drain() -> list[tuple[Any, ...]]` — non-blocking queue drain used by the Tk poll loop.

## Dependencies
- Internal: `models.Candle`, `streaming.StreamSource`.
- External: stdlib `queue`.

## Design Decisions
- **Controller owns only stream mechanics**; Tk scheduling stays in `gui.polling.PollingMixin`.
- **Token gating stays authoritative** so late callbacks from superseded subscriptions are dropped.
- **Compare mode remains both-live-or-neither**; `start()` exits early when compare is enabled.
- **Queue clearing preserves `card:` events** because ChartStack shares the same queue/drain loop.
- **Ticks mutate in place** to preserve candle-list object identity for existing aligned/rendered views.
- **Rollovers persist only on true append / first-bar paths** so tick-level updates do not thrash disk writes.

## Invariants
- `_token` increments on every stop and on each successful start attempt before callbacks are stamped.
- `_active` is `True` only after the new subscription set is fully committed.
- `drain()` preserves FIFO order.
- `apply_tick()` and equal-date `apply_rollover()` preserve list identity.

## Testing
- Covered by dedicated unit tests for start/stop/drain/tick/rollover behavior.
- Existing smoke streaming tests continue to exercise `ChartApp` delegation.
