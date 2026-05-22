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

## Invariants
- Prefetches are deduped by `(source, ticker, interval)` and capped by the caller-provided inflight limit.
- Empty/failed prefetches clear their inflight slot.
- `shutdown()` leaves both executors unusable and clears fetch-related bookkeeping.
- `await_future_on_tk()` never uses `Future.add_done_callback()` to call Tk APIs from a worker thread.

## Testing
- Covered by existing smoke/unit paths that exercise compare warming, drilldown prefetch reuse, poll-tick async fetches, reference-data redraws, and close-time executor shutdown.

