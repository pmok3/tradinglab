# streaming/intraday.py — Spec

Last updated: 2026-09-07

## Purpose
Pure main-chart adapter from native 1m events to safely covered intraday buckets.
No history fetching, socket, worker, full chart cache, or Tk ownership.

## Public API
- `CHART_INTERVALS`: `5m`, `15m`, `30m`, `1h`.
- `IntradayAdapter(interval, *, history=(), seed=())`.
- `apply(kind, bar) -> list[tuple[str, Candle]]`: tick/rollover/authoritative
  closed minute to bounded target updates.
- `reconcile(history=..., seed=...)`: reset after a completed history fetch or
  connection epoch change. `ready`, `needs_reconcile`, `message` expose state.
- `observe_history(history)`: protect newly polled opaque aggregates without
  losing existing minute warm-up.

## Dependencies
- Internal: `models.Candle`, `core.timezones.ET`, `streaming.resampler`.
- External: stdlib only.

## Design Decisions
- **Safe warm-up.** Polling remains in charge until emitted aggregates cover
  every minute from the target boundary. The first forming subscription minute
  is unsafe until an explicit `closed` correction; local clock never proves it.
  An existing REST bucket carries no covered-through metadata, so replacing it
  additionally requires full bucket coverage, not only the observed prefix.
- **Cached seeds only.** Use bounded copies of existing same-provider 1m history,
  excluding its newest possibly-forming minute. Never fetch a second history
  series. Existing target history must share the session-open bucket anchor.
- **Exchange alignment.** Convert aware times to ET. Legacy naive times mean ET
  wall-clock, matching the resampler's legacy exchange-local convention.
  Shared pre/regular/post boundaries clip hourly buckets at session end.
- **Shared reducer.** The opt-in two-bucket BarResampler owns contributions and
  OHLCV math. No parallel full Candle history.
- **Real fallback.** A missing minute or unavailable historical correction sets
  `needs_reconcile`; callers must perform an actual history refresh and continue
  polling. Merely logging is insufficient.

## Invariants
- Startup partial data never overwrites a complete historical bucket.
- No fabricated gap minutes; no readiness on missing prefix coverage.
- Reconciliation resets readiness and subscription-minute safety.
- Authoritative source minutes cannot be downgraded by provisional updates.
- Retained contributions are bounded by two target intervals.

## Testing
`tests/streaming/test_intraday.py`: interval matrix, startup correction,
cached seeds, gap/out-of-retention fallback, duplicate corrections and reset.

## Known limitations / Future work
First activation without safe cached history can wait until a subsequent full
bucket. Only the four named target intervals are integrated; daily and other
surfaces remain outside this adapter. Live feed commissioning is still required.

## Recent history
- 2026-09-07: bounded keyless chart integration.
