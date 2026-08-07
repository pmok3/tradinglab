# streaming/quotes.py — Spec

## Purpose
The **breadth** axis of market data: a protocol + registry for sources
that publish one current value per symbol for many symbols at once.

A deliberate *sibling* of [`streaming/base`](base.spec.md), not a layer
on it. `StreamSource` is bar-centric and per-symbol — one subscription
per `(ticker, interval)`, each owning a `MinuteBarBuilder` that
synthesizes OHLCV from ticks and needs a REST call to seed itself. That
shape is correct for a chart (one symbol, deep history, exact bars) and
wrong for a 500-tile heatmap, a live scanner, or a watchlist percent
column, which want one number per symbol off a single connection.

Routing those through bar subscriptions would mean hundreds of bar
builders and hundreds of REST seeds to reconstruct a value the wire
already carries.

## Public API
- `@dataclass(frozen=True, slots=True) Quote` — `symbol: str` plus
  optional `last`, `prev_close`, `day_volume`, `day_high`, `day_low`,
  `ts` (epoch **seconds**, §7.7).
  - `merged_onto(prior: Quote | None) -> Quote` — field-wise
    last-writer-wins; a `None` field leaves the prior value alone.
    Returns `self` when `prior` is `None` or a different symbol, and
    returns `prior` unchanged when nothing was reported.
- `QuoteCallback = Callable[[Quote], None]` — invoked on the source's
  own thread; consumers marshal to their UI thread themselves, exactly
  as `streaming.base.StreamCallback` requires.
- `class QuoteSubscription(Protocol)` — `set_symbols(symbols)`,
  `close()`.
- `class QuoteSource(Protocol)` —
  `subscribe_quotes(symbols, on_quote) -> QuoteSubscription`.
- `class NullQuoteSource` — never emits, never opens a socket.
- `QUOTE_SOURCES: dict[str, Callable[..., QuoteSource]]` — registered
  **factories**, not instances.
- `register_quote_source(name, factory)` / `unregister_quote_source(name)
  -> bool` / `available_quote_sources() -> list[str]`.
- `resolve_quote_source(name=None, **kwargs) -> (resolved_name, source)`
  — `name` defaults to the `heatmap_quote_source` tunable.

## Dependencies
- Internal: [`defaults`](../defaults.spec.md), read lazily inside
  `resolve_quote_source` so importing this module never pulls in
  settings.
- External: stdlib only.

## Design Decisions
- **`None` means "not reported in this update"**, never zero and never
  "unknown forever". Vendors publish partial updates, so most records
  carry a handful of populated fields. A record whose `last` is `None`
  is not a quote with no price; it is an update that did not mention
  the price. This is why `merged_onto` exists and why consumers must
  never replace wholesale — `prev_close` typically arrives once at
  subscribe time, and discarding it on the first price-only tick would
  blank every percent on the map a second after it painted correctly.
- **`prev_close` comes off the wire.** A quote-driven consumer cannot
  reproduce the daily-bar look-ahead that
  [`backtest/heatmap`](../backtest/heatmap.spec.md) Invariant 6 has to
  defend against, because there is no historical series to index into
  incorrectly — both legs of the percent arrive in the same message.
  That makes the quote path structurally safer than the bar path, not
  merely faster.
- **`subscribe_quotes` returns a handle, not a bare unsubscribe
  callable** (which is what `StreamSource.subscribe` returns). The
  symbol set is expected to change while subscribed — index membership,
  a watchlist edit, a scanner universe — and vendors support
  incremental add/remove on an open connection. Tearing the whole
  subscription down to add one symbol would drop every other symbol's
  state for seconds.
- **Factories, not instances**, mirroring
  [`data/shares_sources`](../data/shares_sources.spec.md): a provider
  holds per-subscription state, and the registry must not become shared
  mutable state.
- **An unknown name resolves to `NullQuoteSource`, never to another
  vendor.** Consistent with `null_shares_fetcher`: a misconfiguration
  should surface as "no live quotes" (the consumer falls back to its
  REST path) rather than as plausible numbers from a feed nobody
  selected. The same applies when a factory raises.
- **`"off"` is a reserved name** resolving to `NullQuoteSource`, so the
  tunable has an explicit disable value distinct from "unset".

## Invariants
- `resolve_quote_source` never raises and always returns a source.
- Resolution performs no network I/O — a connection opens on first
  `subscribe_quotes`, not at resolve time.
- Registered names are lower-cased and non-empty.
- `merged_onto` never mutates either operand (`Quote` is frozen).
- `Quote.ts` is epoch **seconds**; adapters normalize vendor
  milliseconds before construction (§7.7).

## Testing
`tests/streaming/test_quotes.py` — merge keeps unreported prior fields,
merge onto `None`/mismatched symbol/no-op, zero is a reported value;
registry round-trip, unknown name and raising factory degrade to
`NullQuoteSource`, `"off"` resolves to null, blank name rejected, null
subscription is inert.

## Known limitations / Future work
- No per-provider capability metadata (which fields a vendor actually
  publishes, symbol-count ceiling, entitlement tier). When a second real
  adapter lands, that belongs here alongside the registry — mirroring
  `data/quality.py` for price sources. Until then a consumer discovers a
  missing `prev_close` only by seeing `None`.

## Recent history
- Introduced with the live heatmap, when it became clear that every
  many-symbol feature in the app (heatmap, scanner) was replay-only
  precisely because the app had no breadth axis — only per-symbol REST
  and per-symbol bar streams.
