# streaming/quote_book.py — Spec

## Purpose
The coalescing store between a quote stream and a UI: stream threads
write, a UI timer samples. Holds the current merged
[`Quote`](quotes.spec.md) per symbol plus the two timestamps a consumer
needs to tell "quiet symbol" from "dead feed".

## Public API
- `@dataclass(frozen=True, slots=True) QuoteEntry` — `quote: Quote`,
  `received_at: float`.
  - `symbol` property.
  - `price_age_s(now=None) -> float | None` — seconds since the
    **vendor's** event time; `None` when the vendor sent no timestamp.
  - `feed_age_s(now=None) -> float` — seconds since *we* received an
    update for this symbol.
  - `pct_change() -> float | None` — 1-Day % from `last`/`prev_close`;
    `None` when either leg is missing or `prev_close` is zero.
- `class QuoteBook(*, clock=time.time)`
  - `update(quote)` — merge one update; safe from any thread.
  - `retain(symbols)` — drop entries outside the set.
  - `clear()`
  - `get(symbol) -> QuoteEntry | None`
  - `snapshot() -> Mapping[str, QuoteEntry]` — consistent copy for one
    paint.
  - `__len__()`
  - `feed_age_s(now=None) -> float | None` — seconds since the most
    recent update **across all symbols**; `None` before anything
    arrives.

## Dependencies
- Internal: [`streaming/quotes`](quotes.spec.md).
- External: stdlib only (`threading`, `time`, `dataclasses`). No Tk.

## Design Decisions
- **This is not a queue, and dropping updates is the contract.**
  `scanner.tick_source.QueuedTickSource` buffers every tick because a
  missed *bar* is a permanent hole in a series. A quote consumer has the
  opposite requirement: a tile, a percent column, a P&L badge each
  render one current value, and the path between two paints is not just
  unnecessary but harmful to retain. Five hundred symbols during regular
  hours produce on the order of a thousand updates a second against a
  4 Hz paint; queueing would buffer ~250 values per symbol per paint,
  allocate for every one, and still show only the last. The name
  "book" rather than "queue" is meant to keep that visible at call
  sites.
- **Merge, don't replace** — delegated to `Quote.merged_onto`. See
  [`quotes`](quotes.spec.md) for why `prev_close` makes this
  load-bearing rather than a nicety.
- **Two clocks, because there are two failure modes.** A quiet
  small-cap has an old `price_age_s` on a perfectly healthy feed — a
  *per-tile* problem whose honest response is to mark that tile. A dead
  socket freezes `received_at` for every symbol at once — a *feed-level*
  problem, where marking 500 tiles individually would bury the single
  fact that matters. `QuoteBook.feed_age_s` exists so the second case is
  detectable without scanning and can be reported as what it is.
- **`feed_age_s` takes the newest, not the oldest.** On a wide universe
  something always trades, so a healthy subscription keeps this near
  zero during market hours regardless of any individual symbol's
  quietness. Taking the oldest would report a permanently sick feed.
- **Ages clamp at zero.** Vendor clocks and the local clock disagree;
  a negative age would render as "from the future" and is never useful.
- **The lock is held only for a dict read/write, never across a
  callback**, so a slow consumer cannot stall a socket thread.
- **No LRU (§7.21)** because the key space is the subscribed universe,
  not open-ended. A caller whose subscription set churns over a long
  session calls `retain`.

## Invariants
- `update` never raises, and ignores a blank symbol.
- Symbols are normalized `strip().upper()` on both write and read.
- `snapshot()` is isolated from later writes (entries are frozen).
- `price_age_s` / `feed_age_s` are `>= 0`.
- After `retain(s)`, `get(x)` is `None` for every `x` outside `s`.

## Testing
`tests/streaming/test_quotes.py` — partial-update merge, symbol
normalization, blank rejected, 1000 writes leave one entry
(coalescing), snapshot isolation, `retain`/`clear`, 20 concurrent
writers lose nothing, price-age vs feed-age separation, `None` age
without a vendor timestamp, clock-skew clamp, `feed_age_s` newest-wins
and `None` when empty, `pct_change` sign/zero/missing-leg cases.

## Known limitations / Future work
- No change-notification hook: consumers poll `snapshot()` on their own
  timer. Deliberate for a 4 Hz painter; a consumer needing edge-triggered
  behaviour (an alert engine) would want a dirty-set, which should be
  added as an opt-in rather than by making every write notify.

## Recent history
- Introduced with the live heatmap as the thread boundary between a
  quote source and the Tk painter.
