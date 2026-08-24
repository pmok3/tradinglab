# gui/quant_app.py — Spec

## Purpose
`ChartApp` glue for the **Quant** side tab: tab lifecycle, the View → Quant
toggle, double-click routing onto the chart, and the lazy refresh that fills
the tab's Last column. Extracted as a mixin per AGENTS.md §7.24 — no
`__init__`, no `super().__init__()`.

## Public API
- `class QuantAppMixin` — mixed into `ChartApp` between `PrefetchAppMixin`
  and `RecentMenusMixin` (alphabetical).
- `_build_quant_tab()` — construct the tab hidden in `_notebook`. Called once
  from `_build_ui`.
- `_on_view_toggle_quant()` — View → Quant checkbutton command.
- `_quant_tab_visible() -> bool` — revealed AND the selected notebook tab.
- `_on_quant_row_activate(symbol)` / `_on_quant_row_unavailable(row)` — the
  tab's two callbacks.
- `_start_quant_refresh_loop()` / `_stop_quant_refresh_loop()` /
  `_quant_refresh_tick()` — the Last-column refresh loop.
- `_paint_quant_last_values()` / `_submit_quant_fetches()` /
  `_fetch_quant_last(symbol, src)` — repaint, submit, worker body.
- `_quant_fetch_symbol(symbol, src) -> str` — best-effort vendor spelling for
  a catalog symbol under the active source.
- `_quant_fetches_suppressed() -> bool` — true when the refresh tick must not
  make network calls.
- `_format_quant_last(value) -> str` — magnitude-scaled Last formatting.
- `_apply_quant_theme()` — re-tag rows; called from `_on_theme_changed`.
- `QUANT_LAST_INTERVAL = "1d"`, `QUANT_REFRESH_MS = 30_000`.

### State owned on `ChartApp`
`_quant_tab`, `_quant_refresh_job`, `_quant_fetch_inflight` are created by
`_build_quant_tab`. `_quant_visible_var` is a `BooleanVar` on `AppState`
(`gui/app_state.py`), because the menubar is built before `_build_ui` runs.

## Dependencies
- Internal: `gui/quant_tab.py`, `quant/catalog.py` (transitively),
  `data.index_aliases`, `data` (`DATA_SOURCES`, imported inside the worker).
- External: `tkinter`, `threading`, `logging`.

## Design Decisions
- **The tab is added hidden at startup, not built on first reveal.** Notebook
  tab indices then stay fixed for the process lifetime — the same reason the
  Sandbox tab is added hidden. Toggling only flips `state` between
  `"hidden"` and `"normal"`.
- **The menu entry lives in View, not Tools.** View's last group is the
  market-context surfaces (`Live Market Heatmap`, `Heatmap (Finviz)`); Quant
  answers the same question. Tools is for configuration and data plumbing.
- **Last is derived from DAILY bars, whatever the chart interval.** A Quant
  row states a macro quantity; an intraday last for `VIX/15.87` would be a
  different number from the one the description promises. `QUANT_LAST_INTERVAL`
  is the single place that decision lives.
- **Refresh reuses `_apply_watchlist_snapshot_from_bars`.** That is the app's
  documented shared snapshot seam and it already owns the sandbox-clock
  slicing that keeps replay free of look-ahead bias. Re-deriving a last close
  locally is precisely the duplication §7.34 was written about.
  `_watchlist_snapshot` is a flat `symbol -> dict` store; the `watchlist` in
  the name is historical.
- **Network work is gated on selection; repainting is not.** The tick keeps
  running while the tab is revealed so switching back to it shows current
  values immediately, but `_submit_quant_fetches` is skipped unless the tab is
  the *selected* notebook tab. The gate is `Notebook.select()`, NOT
  `winfo_viewable()`: mapping is asynchronous, so a viewability gate skipped
  the very first refresh (the tab is not yet viewable in the same call that
  selected it) and left the Last column blank for a full tick.
