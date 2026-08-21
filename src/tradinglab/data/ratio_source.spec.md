# data/ratio_source.py — Spec

## Purpose
**Ratio pseudo-symbols** — a typed ticker that charts the per-bar quotient of
two real symbols. The user types the general **`NUM/DEN`** form straight into
the ticker box — e.g. `AMD/NVDA` (intra-semiconductor leadership), `XLF/SPY`
(financials sector RS), `RSP/SPY` (equal-weight-vs-cap-weight breadth) — and it
charts like any other symbol everywhere (main chart, compare, watchlist).

**Two shapes share this syntax and they are NOT the same object:**

| | Quotient ratio `AMD/NVDA` | Scaled symbol `^VIX/15.87` |
|---|---|---|
| Meaning | per-bar quotient of two instruments | one instrument on a rescaled axis |
| High / low | **approximate** — widened envelope | **exact** — `H/k` is the true high |
| Bars | inner-joined; non-overlapping dropped | **all preserved** (a constant has no calendar) |
| Volume | forced to `0` | **preserved** (the underlying's, and real) |
| Rebase-to-100 | meaningful | **disabled** — cancels the divisor |
| Events | none | the underlying's |

`is_ratio_symbol` stays "either shape" (so cache/persistence gating is
unchanged); `is_quotient_ratio` / `is_scaled_symbol` distinguish them.

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
  for any non-ratio / empty / `None` input. **Signature and behaviour are
  deliberately unchanged by scaled-symbol support** — see Design Decisions.
- `is_ratio_symbol(ticker) -> bool` — convenience predicate; true for BOTH
  shapes.
- `parse_scale_constant(leg) -> float | None` — a leg as a positive scale
  constant. Accepts a plain positive decimal only (`16`, `15.87`, `0.5`).
- `is_numeric_leg(leg) -> bool` — whether a leg LOOKS numeric, regardless of
  validity. Public so UI copy can tell `^VIX/0` ("you meant a divisor") from
  `AMD/NVDA` ("you meant two tickers") without re-implementing the grammar.
- `scaled_symbol_parts(ticker) -> tuple[str, float] | None` —
  `(base_symbol, divisor)` for `SYM/<positive number>`, else `None`.
- `is_scaled_symbol(ticker) -> bool` / `is_quotient_ratio(ticker) -> bool` —
  the two shapes; mutually exclusive.
- `base_symbol_of(ticker) -> str` — underlying of a scaled ticker
  (`^VIX/15.87` → `^VIX`); quotients and plain tickers returned unchanged.
- `canonical_ratio_symbol(ticker) -> str` — canonical storage/key form:
  ratios normalise to uppercase space-free `NUM/DEN` (so `amd / nvda` and
  `AMD/NVDA` share one cache key / watchlist entry); non-ratios uppercased +
  stripped.
- `ratio_display_label(ticker) -> str` — human label `"AMD / NVDA"` /
  `"^VIX / 15.87"` for chart title / watermark / window title / watchlist
  rows; non-ratios returned unchanged.
- `compute_ratio_candles(numerator, denominator) -> list[Candle]` — per-bar
  component-wise quotient of two candle series (pure function, no I/O).
- `compute_scaled_candles(candles, divisor) -> list[Candle]` — exact,
  lossless division of one real series by a positive constant.
- `fetch_ratio(ticker, interval, *, leg_fetcher) -> list[Candle] | None` —
  routes to the scalar or quotient path and fetches via `leg_fetcher` (the
  active source's `(ticker, interval) -> candles` callable).

## Dependencies
- Internal: `..models.Candle`.
- External: `re` (the scale-constant grammar). No network — it composes
  whatever the caller's `leg_fetcher` returns, so it is source-agnostic.

## Design Decisions
- **`/` delimiter, strict 2-leg parse, nested rejected.** See `RATIO_DELIMITER`
  above. The parser rejects `A/B/C` (split ≠ 2 parts) so the leg-fetch
  recursion is bounded (a single leg has no `/` and can never re-parse as a
  ratio).
- **`parse_ratio_symbol` was NOT changed to carry leg kinds.** Its tuple is
  unpacked in only three places, all inside this module; every other consumer
  (~13 call sites in `app`, `disk_cache`, `base`, `hybrid_source`,
  `yfinance_source`, `config_manager`) merely tests truthiness. A numeric leg
  is a change to *leg classification and fetch*, not to *ratio detection* —
  `VIX/16` already parsed fine, it just fetched a bad ticker. Sibling
  functions keep the spec- and test-pinned signature stable.
- **Scale-constant grammar is deliberately boring**: `^\d+(\.\d+)?$`, positive
  only. Rejects `0` (divide-by-zero), negatives (a sign flip would invert the
  candle), scientific notation, thousands separators and a leading `+`. A real
  numeric-ish ticker essentially always carries a suffix (`0700.HK`,
  `BTC-USD`) that breaks the match, so collision risk is negligible for the
  vendors this app talks to.
- **Denominator-only constants.** `100/VIX` (constant numerator) and `16/4`
  (both constant) are rejected. An inverse is mathematically coherent but
  needs the candle's high and low to **swap** (`high = k / low`); getting that
  wrong silently inverts every wick, so it is a separate feature, not a
  freebie. Division only — no `*`: `SPX*0.1` is `SPX/10`, so multiplication
  adds no expressive power, only a second operator and the first step toward
  an expression engine in the ticker box.
- **A numeric-LOOKING leg is never fetched as a ticker.** `^VIX/0` fails at
  the parser rather than falling through and asking the vendor for a symbol
  named `"0"` (`is_numeric_leg` vs `parse_scale_constant`).
- **Scalar fast-path, not a synthetic constant series.** Dividing OHLC by
  `k > 0` is order-preserving, so `H/k` IS the true high — the envelope
  widening that quotients need is unnecessary. Building a fake denominator
  series and reusing the quotient path would still be numerically correct (the
  widening is a no-op under a monotone transform) but throws away the "this is
  exact" property and does pointless work. The scalar path also skips the
  inner-join entirely, so no bar is ever dropped, and never hands range
  kwargs to a non-existent second leg.
- **Scaled volume is PRESERVED.** A quotient has no honest combined volume, so
  `0` is right there. A scaled symbol is one real instrument: its volume, and
  every volume-weighted study over it, stays valid (VWAP scales by the same
  `k`; RVOL is unchanged). Forcing `0` because the string contains `/` would
  be a regression. (`^VIX` has no volume anyway — because an index has none,
  not because it was scaled.)
- **Component-wise OHLC quotient + widened envelope** (quotient path only).
  For each shared bar: `O = numO/denO`, `H = numH/denH`, `L = numL/denL`,
  `C = numC/denC`, then `H ← max(O,H,L,C)` and `L ← min(O,H,L,C)` so the
  result is always a valid candle (`H ≥ O,C ≥ L`). The true intra-bar ratio
  *path* is unknowable from sealed OHLC; this is the same approximation
  mainstream charting platforms use for symbol ratios. It is exact at the open
  and close; the high/low are an envelope, not a tradeable extreme.
- **Inner-join on `Candle.date`** (quotient path only). Only timestamps present
  in BOTH legs contribute. Mismatched calendars (halts, differing histories)
  drop the unmatched bars rather than guessing.
- **Never persisted to disk.** A ratio is derived from its legs (which DO
  cache individually). `disk_cache.save`/`load` short-circuit for ratio tickers
  (`disk_cache._is_ratio_ticker`) — see `disk_cache.spec.md`. This avoids the
  filename-illegal `/`, keeps `list_entries`/cache-export clean, and prevents a
  cached ratio going stale vs its legs. The staleness argument is weaker for a
  scaled symbol (a constant never goes stale), but the filename-illegality one
  still holds, so behaviour is unchanged for both shapes. The in-memory
  `_full_cache` (keyed by the raw `(source, ticker, interval)` tuple — a `/`
  in a dict key is fine) still gives session-level responsiveness.
- **Resolution is source-agnostic, installed at `register_source`.** Every
  fetcher in `DATA_SOURCES` is wrapped by `data.base._ratio_aware` so a typed
  `NUM/DEN` symbol is decomposed and each leg fetched from the SAME source via
  `fetch_ratio(..., leg_fetcher=<that source>)`. That wrapper also applies
  index aliases (see `index_aliases.spec.md`). Because every fetch surface
  (main chart, compare panel, companion prefetch, watchlist, sandbox, strategy
  tester, and the daily synthetic today-bar via its 5m legs) routes through a
  `DATA_SOURCES[...]` fetcher, ratios resolve everywhere on ANY source with one
  wrapper. `yfinance_source.fetch_live_data` ALSO keeps its own internal hook
  (calling `fetch_ratio(..., leg_fetcher=fetch_live_data)`) — now redundant for
  the registry path but retained so a direct importer of `fetch_live_data`
  still gets ratios. (Historically resolution lived ONLY in that yfinance hook,
  so a ratio typed under Alpaca/Polygon failed — fixed by the registration
  wrapper; see `data/base.spec.md`.)
- **Volume is set to `0` for quotients.** Chosen over a fabricated value
  (min/sum of legs) to stay honest.
- **`session` carried from the numerator / real bar** so the daily today-bar
  synthesiser's regular-session filter still classifies bars correctly.
- **Non-positive denominator bars are skipped** (quotient path, any of `den`
  OHLC `≤ 0`) to avoid divide-by-zero and sign flips.
- **`None`/empty propagation.** `fetch_ratio` returns `None` for a non-ratio
  ticker, an unsupported numeric shape, or when a leg fetch fails / is empty,
  so the caller's existing `None`-handling (status message, disk fallback)
  applies unchanged.

## Invariants
- `parse_ratio_symbol` is total and never raises (incl. `None`/empty input).
- `is_scaled_symbol` and `is_quotient_ratio` are mutually exclusive, and their
  union is `is_ratio_symbol`.
- `compute_ratio_candles` returns bars only for timestamps in both legs; every
  returned bar satisfies `high >= max(open, close)` and `low <= min(open,
  close)`, has `volume == 0`, and a strictly-positive denominator at that bar.
- `compute_scaled_candles` returns EXACTLY as many bars as its input, with
  `volume` and `session` preserved and each OHLC component exactly `x / k`.
- `fetch_ratio(non_ratio, ...) is None` and does NOT invoke `leg_fetcher`.
- `fetch_ratio` on an unsupported numeric shape (`100/VIX`, `16/4`, `^VIX/0`)
  returns `None` without invoking `leg_fetcher` at all.
- A scaled symbol invokes `leg_fetcher` exactly ONCE (the constant is never
  fetched).
- Both legs of a quotient are fetched at the SAME `interval` from the SAME
  source.

## Testing
- `tests/unit/data/test_ratio_source.py` — parse (case/whitespace/unknown/None),
  registry shape, compute (component quotient, envelope validity, inner-join,
  non-positive-denominator skip, session carry, empty/non-overlapping legs),
  `fetch_ratio` (happy path, non-ratio short-circuit, either-leg None/empty,
  interval pass-through), and the `fetch_live_data` routing hook.
- `tests/unit/data/test_ratio_scaled.py` — the scale-constant grammar and its
  rejections, shape classification, `base_symbol_of`, display label, scalar
  compute (exactness, no bar loss, volume preserved, no envelope widening,
  guards), and fetch routing (single-leg fetch, unsupported shapes never
  fetching, quotient path unaffected).
- `tests/unit/gui/test_ratio_render_modes.py` — volume-pane fork (hidden for
  quotients, shown for scaled) and rebase disabled for scaled symbols.

## Known limitations
- **Quotient high/low is an approximation** — see Design Decisions. Do not
  treat a quotient bar's high/low as a price the ratio actually traded at
  intra-bar. A SCALED bar's high/low is exact.
- **Both legs of a quotient must come from the same source at the same
  interval.** A ratio of two different vendors, or two different intervals, is
  not supported.
- **Quotient inner-join silently drops non-overlapping bars.** Halts,
  differing histories and index-vs-ETF session differences compress the series
  with no warning. (Not applicable to scaled symbols.)
- **No inverse (`100/VIX`) and no multiplication.** See Design Decisions.
- **Quotients have no volume and no events**; scaled symbols keep both (events
  resolve to the underlying — see `gui/events_app.spec.md`).
- **Live streaming is not scaled.** Scaled symbols use the same full-render,
  no-live-fast-path treatment as quotients; streaming one leg × a constant is
  future work.
- **Daily adjustment inherited from the legs** — since legs are fetched via the
  normal source, split/dividend adjustment follows that source's policy
  (yfinance auto-adjusts). A ratio of two adjusted series is itself consistent.
  A constant divisor calibrated against an unadjusted price (e.g. `SPX/10 ≈
  SPY`) drifts as the numerator is adjusted; it is a visual alignment, never an
  executable price.

