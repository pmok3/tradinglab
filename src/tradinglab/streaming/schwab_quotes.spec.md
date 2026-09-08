# streaming/schwab_quotes.py — Spec

Last updated: 2026-09-07

## Purpose
[`QuoteSource`](quotes.spec.md) implementation over Schwab's
`LEVELONE_EQUITIES` streamer service — the breadth adapter that lets the
live heatmap (and later a live scanner / watchlist) run without spending
REST quota.

## Public API
- `LEVELONE_QUOTE_FIELD_IDS = ["0","3","8","10","11","12","35"]`.
- `ADVISORY_SYMBOL_LIMIT = 500`.
- `quote_from_levelone(symbol, decoded) -> Quote` — pure.
- `plan_symbol_change(current, desired) -> (to_add, to_remove)` — pure,
  sorted, normalized.
- `class SchwabQuoteSubscription(source, on_quote)` — `symbols` snapshot set,
  `set_symbols(symbols)`, `deliver(quote)` (connection thread),
  `close()`.
- `class SchwabQuoteSource(stream_source=None)` —
  `subscribe_quotes(symbols, on_quote)`.
- `make_source(**kwargs) -> SchwabQuoteSource` — registry factory,
  registered as `schwab-quotes` when Schwab REST credentials exist.

## Dependencies
- Internal: [`streaming/quotes`](quotes.spec.md),
  [`streaming/schwab`](schwab.spec.md) (the shared connection, resolved
  lazily), [`core/timezones`](../core/timezones.spec.md)
  (`normalize_epoch_to_seconds`).
- External: stdlib only. The socket lives in `streaming/schwab_connection.py`.

## Design Decisions
- **One connection, two axes.** Schwab permits a single streamer
  connection per user — a second socket is not a second feed, it evicts
  the first. So this does **not** open its own connection; it delegates
  to the process-wide `SchwabStreamSource`, which owns the socket and
  fans each LEVELONE message to both the bar aggregators and the quote
  subscribers.
- **Quote symbols get no `MinuteBarBuilder`.** They join
  `_symbols_subscribed` so the reconnect re-image covers them, but they
  create no `_Subscription`. Routing 500 heatmap tiles through the bar
  path would stand up 500 aggregators and 500 REST seed lookups to
  rebuild a number the wire already sends — the whole reason this
  adapter exists.
- **The field map is the current Schwab one and differs from legacy TDA
  from field 10 onward.** TDA: 10/11 were times-since-midnight, previous
  close was 15. Schwab: 10 high, 11 low, **12 previous close**. Using a
  TDA table silently reads the exchange ID as a price. Verified against
  the Schwab Trader API Streamer Guide §3.1 and cross-checked against
  `schwab-py` and `Schwabdev`; pinned by
  `test_field_ids_match_the_current_schwab_map_not_the_legacy_tda_one`.
- **Field 35 is epoch milliseconds** and is normalized to seconds on the
  way in (§7.7). Skipping it makes every price read ~55,000 years stale
  and marks the whole map permanently dim.
- **An absent key stays `None`.** Schwab sends a full image on the
  initial SUBS and change-only deltas afterwards. Coercing a missing
  field to `0.0` would overwrite a good previous close on the first
  price-only delta and blank every percent on the map.
- **Per-subscriber symbol filtering.** Two consumers with overlapping
  but different universes share one socket; each sees only its own.
- **`set_symbols` publishes desired state, never does network work.**
  The shared worker reconciles it incrementally to batched ADD/UNSUBS.
  Index membership moves by a
  name or two, and a close-and-resubscribe would blank the map for as
  long as the re-image takes.
- **Symbol edits and close are atomic under the source lock.**
  `_set_quote_symbols` replaces `_symbols` only while the handle is alive.
  `symbols` returns a copy, so external mutation cannot alter desired wire
  state. A racing close prevents stale symbol edits resurrecting a handle.
  Delivery snapshots eligibility under the same lock and calls the consumer
  outside it; at most one already-in-flight callback may complete after close.
- **Every newly added consumer needs an image**, including a second quote
  consumer for an existing quote/bar symbol. Image revisions request an
  ADD re-image so `prev_close` is not lost to change-only delta delivery.
- **Closing one subscriber keeps symbols another still wants**, and the
  same rule applies across axes: dropping the last *bar* subscriber for
  a symbol does not unsubscribe it if the quote axis still wants it.
- **The symbol ceiling is advisory.** Schwab documents that a limit
  exists (error 19 `REACHED_SYMBOL_LIMIT`) but publishes no number; 500
  is the widely-reported community value. We log rather than refuse — a
  wrong guess that blocks a legitimate subscription is worse than the
  server's own error.
- **No registered stream source degrades to `NullQuoteSource`**, so a
  missing Schwab config cannot raise into window construction.

## Invariants
- `quote_from_levelone` never raises; unparseable values become `None`.
- Nonfinite floats (NaN and positive/negative infinity) become `None`.
- `Quote.ts` is epoch **seconds**.
- `close()` is idempotent, and a closed subscription delivers nothing.
- A raising subscriber does not stop delivery to the others.
- LEVELONE's wire symbol set is the union of bars and every quote set;
  quote-only symbols never subscribe to CHART_EQUITY or create builders.

## Testing
`tests/streaming/test_schwab_quotes.py` — field-map pins (including the
TDA divergence and that the bar subscription requests the union),
full-image decode, ms→s normalization, delta leaves fields `None`,
delta-merges-onto-image keeps previous close, unparseable values,
symbol normalization, unknown wire fields dropped; `plan_symbol_change`
delta / normalization / ordering; and socket-free subscription
bookkeeping — desired-state edits, per-subscriber filtering, union
of two subscribers, close keeps another's symbols, idempotent close,
raising subscriber isolated, no-subscriber dispatch, null degradation;
plus close/late-edit idempotence, copied symbol snapshots, returning/second
consumers requesting images, and no network during edits/teardown.
`tests/streaming/test_schwab_connection.py` covers actual worker ACKs,
batched 500-symbol wire reconciliation, shared service membership,
reconnect and lock-free worker auth using fake sockets/tokens.

## Known limitations / Future work
- **Unexercised against a live feed.** The pure layer and scripted socket
  path are tested offline. The first
  live run should verify (a) that the initial SUBS image really does
  carry field 12, (b) the real symbol ceiling, and (c) throughput at
  ~500 names.
- Duplicate ADD re-image behavior must still be confirmed with a live login;
  the local client requests it explicitly without replacing other consumers.
- No entitlement probe: whether a given login receives real-time or
  delayed LEVELONE is not detectable here.

## Recent history
- Introduced with the live heatmap, as the first consumer of the quote
  axis.
