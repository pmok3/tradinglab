# data/prefetch/appglue.py — Spec

## Purpose
Pure, Tk-free helpers for the (flagged) ChartApp integration, keeping the
app-coupled surface thin + testable.

## Public API
- `scheduler_enabled() -> bool` — reads `TRADINGLAB_PREFETCH_SCHEDULER`; False
  for `{0,off,false,no}` (case-insensitive), True otherwise (including unset).
- `scheduler_mode() -> str` — `"shadow"` iff the flag is exactly `shadow`, else
  `"live"` (cut-over default whenever enabled).
- `bucket_registry_for_mode(mode) -> SourceBucketRegistry` — `live` shares the
  process-wide `global_bucket_registry()` (the one real accounting gate);
  every other mode (`shadow`) gets a throwaway `unlimited_bucket_registry()` so
  dry-run planning never spends a real vendor token (review Must-fix).
- `partition_watchlists(active_name, pinned_names, tickers_of) -> (focused,
  other)` — focused = active sub-tab's tickers (if pinned); other = the rest of
  the pinned lists minus focused. Normalized (`strip().upper()`) + deduped,
  order preserved.
- `build_context(*, source, active_symbol, active_interval, compare_symbol="",
  focused_watchlist=(), other_watchlists=(), universe=()) -> PrefetchContext` —
  normalizes symbols + dedupes the tier tuples.

## Contract
- Feature flag defaults ON in live mode (env unset) → the scheduler is
  constructed and drives real prefetches. `off` / `0` / `false` / `no` remains
  the kill-switch; `shadow` → observe-only (Decision 6 revised; a Settings
  toggle is layered later, Decision 13).
- Symbol normalization is uniform `strip().upper()`; blanks dropped.

## Testing
`tests/unit/data/prefetch/test_appglue.py` — flag enabled/disabled values, mode
shadow/live, `bucket_registry_for_mode` (live shares global; shadow gets a
separate unlimited registry; a shadow driver `pump` consumes ZERO tokens from a
capacity-1 global bucket), watchlist partition (focused+other,
focused-not-pinned, empty, normalize+dedupe), build_context normalization +
blank compare.
