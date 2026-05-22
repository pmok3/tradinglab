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
`ChartApp(InteractionMixin, WatchlistTabMixin, WorkerPoolMixin, IndicatorMenuMixin, SandboxMenuMixin, DrilldownMixin, tk.Tk)`

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

## app.py impact
7516 → 6193 lines (−1323 / −17.6%) across all three Phase 2-3 extractions.
