# data/fetch_service.py — Spec

## Purpose
Owns TradingLab's general worker pool, dedicated foreground fetch pool, and the background prefetch/reference-data orchestration that used to live in `ChartApp`.

## Public API
- `class FetchService`
  - `FetchService(worker_count=4)` — creates the shared worker executors plus fetch-related state.
  - `prefetch(...) -> Future | None` — submit a background cache warm-up for `(source, ticker, interval)`.
  - `apply_prefetch_result(...) -> None` — Tk-thread merge/apply step for a finished prefetch.
  - `prefetch_compare(...) -> None` — normalize compare symbol and delegate to a prefetch callable.
  - `prefetch_companion_intervals(...) -> None` — warm adjacent intervals for primary/compare symbols.
  - `fetch_reference(...) -> None` — background provider used by `core.reference_data` (RRVOL/SPY path).
  - `on_reference_data_arrived(...) -> None` — queue a worker-inbox marker instead of touching Tk from a worker thread.
  - `await_future_on_tk(...) -> None` — poll a `Future` from the Tk main loop.
  - `shutdown() -> None` — cancel and tear down both thread pools.

## State
Owns:
- `_executor`
- `_fetch_executor`
- `_prefetch_inflight`
- `_prefetch_futures`
- `_poll_job`
- `_reload_job`
- `_poll_retry_count`
- `_poll_retry_expected_min_ts`

## Dependencies
- Internal: `tradinglab.disk_cache`, `tradinglab.core.reference_data`, `tradinglab.core.bars.Bars`, `tradinglab.data.base.DATA_SOURCES`, `tradinglab.models.Candle`.
- External: `concurrent.futures`.

## Design Decisions
- **No `ChartApp` import**: the service accepts callbacks/mappings (`stash_fn`, worker-inbox callback, cache map) so it stays reusable and avoids a circular import.
- **Two-stage prefetch apply**: the network fetch runs on the worker pool, but the final merge/apply step is re-entered through the app's worker inbox so Tk-thread cache mutations stay centralized.
- **Legacy attribute compatibility**: `ChartApp` keeps exposing `_executor`, `_fetch_executor`, `_prefetch_inflight`, `_prefetch_futures`, `_poll_job`, `_reload_job`, `_poll_retry_count`, and `_poll_retry_expected_min_ts`, but those are now backed by this service.
- **Reference-data path stays async**: RRVOL-style secondary-symbol requests still use the general worker pool and trigger redraw through `core.reference_data`'s arrival callback.
- **No-op prefetch writes are skipped, and the disk re-read is avoided**:
  the in-memory cache is *disk-authoritative* (`_load_data_async` saves the
  merged result to disk before notifying the Tk thread), so
  `apply_prefetch_result` reuses the `full_cache` entry as the merge base
  instead of re-reading + re-parsing the JSONL (~26ms on an 11k-bar file,
  on the Tk thread). It only falls back to `disk_cache.load` when the key
  was never loaded into memory (a watchlist prefetch for a never-viewed
  ticker). The save is then gated by a cheap length+last-bar proxy
  (`_candles_extended_or_updated`) rather than an O(N) `list.__eq__`.

## Invariants
- Prefetches are deduped by `(source, ticker, interval)` and capped by the caller-provided inflight limit.
- Empty/failed prefetches clear their inflight slot.
- A prefetch that adds no new bars to the disk-authoritative in-memory
  cache must not rewrite the JSONL file (gated by the cheap
  length+last-bar proxy, not an O(N) list comparison).
- When the prefetched key is present in `full_cache`, the on-disk JSONL
  is NOT re-read (the in-memory copy is authoritative); a disk read only
  happens for a key absent from memory.
- `shutdown()` leaves both executors unusable and clears fetch-related bookkeeping.
- `await_future_on_tk()` never uses `Future.add_done_callback()` to call Tk APIs from a worker thread.

## Testing
- Covered by existing smoke/unit paths that exercise compare warming, drilldown prefetch reuse, poll-tick async fetches, reference-data redraws, and close-time executor shutdown.
