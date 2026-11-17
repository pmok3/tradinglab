# core/bars_registry.py — spec

Last updated: 2026-11-17

## Purpose
Shared `(symbol, interval) → (BarsBuffer, IndicatorMemo)` registry. Layer 0 of the exit-strategies design — sits on top of `MultiIntervalCache` (which owns buffers) and adds a memo lifecycle keyed by the same tuple. `ScanRunner`, live entry/exit evaluators, and the strategy tester use one memo per `(symbol, interval)` per tick — e.g. a 5m EMA(50) computed for a scan is reused by an exit trigger on the same bars.

Named `BarsRegistry` (not `IndicatorCacheRegistry`) to avoid collision with the unrelated `IndicatorCache` in `indicators/cache.py`.

## Public types
- `BarsView(bars: Bars, memo: IndicatorMemo, fingerprint: tuple, buffer: BarsBuffer)` — frozen container from `get_view`. `bars`/`memo` are work handles; `fingerprint`/`buffer` are test/diagnostic seams.

## Public API
- `BarsRegistry(multi_interval_cache: MultiIntervalCache)` — the cache is the source of truth for buffers; consulted on every `get_view`.
- `get_view(symbol, interval) -> Optional[BarsView]`:
  - Pull buffer from cache. If `None` (lazy-load pending), return `None`.
  - Compute fingerprint from the cache's parallel candle list.
  - Reuse cached `IndicatorMemo` on identical fingerprint; rebuild on any change.
  - Bind freshly snapshotted `Bars` view onto the memo (so indicator computes share it via `compute_via_bars`).
- `invalidate(symbol, interval=None)` — drop memo(s). `interval=None` drops all for the symbol. Buffers untouched.
- `clear()` — drop every memo and fingerprint. Counters preserved.
- `stats() -> Dict[str, int]` — `views_built` / `memos_reused` / `memos_rebuilt`.

## Fingerprint
Same shape as `scanner.runner._Fingerprint`: `(id_of_list, n, last_ts_ns, last_open, last_high, last_low, last_close, last_volume)`. Detects every meaningful same-length tick change — including forming-bar updates that move volume/high/low without affecting close (RVOL, ATR, key-bar all care). Empty lists fingerprint to all-zeros.

## Scope
- Does NOT own buffers — `MultiIntervalCache` does.
- Does NOT handle stale-eviction or history backfill — cache's job.
- No locking: single-writer (typically GUI thread) on `get_view`; underlying cache takes its own `RLock`.
- **Bounded LRU memo maps (2026-09-20)**: `_memos` and `_fingerprints` are `core.lru_dict.LRUDict`s capped at `_MEMO_CACHE_MAX_SIZE` = 512 `(symbol, interval)` pairs. This is a defensive bound for callers retaining a registry across many keys, not evidence of a process-lifetime leak: the strategy tester currently constructs its dependency registry per `evaluate_symbol` call. `get_view` touches both maps in the same order, so hot pairs survive and the maps evict in lockstep. An evicted memo rebuilds lazily from the cache's current candles on its next `get_view`. Fingerprint changes still rebuild resident memos, including forming-bar volume changes at full capacity. The bound does not evict underlying `MultiIntervalCache` buffers or bound individual memo sizes. No API or under-cap behavior change.

## Testing
- `tests/unit/test_bars_registry.py` covers memo reuse/invalidation, paired-map capacity and ordering, hot-entry retention, indicator recomputation after eviction, and forming-volume invalidation at full capacity.

## See also
- [scanner/engine](../scanner/engine.spec.md), [scanner/runner](../scanner/runner.spec.md), [data/multi_interval_cache](../data/multi_interval_cache.spec.md).
