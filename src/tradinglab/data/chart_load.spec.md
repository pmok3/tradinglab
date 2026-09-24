# data/chart_load.py — Spec

Last updated: 2026-09-24

## Purpose
Own the headless chart-loading transaction shared by interactive, synchronous,
and polling entry points. Capture selection and provider once, return named
worker results, and publish primary/compare state only for the current request.
Tk delivery, controls, status messages and rendering remain in `ChartApp`.

## Public API
- `ChartSelection(source, ticker, interval, compare_on=False, compare_ticker="", prepost=False)`:
  immutable selection and cache keys; callers supply normalized symbols.
- `ChartLoadRequest`: selection, shared fetch generation, stream-history fence,
  worker-merge policy and captured provider callable.
- `FetchedSide`: provider bars, preloaded disk bars and optional premerged bars.
- `ChartLoadResult`: request plus named primary/compare results and preload flags.
- `ChartLoadCompletion`: application outcome, failure/reconciliation flags,
  cache-only classification, view-switch completion and indicator invalidation hints.
- `ChartLoadCoordinator(data, streams, view, *, is_stale, store=disk_cache)`:
  receives existing controllers and narrow cache-policy/storage dependencies.
- `begin(selection, *, refresh=False)`: supersede the prior request and capture
  provider/fence; polling evicts requested cache keys only without a subscription.
- `cache_hit(request)`: test the existing memory freshness policy.
- `fetch(request)`: worker-side provider/disk work; never publishes chart state.
- `complete(request, result=None, *, current, pinned_tickers=())`: reject stale
  work or resolve and publish both sides. No result means synchronous resolution;
  an empty result means a completed empty/failed fetch, not another network attempt.
- `is_current(request, current=None)` / `pending`: inspect request ownership.
- `close()`: permanently reject publication and new requests, advancing the
  shared generation. Executor shutdown remains `FetchService`'s responsibility.

## Dependencies
- Internal: `data.controller`, `data.base`, `data.stream_controller`,
  `data.today_upsample`, `core.view_intent`, `disk_cache`, `models`.
- External: standard-library dataclasses, typing and logging.

## Design Decisions
- **One publication owner.** The coordinator writes raw cache entries, derives
  visible series and publishes through `DataController.set_primary`. It never
  receives `ChartApp`, widgets, axes or an executor.
- **Existing policies remain.** Registry selection, source-keyed caches,
  provider-first fetching, stale-memory-before-disk fallback, primary rejection
  and primary-only publication on compare failure are unchanged.
- **Shared generation.** Replay cutover and stream takeover can still invalidate
  a request through `DataController.token`. Identity also rejects duplicate
  delivery; selection equality rejects edits that precede their debounced load.
- **Preserve disk timing.** Interactive workers preload, merge and save using
  `merge_adds_nothing`. Polling and subscribed-stream workers only preload disk;
  acceptance reconciles stream history before merging or saving. Superseded
  interactive workers may finish warming disk, but cannot publish a chart.
- **No deep copies.** Typed envelopes do not make their candle lists immutable.
  Existing cache/list-copy and shared-Candle conventions remain; replay bypasses
  this coordinator entirely and retains its identity-stable visible lists.
- **Explicit failures.** Provider, disk-read and worker merge/save failures log
  exceptions before following the existing fallback policy. Storage/processing
  errors during publication propagate to the UI delivery boundary.
- **Separate presentation.** Only accepted completion releases a source-switch
  hold. The adapter decides synchronous versus deferred rendering and invalidates
  previous indicator entries when worker-supplied bars replace visible lists.

## Invariants
- A stale generation, mismatched selection, closed loader or duplicate completion
  cannot change active series, persist completion data or consume view intent.
- A result for another request raises `ValueError`.
- `fetch` uses the captured provider, never Tk variables or mutable chart state.
- Fresh memory bypasses provider I/O on synchronous and interactive cache-hit paths.
- Stream reconciliation precedes persistence; cached fallback cannot discharge
  history debt as though it were fresh provider data.
- Cache entries retain provider data before daily synthesis and pre/post filtering.
- Primary and compare are derived using one selection before active-state publication.

## Data Flow / Algorithm
`begin -> cache probe or fetch -> complete -> render adapter`.
Completion checks ownership, resolves fresh memory/provider/fallback, reconciles
stream history, merges/persists raw bars, synthesizes daily context, filters and
aligns the pair, publishes the data state, then acknowledges fresh stream history.

## Testing
- `tests/unit/data/test_chart_load.py`: headless selection/generation/close gates,
  fallback, cache hits, captured providers, disk timing, pair publication and filtering.
- `tests/unit/test_load_data_prefetch_indicator_refresh.py`: real UI completion,
  indicator invalidation and request lifecycle.
- `tests/unit/gui/test_stream_reconciliation_polling.py`: real polling adapter,
  delayed futures, stream coverage debt and persistence before takeover.
- Smoke `check_d10_poll_tick_offloads_fetch_to_executor` and
  `check_d30_drilldown_ylim_no_deferred_render_race`: GUI wiring and synchronous
  drilldown/cache behavior.

## Known limitations / Future work
- Provider error taxonomy and provenance-aware cache identities are separate work.
- Cancellation rejects publication; it does not interrupt running provider calls.
- Daily synthesis and source-specific freshness retain their existing clocks.
- Compare toggles without a pending load remain cache-only presentation operations.

## Recent history
- 2026-09-24: extract chart-load ownership from `ChartApp` and polling; remove
  positional completion tuples and the mutable `_prefetched_raw` handoff.