- **Three guards keep the 30-second tick free.** A fresh `_full_cache` entry
  short-circuits, `_quant_fetch_inflight` blocks double submission, and a
  missing executor (teardown, unit harness) skips silently. Steady state over
  ~29 symbols is zero HTTP calls.
- **A warm cache with no snapshot is repaired, not refetched.** After a
  restart `_full_cache` repopulates from disk before any Quant snapshot
  exists; deriving the snapshot from those bars avoids a pointless refetch.
- **Quant fetches use vendor symbols.** `_submit_quant_fetches` keys
  `_full_cache`, `_quant_fetch_inflight`, `_fetch_quant_last`, and
  `_apply_watchlist_snapshot_from_bars` on `_quant_fetch_symbol(symbol, src)`.
  The catalog says `VIX`, while the chart, `_full_cache`, and disk cache hold
  `^VIX` / `$VIX` after source resolution. The old shorthand key made the tab
  its own cache namespace and refetched every restart.
- **Paint falls back to catalog spelling.** `_paint_quant_last_values` first
  reads the resolved snapshot key, then the catalog spelling. Snapshots written
  by watchlist paths or older sessions are therefore still displayed.
- **Strict offline suppresses network only.** `_quant_fetches_suppressed()`
  blocks the 30-second background fetch loop during a strict-offline sandbox
  session. This honours the no-network promise; it is not a look-ahead fix,
  because `_apply_watchlist_snapshot_from_bars` already slices Last by the
  sandbox clock.
- **The worker never calls `self.after`.** Bars cross to the Tk thread via
  `_worker_inbox` (§7.15). The direct-stash branch exists for synchronous
  test shims running on the main thread, matching `_preload_one_last`.
- **Last formatting scales with magnitude.** Quant rows span `BTC-USD` near
  80,000 and `RSP/SPY` near 0.29; one fixed precision is unreadable at one end
  or the other.
- **Activation mirrors `_on_watchlist_double`.** Same `_last_hovered_slot`
  routing, same compare-mode gate, same drilldown / time-preserve handling,
  and the Notebook is deliberately not switched away so the user can click
  through several gauges. The "already showing this" short-circuit compares
  `data.index_aliases.canonical_symbol_key`, so a `VIX` catalog row matches a
  ticker box already holding `^VIX`.
- **`_quant_visible_var` is not persisted.** The tab is a reference panel
  pulled up on demand, not a layout preference like ChartStack.

## Invariants
- `_build_quant_tab` never raises into `_build_ui`; on failure `_quant_tab`
  is `None` and every other method no-ops.
- `_quant_refresh_job` is `None` whenever no tick is armed.
- A symbol is in `_quant_fetch_inflight` only while its worker runs; the
  worker's `finally` always discards it.
- The refresh loop stops when the checkbutton is unchecked, and re-arms only
  while `_quant_visible_var` is true.
- `_fetch_quant_last` runs off the Tk thread and touches no Tk widget.
- Quant Last fetches are keyed by the active source's resolved symbol; catalog
  shorthand is only a display input.

## Testing
`tests/unit/gui/test_quant_app.py` — toggle reveals/hides and starts/stops the
loop, activation sets `ticker_var` vs `compare_ticker_var` by hovered slot,
unavailable rows warn instead of loading, `_format_quant_last` boundaries,
fetch dedup + cache short-circuit, and that a missing tab makes every entry
point inert. `tests/unit/test_quant_universe.py` covers resolved-symbol cache
keys, strict-offline suppression, and canonical activation comparisons.
Smoke: `check_g5_quant_tab`.

## Known limitations / Future work
- `GEX` / `DIX` have no feed, so they never populate a Last value.

## Recent history
- Quant Last refresh now shares resolved cache keys with chart loads and
  suppresses network fetches in strict-offline sandbox sessions.
- Initial version alongside `gui/quant_tab.py` and `quant/catalog.py`.
