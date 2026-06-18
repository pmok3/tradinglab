# data/ratio_source.py — Spec

## Purpose
Synthetic **ratio pseudo-symbols** — a typed ticker that charts the per-bar
quotient of two real symbols. The canonical (and currently only registered)
example is **RSPSPY** = `RSP / SPY`: the equal-weight S&P 500 ETF divided by
the cap-weight ETF, a standard macro / breadth gauge (rising ⇒ broad
participation; falling ⇒ mega-cap concentration).

The user types `RSPSPY` in the ticker box and it charts like any other symbol.

## Public API
- `RATIO_SYMBOLS: dict[str, tuple[str, str]]` — registry mapping an UPPERCASE,
  separator-free pseudo-symbol → `(numerator, denominator)`. Adding a gauge is
  a one-line edit (e.g. `"QQQSPY": ("QQQ", "SPY")`). No other wiring needed.
- `parse_ratio_symbol(ticker) -> tuple[str, str] | None` — case-insensitive,
  whitespace-tolerant lookup. Returns `None` for any non-ratio ticker (the
  common case) and for `None`/empty input, so callers can cheaply gate on it.
- `is_ratio_symbol(ticker) -> bool` — convenience predicate.
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
- **Resolution lives at the fetcher, not a new `DATA_SOURCES` entry.** The hook
  is at the top of `yfinance_source.fetch_live_data`: if `parse_ratio_symbol`
  matches, it calls `fetch_ratio(..., leg_fetcher=fetch_live_data)` and recurses
  on the two legs. Because every fetch surface (main chart, compare panel,
  companion prefetch, watchlist, and the daily synthetic today-bar via its 5m
  legs) routes through `DATA_SOURCES["yfinance"]`, resolving here covers them
  all with one edit. No infinite recursion: the legs (`RSP`, `SPY`) are not
  themselves ratio symbols.
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
