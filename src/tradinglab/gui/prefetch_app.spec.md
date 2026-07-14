# gui/prefetch_app.py — Spec

## Purpose
`PrefetchAppMixin` — the ChartApp glue for the flagged background prefetch
scheduler (`data/prefetch/*`), extracted from `app.py` to keep it under its LOC
ceiling (§7.24). A pure method-bag mixin: **no `__init__`**.

## Public surface (methods on ChartApp)
- `_maybe_build_prefetch_driver()` — construct a `PrefetchDriver` iff
  `scheduler_enabled()`, else `None` (called once from `ChartApp.__init__`).
- `_build_prefetch_driver()` — `PrefetchScheduler(standard_tiers(),
  buckets=bucket_registry_for_mode(mode), supports_range=source_supports_range)`
  + `PrefetchDriver(shadow=mode != "live")`.
- `_prefetch_submit(job)` / `_prefetch_apply(job, bars, memory_allowed)` —
  live-mode seams (no-ops until the cut-over).
- `_build_prefetch_context()` — snapshot source/ticker/interval/compare +
  `partition_watchlists(active sub-tab, pinned)` (universe deferred) into a
  `PrefetchContext` (or `None`).
- `_prefetch_observe(changed_ranks=None)` — no-op when the driver is `None`;
  else `set_context` + `pump`; in shadow mode logs the planned-job count with no
  fetch/cache side effects.

## Contract
- Gated by `TRADINGLAB_PREFETCH_SCHEDULER` (default OFF → `_prefetch_driver is
  None` → zero behaviour change). Reads only `self.<attr>` state owned by
  `ChartApp` (`source_var`/`ticker_var`/`interval_var`/`compare_ticker_var`/
  `watchlist_var`/`_watchlists`/`_prefetch_driver`).
- The driver's bucket registry depends on the mode (`bucket_registry_for_mode`):
  **live** shares the process-wide `global_bucket_registry()` — the same
  per-source `TokenBucket` the Alpaca fetch path uses (Decision 1); **shadow**
  gets a throwaway `unlimited_bucket_registry()` so dry-run observation never
  spends a real vendor token (review Must-fix).
- Wired into `ChartApp.__init__` (`self._prefetch_driver =
  self._maybe_build_prefetch_driver()`) and `_on_explicit_axis_change`
  (`self._prefetch_observe()`).

## Testing
Covered via the flag-on shadow-boot path + the full smoke suite with the flag
OFF (zero regression). The pure helpers it composes are unit-tested under
`tests/unit/data/prefetch/`.
