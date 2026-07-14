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
  + `PrefetchDriver(submit=_prefetch_submit, apply_result=None,
  shadow=mode != "live")`. **`apply_result=None`** — the app owns ALL cache
  writes (worker-side merge), so `driver.complete` never double-applies.
- `_prefetch_submit(job)` — live-mode async fetch (never called in shadow).
  `window_for(job)` → build the fetch (via `data.prefetch.live.fetch_window`:
  range→`fetch_page`, period→trailing) and run it + the disk merge/save on the
  DEDICATED prefetch pool (`fetch_svc.submit_prefetch`, `apply_prefetch_result(
  memory_allowed=False, stale_guard=band<=0)` — worker-side, NOT Tk, per review
  Must-fix). The Tk callback (`_await_future_on_tk`) stashes the merged series
  into the in-memory working set ONLY when `cache_policy_for(job) ==
  CACHE_MEMORY_AND_DISK`, calls `driver.complete(bars_count=…, oldest_ts=…,
  error=…, retry_after_s=…)` (only the count is marshalled back, not the page),
  then `_prefetch_pump()`.
- `_prefetch_pump()` — dispatch ready jobs; self-reschedule on the Tk thread via
  `_track_after` while gated. `driver.pump()` → `None` (idle → stop; a context
  change / completion re-pumps), `0.0` (hit per-pump bound → re-pump in 1 ms), or
  positive `retry_after_s` (rate/time-gated → re-pump at that delay).
- `_build_prefetch_context()` — snapshot source/ticker/interval/compare +
  `partition_watchlists(active sub-tab, pinned)` (universe deferred) into a
  `PrefetchContext` (or `None`).
- `_prefetch_observe(changed_ranks=None)` — no-op when the driver is `None` OR a
  sandbox session is active (sandbox owns the slots offline); else `set_context`
  + `_prefetch_pump`; in shadow mode logs the planned-job count with no
  fetch/cache side effects.
- `_prefetch_observe_soon(changed_ranks=None)` — defer `_prefetch_observe` to the
  next Tk idle via `_track_after(0, …)`, keeping the re-arm OFF the perf-critical
  load path. No timer is scheduled when the feature is off.
- `_prefetch_observe_compare()` / `_prefetch_observe_watchlists()` — scoped
  convenience wrappers (`changed_ranks=[TIER_COMPARE]` / `[TIER_FOCUSED_WL,
  TIER_OTHER_WL]`) so a compare-toggle / subtab / pinned-rebuild re-arms only the
  affected tiers (the scheduler's enqueue-all rebuild still reassigns ownership
  shifts; scoping just avoids dropping unchanged tiers' in-flight deep bands).

### Observe-hook coverage (where the scheduler re-arms)
- **`_load_data_async` chokepoint** (`app.py`, after the sandbox early-return):
  `self._prefetch_observe_soon()` — the single site covering ticker / watchlist
  double-click + space-cycle / chart-stack promote / explicit axis change, since
  they ALL route through `_load_data_async`. Deferred so it never adds to
  ticker-switch latency.
- **compare toggle** (`app.py:_on_compare_toggle`): `_prefetch_observe_compare()`.
  (Compare-*ticker* changes route through `_load_data_async` → covered above.)
- **watchlist subtab change** (`gui/watchlist_tab.py:_on_watchlist_subtab_changed`)
  and **pinned rebuild** (`_kick_watchlist_preloads`):
  `_prefetch_observe_watchlists()`.
- **startup** (`app.py.__init__`, after the initial `_load_data`):
  `_prefetch_observe_soon()`.

## Contract
- Gated by `TRADINGLAB_PREFETCH_SCHEDULER` (default OFF → `_prefetch_driver is
  None` → zero behaviour change). Reads only `self.<attr>` state owned by
  `ChartApp` (`source_var`/`ticker_var`/`interval_var`/`compare_ticker_var`/
  `watchlist_var`/`_watchlists`/`_prefetch_driver`), plus, for the live seam,
  `_fetch_svc` (`submit_prefetch` / `apply_prefetch_result`), `_full_cache`,
  `_stash_full_cache`, `_await_future_on_tk`, `_track_after`.
- The driver's bucket registry depends on the mode (`bucket_registry_for_mode`):
  **live** shares the process-wide `global_bucket_registry()` — the same
  per-source `TokenBucket` the Alpaca fetch path uses (Decision 1); **shadow**
  gets a throwaway `unlimited_bucket_registry()` so dry-run observation never
  spends a real vendor token (review Must-fix).
- Wired into `ChartApp.__init__` (`self._prefetch_driver =
  self._maybe_build_prefetch_driver()` + a startup `_prefetch_observe_soon()`
  after the initial load), the `_load_data_async` chokepoint (covers ticker /
  watchlist / chart-stack / axis switches), `_on_compare_toggle`
  (`_prefetch_observe_compare()`), and the watchlist subtab / pinned-rebuild
  handlers (`_prefetch_observe_watchlists()`).

## Testing
Covered via the flag-on shadow-boot path + the full smoke suite with the flag
OFF (zero regression). `tests/unit/gui/test_prefetch_app_live.py` unit-tests the
live seam with a `SimpleNamespace` fake self: `_prefetch_submit`
(window-None→complete-zero, live fetch→worker-merge+memory-stash+complete,
disk-only deep band→no-stash+stale_guard=False, error→complete-with-retry_after,
None-future→complete-zero) and `_prefetch_pump` (idle/no-reschedule,
hit-bound→1ms, rate-gated→delay, None-driver noop). The pure helpers it composes
are unit-tested under `tests/unit/data/prefetch/`.
