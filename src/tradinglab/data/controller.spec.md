# data/controller.py — Spec

## Purpose
Own the app's in-memory candle cache plus the active primary/compare candle lists. This isolates cache staleness, LRU trimming, worker stash handling, pair filtering, and fetch-token bookkeeping from `ChartApp`.

## Public API
- `DataController(full_cache_size=50)` — create an empty cache/state holder.
- `primary` / `compare` / `primary_raw` / `compare_raw` — read-only accessors for the active lists.
- `bump_token()` / `token` — monotonic fetch-token gate for stale async completions.
- `get(key, *, touch=False)` — read a cached candle list, optionally refreshing LRU order.
- `stash(key, bars, *, pinned_tickers=frozenset(), now_s=None, session_open=None)` — worker-to-cache sink with stale-overwrite protection and protected-key trim.
- `is_stale(candles, interval, *, now_s=None, session_open=None)` — session-aware freshness test for sealed bars. Intraday: stale when the session is open and the last bar is older than `2×interval`. Daily/weekly/monthly: stale when the last bar is older than `2×interval`. **Gap-aware (1d only):** additionally stale when a recent interior weekday is missing between two present daily bars (a hole left by a dropped NaN-OHLC poison bar) — reported stale ONCE per unique gap per controller session so a single re-fetch + merge can fill it without looping on genuine holidays.
- `trim(pinned_tickers=frozenset(), *, protected_key=None)` — soft-cap the cache while preserving pinned tickers.
- `disk_load(source, ticker, interval)` — defensive wrapper around `disk_cache.load`.
- `set_primary(raw, filtered, *, compare_raw=None, compare_filtered=None)` — replace active raw/visible lists.
- `apply_pair_filter(primary_raw, compare_raw, *, interval, prepost, keep_window=None)` — delegate to `core.pairing.apply_pair_filter_and_align`; `keep_window=(lo_ts, hi_ts)` is forwarded verbatim to retain an old on-screen primary window the compare doesn't cover yet (see `core/pairing.spec.md`, audit `compare-toggle-drilldown-preserve`).

## Dependencies
- Internal: `disk_cache`, `constants`, `core.pairing`, `models.Candle`.
- External: `collections.OrderedDict`, `time`.

## Design Decisions
- `ChartApp` keeps legacy attributes (`_full_cache`, `_primary`, `candles`, etc.) as direct aliases to controller-owned objects for backward compatibility.
- Intraday freshness accepts `session_open` from the caller so the controller stays Tk-free and does not reach into app/session state.
- Trim keeps pinned tickers warm and supports a `protected_key` so a just-stashed preload is not evicted immediately.
- Visible candle lists preserve identity when supplied by callers; sandbox/replay paths rely on that for in-place updates.
- **Gap-aware daily staleness.** The last-bar age check is blind to an *interior* missing weekday — a `Mon, Wed` daily series with `Tue` absent looks fresh. Such holes come from dropped NaN-OHLC poison bars (see `disk_cache.spec.md`). `is_stale` scans the trailing `_DAILY_GAP_WINDOW` (8) bars for weekday holes (`_recent_interior_gap_dates`, weekends excluded) and forces a one-time re-fetch. The `_stale_gap_seen` guard keys on `(interval, gap-date-tuple)` so a permanent gap (a genuine market holiday the heuristic can't distinguish) is reported at most once per session — a single no-op merge, never a loop. Scoped to `1d`; weekly/monthly cadence is not a business-day sequence. `is_stale` mutating `_stale_gap_seen` is a deliberate, documented side effect; all call sites run on the Tk thread (single-threaded, like the rest of the controller cache).

## Invariants
- `_full_cache` is keyed by `(source, ticker, interval)`.
- `primary`/`compare` and `primary_raw`/`compare_raw` are always lists.
- `bump_token()` is monotonic for the controller lifetime.
- `trim()` never evicts pinned entries when all over-cap entries are pinned.
- The gap-aware branch of `is_stale` reports any given `(interval, gap-dates)` signature stale at most once per controller instance.

## Testing
- `tests/unit/data/test_controller_gap_staleness.py` — gap-aware `is_stale` (interior weekday gap flags stale once, weekends are not gaps, filled gap clears, 1d-only scope, age-stale precedence).
- Otherwise covered indirectly by the existing `ChartApp` unit suite, which exercises cache hits/misses, compare-mode alignment, sandbox installs, preload paths, and async token gating through the app boundary.

