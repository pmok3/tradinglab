# streaming/schwab.py - Spec

Last updated: 2026-09-07

## Purpose
Shared Schwab bar/quote subscription facade. One lazily started worker owns
one socket for both axes; network and ACK handling live in
[`schwab_connection`](schwab_connection.spec.md), bar decoding/aggregation
in [`schwab_aggregator`](schwab_aggregator.spec.md).

## Public API
- `SchwabStreamSource(*, seed_lookup=None)`.
- `subscribe(ticker, interval, on_event) -> Callable[[], None]`. Only `1m`
  is supported; other intervals or empty symbols raise `ValueError`.
  Closed sources raise `RuntimeError`. Symbols are stripped/uppercased.
  Credential/dependency failures occur asynchronously and surface in status.
- `subscribe_quotes(symbols, on_quote) -> SchwabQuoteSubscription`.
- `get_status(ticker: str | None = None) -> StreamStatus`, an optional
  capability, not a new requirement on the `StreamSource` protocol.
- `close()`: idempotent, terminal, marks all bar/quote handles closed and
  signals worker shutdown. To re-enable a terminal-closed registry entry,
  construct/register a new `SchwabStreamSource`.
- `seed_lookup` is retained for call compatibility, deprecated with a
  warning, and **never invoked**. A prior close cannot establish a minute's
  true open. Subscription emits no synchronous seed/placeholder callback.
- Compatibility re-exports: `fetch_streamer_info`, `build_login_request`,
  `build_subs_request`, field constants and `USER_PREFERENCE_URL`.

## Dependencies
- Internal: `streaming/base`, `quotes`, `schwab_quotes`,
  `schwab_connection`, `schwab_aggregator`, `models.Candle`.
- External: stdlib only here; worker late-loads optional `websocket-client`.

## Lifecycle and health
- `StreamStatus` is immutable. `changed_at`/`last_received_at` are
  monotonic seconds. `generation` increments at the beginning of every
  connection attempt, before builders/first-data state reset; UI consumers
  can discard events from old connection epochs.
- No-argument status describes the shared transport: `IDLE` with no desired
  symbols, `CONNECTING` while login/service reconciliation is pending,
  `LIVE` only after login and all requested-service ACKs, `STALE` for missing
  transport traffic/heartbeats (30 seconds), `DISCONNECTED` for network
  failure, `AUTH_REQUIRED` for missing tokens/rejected login, `ERROR` for
  service/symbol rejection or protocol failure, `CLOSED` after close.
- Any requested service rejection fails the entire connection
  conservatively; a healthy quote axis cannot conceal failed chart service.
- `get_status(ticker)` adds bar readiness: `IDLE` without a bar subscription,
  `CONNECTING` before its first usable finite-positive bar this epoch,
  `STALE` after 90 seconds without a recent symbol bar. Both receive age
  and latest vendor bar-start age matter: an old snapshot/correction must
  not masquerade as a current trade. A same-symbol newcomer resets readiness.
  This symbol-local stale message explicitly says the shared transport is
  healthy; it neither reconnects nor marks the whole quote feed stale.
- Health timestamps update only for delivered usable bars, not bid/ask-only,
  nonfinite or out-of-order provisional snapshots. Older corrections do not
  move the latest symbol timestamp backwards.

## Design Decisions
- All auth, preferences, socket operations and reconnect work are on the
  worker, outside the source lock. `subscribe`, quote `set_symbols`,
  unsubscribe and `close` only mutate locked desired state or signal an event.
- Per-service desired sets: LEVELONE is the union of bars and quotes;
  CHART is **bar symbols only**. Every new bar or quote consumer requests
  a LEVELONE image, even for an already subscribed symbol. Quote newcomers
  need previous close; a returning/second quote consumer must not get only
  deltas. Symbol image revisions are bounded by the current desired union.
- Last-symbol removal signals shutdown immediately (no 30-second idle
  timer or pointless UNSUBS before close). Normal last-unsubscribe is
  reusable. If a subscriber arrives during shutdown, its successor worker
  starts only after the old worker's socket cleanup completes.
- Each bar subscription has its own builder. Subscription state, builders,
  symbol generations and health share the source lock; only one worker
  dispatches. Callbacks run outside locks and are isolated with sanitized
  error logging. At most one already-in-flight callback may trail unsubscribe.
- LEVELONE emits provisional `rollover` on an observed new minute, `tick`
  within it. CHART emits **`closed`**, an authoritative upsert by timestamp.
  Older CHART corrections are delivered; LEVELONE at/before the newest
  authoritative minute is suppressed so it cannot overwrite corrected OHLC.
- Reconnect resets provisional builders/volume baselines, not the per-handle
  authoritative watermark. Closed callbacks cannot resurrect subscriptions.

## Invariants
- Dormant construction; no synchronous credentials lookup or network on
  subscribe. Registry credential-presence checks remain the registry's concern.
- No zero seeds, prior-close fake opens, bid/ask midpoint trades, clock
  thread, disconnected bars or gap-filling bar loop.
- Terminal `close()` cannot be undone by old callbacks or worker completion.
- State/logs contain fixed explanatory text, never tokens, customer IDs,
  raw frames, vendor error messages or arbitrary exception text.

## Testing
`tests/unit/test_schwab_streaming.py`,
`tests/streaming/test_schwab_quotes.py`,
`tests/streaming/test_schwab_connection.py`: realistic envelopes, OHLC/volume
guards, optional lifecycle, per-service ACKs, batching and overlapping
consumers, worker-only auth, cleanup/reconnect and shutdown races. All offline.

## Known limitations
- Not yet exercised with live OAuth/entitlements. LEVELONE is a snapshot
  feed, not exact tape; CHART is required to finalize/correct OHLCV.
- Shutdown is nonblocking. It waits for the current bounded network call
  (up to 15 seconds during connect/auth/preferences, 0.25 seconds at recv).
- Per-symbol health is conservative, not an entitlement probe. Thin symbols
  can legitimately need chart polling while the shared quote socket is healthy.
