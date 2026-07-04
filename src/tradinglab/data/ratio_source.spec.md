# data/ratio_source.py — Spec

## Purpose
**Ratio pseudo-symbols** — a typed ticker that charts the per-bar quotient of
two real symbols. The user types the general **`NUM/DEN`** form straight into
the ticker box — e.g. `AMD/NVDA` (intra-semiconductor leadership), `XLF/SPY`
(financials sector RS), `RSP/SPY` (equal-weight-vs-cap-weight breadth) — and it
charts like any other symbol everywhere (main chart, compare, watchlist).

**`NUM/DEN` is the only supported form.** There is no shorthand / alias
registry — a separator-free string like `RSPSPY` is treated as an ordinary
(and, for that example, non-existent) ticker, not a ratio.

## Public API
- `RATIO_DELIMITER = "/"` — the single delimiter that denotes a ratio in a
  typed ticker. `/` is chosen because (a) `disk_cache` already sanitises it
  out of cache filenames, and (b) it doesn't collide with real symbols that
  use `-`/`.` (`BRK-B`, `BRK.B`, `BTC-USD`) or `:` (Windows-illegal / exchange
  prefix).
- `parse_ratio_symbol(ticker) -> tuple[str, str] | None` — case-insensitive,
  whitespace-tolerant. Parses the general `NUM/DEN` form: exactly one `/`
  splitting into two non-empty legs (rejects nested `A/B/C`). Returns `None`
  for any non-ratio / empty / `None` input.
- `is_ratio_symbol(ticker) -> bool` — convenience predicate.
- `canonical_ratio_symbol(ticker) -> str` — canonical storage/key form:
  ratios normalise to uppercase space-free `NUM/DEN` (so `amd / nvda` and
  `AMD/NVDA` share one cache key / watchlist entry); non-ratios uppercased +
  stripped.
- `ratio_display_label(ticker) -> str` — human label `"AMD / NVDA"` for chart
  title / watermark / window title / watchlist rows; non-ratios returned
  unchanged.
- `compute_ratio_candles(numerator, denominator) -> list[Candle]` — per-bar
  component-wise quotient of two candle series (pure function, no I/O).
- `fetch_ratio(ticker, interval, *, leg_fetcher) -> list[Candle] | None` —
  fetches both legs via `leg_fetcher` (the active source's
  `(ticker, interval) -> candles` callable) and computes the ratio.

## Dependencies
- Internal: `..models.Candle`.
- External: none. No network — it composes whatever the caller's `leg_fetcher`
  returns, so it is source-agnostic.

## Design Decisions
- **`/` delimiter, strict 2-leg parse, nested rejected.** See `RATIO_DELIMITER`
  above. The parser rejects `A/B/C` (split ≠ 2 parts) so the leg-fetch
  recursion is bounded (a single leg has no `/` and can never re-parse as a
  ratio).
- **Never persisted to disk.** A ratio is derived from its two legs (which DO
  cache individually). `disk_cache.save`/`load` short-circuit for ratio tickers
  (`disk_cache._is_ratio_ticker`) — see `disk_cache.spec.md`. This avoids the
  filename-illegal `/`, keeps `list_entries`/cache-export clean, and prevents a
  cached ratio going stale vs its legs. The in-memory `_full_cache` (keyed by
  the raw `(source, ticker, interval)` tuple — a `/` in a dict key is fine)
  still gives session-level responsiveness.
- **Resolution lives at the fetcher, not a new `DATA_SOURCES` entry.** The hook
  is at the top of `yfinance_source.fetch_live_data`: if `parse_ratio_symbol`
  matches, it calls `fetch_ratio(..., leg_fetcher=fetch_live_data)` and recurses
  on the two legs. Because every fetch surface (main chart, compare panel,
  companion prefetch, watchlist, and the daily synthetic today-bar via its 5m
  legs) routes through `DATA_SOURCES["yfinance"]`, resolving here covers them
  all with one edit. Ratios therefore resolve on the yfinance source (the
  default + only fully-wired live source); extending to other sources is a
  follow-up.
- **Component-wise OHLC quotient + widened envelope.** For each shared bar:
  `O = numO/denO`, `H = numH/denH`, `L = numL/denL`, `C = numC/denC`, then
  `H ← max(O,H,L,C)` and `L ← min(O,H,L,C)` so the result is always a valid
  candle (`H ≥ O,C ≥ L`). The true intra-bar ratio *path* is unknowable from
  sealed OHLC; this is the same approximation mainstream charting platforms use
  for symbol ratios. It is exact at the open and close; the high/low are an
  envelope, not a tradeable extreme.
- **Inner-join on `Candle.date`.** Only timestamps present in BOTH legs
  contribute. Mismatched calendars (halts, differing histories) drop the
  unmatched bars rather than guessing.
- **Volume is set to `0`.** A ratio has no meaningful volume; the volume pane
  renders flat. Chosen over a fabricated value (min/sum of legs) to stay
  honest.
- **`session` carried from the numerator bar** so the daily today-bar
  synthesiser's regular-session filter still classifies ratio bars correctly.
- **Non-positive denominator bars are skipped** (any of `den` OHLC `≤ 0`) to
  avoid divide-by-zero and sign flips.
- **`None`/empty propagation.** `fetch_ratio` returns `None` for a non-ratio
  ticker or when *either* leg fetch fails / is empty, so the caller's existing
  `None`-handling (status message, disk fallback) applies unchanged.

## Invariants
- `parse_ratio_symbol` is total and never raises (incl. `None`/empty input).
- `compute_ratio_candles` returns bars only for timestamps in both legs; every
  returned bar satisfies `high >= max(open, close)` and `low <= min(open,
  close)`, has `volume == 0`, and a strictly-positive denominator at that bar.
- `fetch_ratio(non_ratio, ...) is None` and does NOT invoke `leg_fetcher`.
- Both legs are fetched at the SAME `interval` from the SAME source.

## Testing
- `tests/unit/data/test_ratio_source.py` — parse (case/whitespace/unknown/None),
  registry shape, compute (component quotient, envelope validity, inner-join,
  non-positive-denominator skip, session carry, empty/non-overlapping legs),
  `fetch_ratio` (happy path, non-ratio short-circuit, either-leg None/empty,
  interval pass-through), and the `fetch_live_data` routing hook (ratio →
  `fetch_ratio` with `leg_fetcher is fetch_live_data`; non-ratio bypasses it).

## Known limitations
- **OHLC high/low is an approximation** — see Design Decisions. Do not treat a
  ratio bar's high/low as a price the ratio actually traded at intra-bar.
- **Both legs must come from the same source at the same interval.** A ratio of
  two different vendors, or two different intervals, is not supported.
- **No volume / no events** — volume is 0; corporate-event glyphs are not
  composed for the synthetic symbol.
- **Daily adjustment inherited from the legs** — since legs are fetched via the
  normal source, split/dividend adjustment follows that source's policy
  (yfinance auto-adjusts). A ratio of two adjusted series is itself consistent.
