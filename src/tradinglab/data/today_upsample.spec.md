# `data/today_upsample.py` — Spec

## Purpose

Synthesise today's running daily bar from cached intraday data so the
1d chart shows the in-progress session instead of "everything up to
yesterday" mid-day. Audit tag `daily-today-upsample`.

## Public API

- `SUPPORTED_INTERVALS: frozenset[str]` — `{"1d"}`. Daily-class
  intervals that get synthesised. 1wk / 1mo not supported yet (would
  need week-to-date / month-to-date aggregation over a mix of cached
  daily bars + intraday).
- `synthesize_today_daily_candle(intraday_candles, *, today_et=None, sessions=frozenset({"regular"}))`
  — `Candle | None`. OHLCV aggregation: O = first match's open;
  H = max(highs); L = min(lows); C = last match's close; V = sum(vols).
  Preserves first match's timestamp as the synth bar's `date` so
  event/hover lookups still resolve a real bar. Returns `None` when
  no intraday bars match today's ET date for the requested sessions.
- `find_best_intraday_source(full_cache, *, source, symbol)` —
  `list[Candle] | None`. Iterates `("1m","2m","5m","15m","30m","1h")`
  finest-first; returns the first non-empty cache entry. `None` when
  no intraday cache exists for the symbol — caller should schedule a
  5m prefetch and re-render on arrival.
- `upsample_daily_with_today(daily_candles, *, intraday_candles, today_et=None)`
  — `list[Candle]`. Append-or-overwrite: when the daily series' last
  bar's ET date matches today's, overwrites with the freshly
  synthesised bar; otherwise appends. Always returns a fresh list (no
  mutation of inputs).

## Dependencies

- Internal: `..models.Candle`.
- External: `zoneinfo` (optional — lazy import, hard fallback to
  fixed -5h UTC offset when tzdata missing).

## Design decisions

### Resolution preference (1m > 2m > 5m > … > 1h)

Always pick the finest cached intraday interval. The user's preferred
intraday view is the one they polled most recently; falling back to a
coarser interval is acceptable when the finer one isn't warm.

### ET date assignment

Tz-aware intraday timestamps convert to `America/New_York` via
`zoneinfo` (codebase convention — see `app.spec.md` §"Cache staleness"
for the same pattern). Tz-naive timestamps are treated as already-in-ET
(matches `models.spec.md`). The fallback path on missing tzdata is a
fixed `-5h` UTC offset; off-by-one for ~10% of the year (EDT vs EST)
but only matters across midnight ET which RTH never straddles.

### Regular session by default

The default `sessions=frozenset({"regular"})` matches discretionary-
trader expectation: a daily candle aggregates regular-hours OHLCV, not
extended hours. Callers wanting extended-hours synthesis can pass an
explicit `sessions={"pre", "regular", "post"}`.

### Append vs overwrite

- The provider's lagged daily series ends at yesterday → append.
- A provider that DOES emit a partial today bar → overwrite, because
  our intraday-derived OHLCV is fresher than whatever the provider's
  partial-day rollup reports.

### Cache hygiene

Always returns a fresh `list[Candle]`. Callers wire the upsampled list
into the data controller's `primary_raw` / `compare_raw`; the original
`_full_cache` entries stay truthful so a subsequent provider fetch
that finally includes today simply lands on top of stale state and
the synth round-trips cleanly.

## Invariants

- `synthesize_today_daily_candle([])` → `None`.
- The synth bar's `session` is always `"regular"`.
- The synth bar's `date` equals the first matched intraday bar's date
  (never fabricated).
- `upsample_daily_with_today(daily, intraday=None)` returns a copy of
  `daily` (never mutates input).

## Out of scope

- 1wk / 1mo synthesis (week / month-to-date roll-up over mixed daily
  + intraday cache).
- Holiday calendar (a market-closed weekday won't have intraday bars,
  so the synth path naturally returns `None`).
- Source-aware filtering (the cache key already includes `source`;
  same symbol on different sources upsamples independently).
