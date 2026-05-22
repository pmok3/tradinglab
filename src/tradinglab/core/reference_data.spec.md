# core/reference_data.py — Spec

## Purpose
Cross-symbol reference-data registry for indicators that need OHLCV for a *second* symbol (e.g. RRVOL divides the primary's RVOL by SPY's RVOL of the same flavor). Bridges the synchronous indicator-compute path with the app's async fetch machinery.

## Public API
- `ProviderFn = Callable[[str, str, str], None]` — `(source, symbol, interval) -> None`. Schedules the fetch; provider is responsible for calling `set_reference_bars()` on completion.
- `OnArrivalFn = Callable[[], None]` — invoked once data arrives; typical implementation clears the indicator cache and re-renders.
- `set_provider(provider: Optional[ProviderFn], *, on_arrival: Optional[OnArrivalFn] = None) -> None` — install or clear. `provider=None` disables auto-fetch (tests inject results directly).
- `generation() -> int` — monotonic counter bumped on every cache mutation. Useful as part of an indicator-cache key.
- `get_reference_bars(source, symbol, interval) -> Optional[Bars]` — cache-only synchronous read. On miss, schedules a background fetch (deduped via `_inflight`) but never blocks; returns `None`.
- `set_reference_bars(source, symbol, interval, bars: Bars) -> None` — populate the cache, bump the generation counter, invoke the on-arrival callback.
- `mark_fetch_failed(source, symbol, interval) -> None` — release the in-flight slot without populating the cache, so a future read can retry.
- `clear() -> None` — reset all module state. Tests only.

## Dependencies
- Internal: `.bars.Bars`.
- External: stdlib only (`threading`, `typing`).

## Design Decisions
- **App-scoped singleton state at module level**: only one `ChartApp` per process in production; tests reset via `clear()`. Singleton is the simplest correct shape.
- **Source-aware cache key `(source.lower(), symbol.upper(), interval)`**: switching data sources (yfinance ↔ synthetic ↔ schwab) MUST NOT reuse bars from the prior source — different timestamp conventions / history depths. `_norm` enforces case normalisation.
- **Generation counter** lets callers integrate cache freshness into their own keys. Either the counter OR the on-arrival callback is sufficient; both are exposed for flexibility.
- **Synchronous read path**: indicators call `get_reference_bars` from inside `compute_arr`. Cache hit → immediate `Bars`. Miss → schedule fetch (deduped), return `None`; indicator emits all-NaN for this render and the on-arrival callback triggers a re-render once data arrives.
- **Provider runs without the lock held**: registry acquires `_lock` only long enough to mark `_inflight`, then releases before invoking `provider(*key)`. Avoids long-held locks blocking the indicator path.
- **Failed-provider hygiene**: a provider that raises has its in-flight slot released so a future read retries. `set_reference_bars` is the success path; `mark_fetch_failed` is the explicit failure path.
- **On-arrival callback exceptions are swallowed**: a misbehaving callback must not corrupt cache state.

## Invariants
- `generation()` is monotonically non-decreasing across the process lifetime (until `clear()`).
- `get_reference_bars` never blocks; never raises.
- `set_reference_bars(source, symbol, interval, bars)` is atomic (single lock acquisition); after the call returns, a concurrent `get_reference_bars` for the same key returns the new bars.
- A `set_reference_bars(..., bars=None)` call is a no-op (defensive).

## Testing
- Covered indirectly via integration smoke tests (RRVOL indicator). Standalone unit coverage is straightforward: `set_provider` + `get/set` + `generation` round-trip.

