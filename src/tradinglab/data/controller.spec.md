# data/controller.py — Spec

## Purpose
Own the app's in-memory candle cache plus the active primary/compare candle lists. This isolates cache staleness, LRU trimming, worker stash handling, pair filtering, and fetch-token bookkeeping from `ChartApp`.

## Public API
- `DataController(full_cache_size=50)` — create an empty cache/state holder.
- `primary` / `compare` / `primary_raw` / `compare_raw` — read-only accessors for the active lists.
- `bump_token()` / `token` — monotonic fetch-token gate for stale async completions.
- `get(key, *, touch=False)` — read a cached candle list, optionally refreshing LRU order.
- `stash(key, bars, *, pinned_tickers=frozenset(), now_s=None, session_open=None)` — worker-to-cache sink with stale-overwrite protection and protected-key trim.
- `is_stale(candles, interval, *, now_s=None, session_open=None)` — session-aware freshness test for sealed bars.
- `trim(pinned_tickers=frozenset(), *, protected_key=None)` — soft-cap the cache while preserving pinned tickers.
- `disk_load(source, ticker, interval)` — defensive wrapper around `disk_cache.load`.
- `set_primary(raw, filtered, *, compare_raw=None, compare_filtered=None)` — replace active raw/visible lists.
- `apply_pair_filter(primary_raw, compare_raw, *, interval, prepost)` — delegate to `core.pairing.apply_pair_filter_and_align`.

## Dependencies
- Internal: `disk_cache`, `constants`, `core.pairing`, `models.Candle`.
- External: `collections.OrderedDict`, `time`.

## Design Decisions
- `ChartApp` keeps legacy attributes (`_full_cache`, `_primary`, `candles`, etc.) as direct aliases to controller-owned objects for backward compatibility.
- Intraday freshness accepts `session_open` from the caller so the controller stays Tk-free and does not reach into app/session state.
- Trim keeps pinned tickers warm and supports a `protected_key` so a just-stashed preload is not evicted immediately.
- Visible candle lists preserve identity when supplied by callers; sandbox/replay paths rely on that for in-place updates.

## Invariants
- `_full_cache` is keyed by `(source, ticker, interval)`.
- `primary`/`compare` and `primary_raw`/`compare_raw` are always lists.
- `bump_token()` is monotonic for the controller lifetime.
- `trim()` never evicts pinned entries when all over-cap entries are pinned.

## Testing
- Covered indirectly by the existing `ChartApp` unit suite, which exercises cache hits/misses, compare-mode alignment, sandbox installs, preload paths, and async token gating through the app boundary.

