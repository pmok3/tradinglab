# data/prefetch/appglue.py — Spec

## Purpose
Pure, Tk-free helpers for the (flagged) ChartApp integration, keeping the
app-coupled surface thin + testable.

## Public API
- `scheduler_enabled() -> bool` — reads `TRADINGLAB_PREFETCH_SCHEDULER`; True for
  `{1,on,true,yes,shadow,live}` (case-insensitive), else False (default).
- `scheduler_mode() -> str` — `"live"` iff the flag is exactly `live`, else
  `"shadow"` (safe default whenever enabled).
- `partition_watchlists(active_name, pinned_names, tickers_of) -> (focused,
  other)` — focused = active sub-tab's tickers (if pinned); other = the rest of
  the pinned lists minus focused. Normalized (`strip().upper()`) + deduped,
  order preserved.
- `build_context(*, source, active_symbol, active_interval, compare_symbol="",
  focused_watchlist=(), other_watchlists=(), universe=()) -> PrefetchContext` —
  normalizes symbols + dedupes the tier tuples.

## Contract
- Feature flag defaults OFF (env unset) → the scheduler is never constructed →
  zero behavior change. `shadow` (or any truthy) → observe-only; `live` → drive
  fetches (Decision 6 revised; a Settings toggle is layered later, Decision 13).
- Symbol normalization is uniform `strip().upper()`; blanks dropped.

## Testing
`tests/unit/data/prefetch/test_appglue.py` — flag enabled/disabled values, mode
shadow/live, watchlist partition (focused+other, focused-not-pinned, empty,
normalize+dedupe), build_context normalization + blank compare.
