# data/stream_controller.py — Spec

Last updated: 2026-09-07

## Purpose
Encapsulates chart subscription lifetime, provider/interval capability resolution,
own-symbol readiness, generation gating, bounded queue draining, known historical
correction and append/upsert persistence.

## Public API
- `StreamController()` — owns `_queue`, `_token`, `_unsubs`, `_subs`, and `_active`.
- `IndicatorCacheLike` — protocol for optional indicator-cache invalidation (`invalidate_for_candles(candles) -> int`).
- `active` / `token` — read-only convenience properties for legacy alias sync.
- `start(source_name, ticker, interval, *, compare_on, compare_ticker, full_cache, stream_sources, is_intraday_fn) -> bool` — transactional subscribe for the primary slot only. Returns `True` when a subscription is committed.
- `stop() -> None` — unsubscribe all main-chart subscriptions, stale the token, and clear queued main-chart events.
- `apply_tick(evt, full_cache, indicator_cache, disk_save_fn=None) -> bool` —
  in-place timestamp update, including explicit authoritative `closed` events.
- `apply_rollover(evt, full_cache, trim_fn, disk_save_fn, indicator_cache=None) -> bool` — append/upsert rollover handling with disk persistence on sealed-bar appends.
- `drain() -> list[tuple[Any, ...]]` — non-blocking queue drain used by the Tk poll loop.
- `refresh_health() -> bool` — nonblocking own-symbol status/readiness.
- `matches(...)`, `context`, `subscribed` — distinguish same-context history
  refresh from a user/provider switch.
- Adapter construction failure leaves no committed subscription or context.
- `needs_reconcile`, `claim_reconcile()` — coalesced real fallback-fetch handoff.
- `history_request() -> StreamHistoryRequest` — frozen subscription token,
  connection generation and controller/adapter reconciliation revisions.
- `history_refreshed(key, full_cache, *, request=None, fresh=None)` — acknowledge
  only a matching request after its fresh result is applied. GUI always supplies
  the submission-time request and actual provider response, not merged cache.
- `last_mutation: StreamMutation` — rejected/last/append/correction repaint
  requirement; `latest_price` is only an accepted non-historical current price.

## Dependencies
- Internal: `models.Candle`, `streaming.StreamSource`.
- External: stdlib `queue`.

## Design Decisions
- **Controller owns only stream mechanics**; Tk scheduling stays in `gui.polling.PollingMixin`.
- **Token gating stays authoritative** so late callbacks from superseded subscriptions are dropped.
- **Compare mode remains both-live-or-neither**; `start()` exits early when compare is enabled.
- **Queue clearing preserves `card:` events** because ChartStack shares the same queue/drain loop.
- **Known corrections mutate in place**; never insert an unknown old timestamp.
- **Subscription is not readiness.** A legacy source requires an accepted event
  and expires after 90 seconds without one. Optional `get_status(ticker)` requires
  own-symbol LIVE and the current generation; adapter coverage adds a further gate.
- **Native startup completeness.** An authoritative-minute capability protects
  the first provisional 1m bar after subscribe or connection loss/reconnect.
  That partial snapshot cannot replace REST OHLCV, publish a price or establish
  readiness until its explicit authoritative `closed` update. Subsequent minute
  updates cannot bypass an unresolved startup minute. CHART-only sources can
  still publish authoritative observations without a LEVELONE event.
- **Epochs are explicit.** Typed callbacks carry an eighth generation field;
  legacy events retain seven fields. Source epoch changes require history
  reconciliation and discard prior readiness. No wall-clock provenance heuristic.
- Connection loss also resets coverage immediately. Higher-interval events
  cannot mutate charts until the requested history reconciliation completes.
- **Refresh is idempotent.** Same context and source preserve the subscription
  and warm-up state across ordinary polling reloads.
- **Debt precedes takeover.** A safe new higher-interval bucket cannot cancel or
  invalidate backfill owed to an incomplete prior bucket. Submission-time
  reconciliation revisions reject late pre-boundary acknowledgements; the owed
  timestamp must appear in the fresh response, not merely the merged cache.
- **Price publication is timestamped.** Accepted latest `closed` observations
  advance the overlay, including a same-minute authoritative final correction.
  The publication watermark includes its connection generation and never moves
  behind a newer provisional minute. Historical corrections do not refresh the
  last-good-event clock or regress the published price.
- **Rollovers persist only on true append / first-bar paths** so tick-level updates do not thrash disk writes.

## Invariants
- `_token` increments on every stop and on each successful start attempt before callbacks are stamped.
- `_active` is true only after an accepted own-symbol event, healthy source and
  safe adapter coverage, with no unresolved reconciliation.
- `drain()` preserves FIFO order, processes at most 512 entry-time events, and
  leaves new producer arrivals for the next Tk turn.
- `apply_tick()` and equal-date `apply_rollover()` preserve list identity.
- An older correction cannot update the latest-price overlay or restore freshness.
- Sealed updates/corrections persist; indicators invalidate after each mutation.
- Successful ordinary fallback history advances the adapter's protected REST
  boundary without discarding accumulated minute contributions.

## Testing
- Covered by dedicated unit tests for start/stop/drain/tick/rollover behavior.
- Existing smoke streaming tests continue to exercise `ChartApp` delegation.
- Native startup/reconnect uses the real `MinuteBarBuilder` zero-volume first
  snapshot. `tests/unit/gui/test_stream_reconciliation_polling.py` exercises the
  actual poll/readiness hooks, delayed futures, post-boundary success and
  None/error worker results with cached fallback.
