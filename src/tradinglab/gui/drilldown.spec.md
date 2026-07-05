# DrilldownMixin

Mixin extracted from `ChartApp` (Phase 3 of god-class decomposition). Owns the
dbl-click → 5-minute drilldown flow.

## Public API (used by ChartApp)
- `_do_drilldown(day)` — entry point bound to chart double-click. Single positional `day` argument (a `datetime.date`).
- `_zoom_primary_to_date(day)` — dispatcher: tries cache, else schedules
  fetch, else falls back to daily zoom.
- `_zoom_5m_for_date(day)` — primary worker; pans 5m view to the
  requested session. Returns `bool` indicating success.
- `_reload_preserving_drilldown(load_fn)` — reload that re-applies the active drilldown
  pin afterward. `load_fn` is the loader callable (e.g. `self._load_data` or `self._load_data_async`) the caller wants invoked between the drilldown clear and the post-load zoom.
- Internal completion path: `_drilldown_sync_fetch`, `_on_drilldown_sync_ui_timeout`,
  `_on_drilldown_fetch_done`, `_finish_drilldown_request`,
  `_retry_drilldown_after_prefetch`, `_drilldown_request_is_valid`.

## Module exports
- `_DrilldownRequest` dataclass — moved here from `app.py`. Re-exported via
  `from .gui.drilldown import _DrilldownRequest, DrilldownMixin` in `app.py` so the
  existing `Optional[_DrilldownRequest]` annotation in `ChartApp.__init__` resolves.

## Required ChartApp state (initialised in `ChartApp.__init__`)
- `self._drilldown_request_seq: int`
- `self._drilldown_request: Optional[_DrilldownRequest]`
- `self._drilldown_day: Optional[date]`
- `self._full_cache`, `self._panel_state`, `self._render`, `self.after`,
  `self._executor`, plus standard chart state.

## MRO position
`ChartApp(PollingMixin, InteractionMixin, WatchlistTabMixin, WorkerPoolMixin, IndicatorMenuMixin, SandboxMenuMixin, ConfigMenuMixin, DrilldownMixin, EntriesAppMixin, ExitsAppMixin, HelpMenuMixin, FirstRunBannerMixin, DrawingsAppMixin, LivePriceOverlayAppMixin, RecentMenusMixin, SandboxAliasMixin, SandboxAppMixin, ScannerAppMixin, SnapshotMixin, UpdateCheckMixin, tk.Tk)`

## Validation
- `tests/smoke/test_smoke_drilldown.py`: 7 checks (d17/d20/d30/d34/d38/d45/d53).
- Full smoke (`test_smoke_full.py`): 1 pass / 91s.
- Cross-suite: 332 pass / 14 skipped.

## Notes on request validity
``_drilldown_request_is_valid`` checks **only** that the request is the
current ``_drilldown_request`` slot AND its ``(src, ticker)`` still match
the active selection. It deliberately does **not** compare
``req.fetch_token`` to ``ChartApp._fetch_token`` even though the field is
captured at request creation. The token bumps on every ``_load_data`` /
``_load_data_async`` / periodic ``_next_bar_fetch_tick`` — including
internal loads that don't change context — and was previously silently
invalidating legitimate pending drilldowns whenever such a load
completed inside the grace window after a double-click. The ``(src,
ticker)`` invariant is sufficient: a ticker/source switch still
short-circuits, but a same-context async-load completion no longer
discards the user's drill intent. See d38 sub-test C (latest-click-wins,
retarget pending day).

## Coverage check & the intraday fetch window

When the 5m cache is present but the clicked day isn't in it
(`has_day` is False in `_zoom_5m_for_date` and
`_retry_drilldown_after_prefetch`), the day is NOT assumed unavailable.
The cache can be stale or only partially companion-prefetched while the
user sits on the 1d chart, so a recent day — including **today** — may
be missing even though a manual 5m toggle would load it. The fix:

- `_day_within_intraday_fetch_window(day, interval="5m")` returns True
  when `day` is reachable for the active source. **Two regimes:**
  - **Range-capable providers** (Alpaca — `data.source_supports_range`)
    fetch any historical day on demand, so reachability is gated on the
    learned coverage `data_start` watermark
    (`data.coverage.data_start`): unknown → always reachable; known →
    `day >= data_start − 7d`. No trailing-window cap applies.
  - **Trailing-window providers** (yfinance) use the provider-aware
    intraday window `constants.provider_lookback_days(source_var.get(),
    interval)` (yfinance capped at ~60d for 5m), measured against
    `date.today()` with a generous 7-day buffer. Called with `self=None`
    the `source_var` read is caught → the yfinance windows.
- **Day inside the window** → fall through to the fetch path (branch 3 /
  `_drilldown_sync_fetch`), identical to a cold cache miss. The fetch
  uses the same `DATA_SOURCES` fetcher a manual toggle uses;
  `_on_drilldown_fetch_done` re-checks coverage and drills, or warns
  `"5m data fetched but does not cover …"` if the provider genuinely
  lacks it.
- **Day predates the window** → the synchronous WARN `"… no-op: 5m
  data only available from … onward"` and no fetch (the only
  case that warning now fires).

This fixed the reported bug where drilling into today / a recent day (or
any gap in a stale cache) errored "only available from … onward" even
though the day was well within yfinance's reach. Pinned by
`tests/unit/gui/test_drilldown_fetch_window.py` (window logic) and
`test_smoke_full.py::check_d38…` sub-tests B (out-of-window → WARN) and
B2 (in-window-but-uncovered → fetch).

## Targeted intraday fetch (range-capable providers)

For providers where `data.source_supports_range(src)` is True (Alpaca),
`_drilldown_sync_fetch` no longer bulk-loads a trailing window. Instead it
pulls **just the clicked day's ~1-API-page window** on demand, so drilling
into an arbitrarily old day is fast and stays within the sync-UI deadline.
See `docs/TARGETED_FETCH.md` for the locked design.

Flow (all worker steps run on `self._executor`, never touching Tk):

1. **Skip prefetch reuse.** A trailing companion-prefetch cannot contain an
   old day, so `existing_fut` is forced to `None` for range-capable sources;
   a fresh targeted `_work` is always submitted.
2. **Capture compare on the Tk thread.** The active compare symbol
   (`compare_var` on + `compare_ticker_var`) is read in
   `_drilldown_sync_fetch` (Tk thread) and stored on `req.compare_ticker`;
   the worker must never read a Tk variable.
3. **Worker: `_targeted_range_fetch(src, sym, interval, day, now_ts, *,
   merge_to_disk)`** —
   - loads/bootstraps the `coverage` sidecar, reads `data_start`, and
     computes `constants.targeted_window(interval, day_ts, now_ts=…,
     data_start_ts=…)` centered on the clicked day (clamped to now / the
     provider data-start);
   - if the window is already `coverage.covered(…)`, returns the on-disk
     series (no network) so the caller's merge still finds the day;
   - else `data.fetch_range(…)` → on `ok` records coverage
     (`record_fetch`, learning `data_start` when bars start materially
     later than requested) and returns the bars; on `empty` records the
     attempt and returns `[]`; on `unsupported`/`error` degrades to the
     plain trailing-window `_trailing_fetch`.
   - `merge_to_disk=False` for the **primary** (whose result
     `_on_drilldown_fetch_done` merges into `_full_cache` + disk itself);
     `merge_to_disk=True` for the **compare** symbol (persisted here since
     the primary-only done-handler won't).
4. **Tk: `_on_drilldown_fetch_done`** merges the primary result as before,
   then — when `req.compare_ticker` is set — reloads the compare symbol's
   `_full_cache` entry from disk (the worker extended it) so the drill's
   re-render draws an aligned RS/compare line over the same window, and
   drills.

Non-range providers (yfinance) keep the original single-fetcher path
unchanged. `_targeted_range_fetch` / `_trailing_fetch` never raise.

Pinned by `tests/unit/gui/test_drilldown_fetch_window.py` (range-source
reachability + `data_start` gating) and
`tests/smoke/test_smoke_full.py::check_d84…` (targeted drilldown end-to-end
with a stubbed range-capable source).

## app.py impact
7516 → 6193 lines (−1323 / −17.6%) across all three Phase 2-3 extractions.
