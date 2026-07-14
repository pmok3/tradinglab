# gui/watchlist_tab.py — Spec

## Purpose

Mixin owning the Watchlist tab's **pinned sub-tab container**,
snapshot-driven repaint, background preload workers, and
click-to-sort. The top-level Watchlist tab hosts a nested
`ttk.Notebook` of *pinned* watchlists (up to
`WatchlistManager.MAX_PINNED`; default 5). The catalog can be
larger; pinning makes a list reachable from the main UI.

## State (initialized by `ChartApp`)

- `_watchlist_snapshot: Dict[ticker, dict]` — shared ticker data pool.
- `_watchlist_subnotebook: ttk.Notebook`.
- `_watchlist_sub_frames: Dict[name, ttk.Frame]`.
- `_watchlist_trees: Dict[name, ttk.Treeview]`.
- `_watchlist_sort_by_name: Dict[name, Tuple[Optional[str], bool]]`.
- `_watchlist_empty_frame: Optional[ttk.Frame]` — 0-pinned placeholder.
- `_watchlist_tree` — **alias** pointing at the selected sub-tab's
  Treeview (back-compat for smoke tests and `_apply_theme`).
- `_watchlists`, `_watchlist_tab_refresh_job`, `_after_jobs`,
  `watchlist_var` (mirrors active sub-tab name).

## Public API

- `_DEFAULT_WATCHLIST_NAME` and `_DEFAULT_WATCHLIST_TICKERS` are
  re-exports of `tradinglab.watchlists.DEFAULT_WATCHLIST_NAME` /
  `DEFAULT_WATCHLIST_TICKERS` (single source of truth).
  Columns are **configurable per watchlist** (see *Configurable
  columns* below); the fixed `_WL_COLUMNS` tuple was removed in favour
  of `watchlists.columns.default_columns()`.
- `class WatchlistTabMixin`:
  - `_ensure_default_watchlist()` — first-run creates "Default";
    auto-pins first name if pin list is empty after load.

### Sub-tab plumbing

- `_build_watchlist_container(parent) -> Frame` — builds nested
  `ttk.Notebook` once during `_build_ui`. Binds
  `<<NotebookTabChanged>>` and `<Button-3>`.
- `_rebuild_watchlist_subtabs()` — teardown + rebuild from
  `WatchlistManager.pinned_names()`. Preserves selection by name.
  Calls `_apply_theme()` so post-init rebuilds get bull/bear
  colors. Populates every sub-tab once.
- `_make_watchlist_tree(parent, name) -> Treeview` — dynamic-column
  tree; sort command captures `name` so click-to-sort is
  per-sub-tab. Installs widget-level
  `<KeyPress-space>` → `_cycle_watchlist_ticker()` returning
  `"break"` (highest-priority binding fires before the Treeview
  class binding which would otherwise swallow the event).
- `_build_watchlist_empty_state()` — `(no pins)` placeholder tab.
- `_on_watchlist_subtab_changed(event)` — mirrors selection into
  `watchlist_var`, updates alias, repaints.
- `_sync_watchlist_tree_alias()` — points alias at selected
  sub-tab (or first pinned as fallback).
- `_on_watchlist_subtab_right_click(event)` — identifies tab via
  `notebook.index(f"@{x},{y}")` (guarded by `TclError`); pops
  context menu `Unpin` / `Move left` / `Move right` (Move
  disabled at edges). The popup `tk.Menu` is themed inline at
  construction via `_current_menu_colors()` — because it is
  created on-demand and discarded after `tk_popup`, it cannot live
  in the controller's `_menubar_submenus` sweep. Audit
  `watchlist-popup-theme`.
- `_current_menu_colors() -> dict[str, object]` — reads the current
  palette from `self._theme_ctrl.theme` and returns the
  `(background, foreground, activebackground, activeforeground,
  selectcolor, disabledforeground, borderwidth, relief)` kwargs
  dict used to colour on-demand `tk.Menu` popups so they match dark
  mode. The active row uses `grid` rather than `text` so hover does
  not flash a light strip in dark mode. The `_pick_watchlist_name`
  Toplevel + Listbox use the same theme lookup inline (separate from
  this helper since they need `win_bg`/`tree_bg`/`tree_fg`/`spine`
  rather than the menu set); the Listbox also pins its focus ring
  and border to `spine`/flat chrome so the picker has no Win32
  `SystemButtonFace` ring. Audit `watchlist-entries-full-dark`.
- `_unpin_watchlist(name)` / `_move_pinned_watchlist(name, delta)`
  — mutate manager then `_rebuild_watchlist_subtabs`.

### Repaint + sort

- `_on_watchlist_double(event)` — double-click: uses `event.widget`
  to pick the right tree. Routes to `compare_ticker_var` when
  `_last_hovered_slot == "compare"` AND compare mode on; else
  `ticker_var`. Calls `_load_data()` (or
  `_reload_preserving_drilldown` for active drilldown +
  interval=5m). Does NOT switch notebook away from Watchlist.
- `_cycle_watchlist_ticker()` — Space-key handler: advances
  `_last_clicked_slot`'s ticker to next entry in active pinned
  watchlist (mod-N wrap, stateless lookup by current symbol).
  Returns False on empty / no-op cycles. Honors drilldown lock
  via `_reload_preserving_drilldown`. Outside drilldown it sets
  `_preserve_xlim_by_time_on_render = _ticker_change_should_time_preserve()`
  (True only when the current view is HISTORICAL) so a cycle at the
  default right-edge view shows the NEW ticker's own default window
  instead of imposing the previous ticker's calendar window —
  otherwise a sparse small-cap's wider reset window misaligns a dense
  large-cap's left edge (audit `ticker-switch-default-view-align`).
  Same gate applies to `_on_watchlist_double`.
- `_sort_watchlist_by(name, col)` — toggle sort; updates
  `_watchlist_sort_by_name[name]`; repaints.
- `_populate_watchlist_tab(name=None)` — repaint Treeview for
  pinned watchlist `name` (or current if None). Partitions rows
  by `(is_missing, value)` so blanks always trail, then sorts
  with `list.sort(reverse=reverse)`. (Negate-value approach was
  buggy for prefix-string columns like `A` vs `AA`.) Builds the
  desired `(ticker, values, tag)` rows then delegates to
  `_diff_watchlist_rows` for minimal-churn application.
- `_diff_watchlist_rows(name, tree, rows)` — incremental Treeview
  update (qw-watchlist-diff). Each ticker doubles as its row iid, so
  when the ordered ticker list is unchanged only rows whose displayed
  cells changed are touched (one `tree.item` call each, gated by the
  per-name `_watchlist_row_cache`), instead of the legacy delete-all +
  reinsert-all every 60 ms refresh. Side benefit: the user's selection
  and scroll position survive a live-price refresh. Full rebuild (keyed
  by ticker iid) fires only on row add/remove/reorder. Falls back to a
  legacy auto-iid rebuild (and drops the cache for that name) when the
  ticker list has duplicates, since duplicate iids are illegal. The
  cache self-heals: a recreated (empty) tree has no children, so the
  order check forces a rebuild that repopulates it.
- `_populate_all_watchlist_tabs()` — repaints **only the visible
  sub-tab** (`watchlist_var.get()`); hidden sub-tabs are repainted
  lazily on switch by `_on_watchlist_subtab_changed` from the same
  shared preload cache (qw-watchlist-visibletab). Falls back to
  repainting every sub-tab when the selected name isn't a known tree.
  Used by the debounced refresh.
- `_watchlist_sort_key(col, ticker, snap) -> (is_missing, value)`.
- `_schedule_watchlist_tab_refresh(delay_ms=60)` /
  `_run_watchlist_tab_refresh()` — debounce; callback delegates to
  `_populate_all_watchlist_tabs` (visible sub-tab only, with fallback
  to all pinned tabs when selection is indeterminate).

### Ticker helpers

- `_watchlist_tickers(name=None) -> List[str]` — falls back to
  first-pinned, then first-existing.
- `_pinned_ticker_union() -> List[str]` — deduped union across
  all pinned (preserves first-seen order).

### Preload

- `_preload_watchlist()` / `_preload_watchlist_daily()` — one
  fetch per ticker in `_pinned_ticker_union()` (dedup prevents
  fetching shared tickers N times).
- `_preload_watchlist_events()` — fans `_load_events_async` over
  `_pinned_ticker_union()` for tickers missing from `_events_cache`,
  so the default Next Earnings column fills proactively. Repeated calls
  are harmless because `_load_events_async` also in-flight dedupes.
- `_apply_watchlist_snapshot_from_bars(ticker, src, itv, bars)` —
  Tk-thread snapshot seam shared by legacy preload workers and the
  scheduler live seam. Intraday bars update `last`, `_last_source`,
  `_last_day`, then recompute Change from cached daily tails; daily bars
  update the Change columns (and may provide a daily fallback Last when no
  intraday Last exists). Preserves sandbox replay slicing: intraday Last
  uses only bars at-or-before the replay clock, and daily Change uses only
  sessions before the replay session date. Successful updates queue a
  `("refresh", None)` inbox item.
- `_preload_one_last(ticker, src=None, itv=None)` /
  `_preload_one_daily(ticker, src=None)` — worker-thread fetchers.
  Fetch bars, delegate snapshot derivation to
  `_apply_watchlist_snapshot_from_bars`, and warm
  `ChartApp._full_cache[(src, ticker, itv)]`. `src` / `itv` read
  on **caller's** thread (typically Tk main) and passed as args
  — workers must not access Tcl/Tk variables. Intraday Last writes
  carry private `_last_source` / `_last_day` metadata so daily Change
  can anchor to the correct prior session close.
- **Inbox queue, not `after()`** — results deposited on
  `ChartApp._worker_inbox` (queue.Queue), drained by
  `_drain_worker_inbox` every ~80 ms. (`tk.createcommand` blocks
  under `self.after` from non-main threads on this Tk build, so
  direct worker `after()` would silently drop completions.)
  Items: `("stash", (key, bars))`, `("refresh", None)`.
- **Synchronous fast path** for smoke tests: when invoked on the
  Tk thread the cache stash is applied inline.
- `_kick_watchlist_preloads()` — incremental wrapper invoked at
  the end of `_rebuild_watchlist_subtabs`. Submits
  `_preload_one_last` when `last` is missing or only a daily fallback,
  and `_preload_one_daily` only when both `change_1d` and `chg` are
  missing. Lets brand-new pins populate without requiring
  `_load_data`.

### Recurring poll loop

- `_WATCHLIST_POLL_RTH_OPEN_MIN = 570` / `_WATCHLIST_POLL_RTH_CLOSE_MIN
  = 960` — approximate US regular trading hours in ET (09:30 incl.
  – 16:00 excl., weekdays). Holidays not handled (worst case: a
  few extra cache-fresh short-circuit hits on a holiday).
- `_watchlist_poll_in_rth_now() -> bool` — True iff wall-clock is
  inside the RTH window above. Conservative fallback (`True`) when
  `zoneinfo` is unavailable so the user sees live-cadence polling
  rather than a silent off-hours slowdown.
- `_watchlist_poll_effective_delay_ms() -> Optional[int]` — reads
  `defaults.get("watchlist_poll_interval_sec")` (default 60) and
  `defaults.get("watchlist_poll_offhours_multiplier")` (default
  5.0). Returns `None` when interval ≤ 0 (disabled). Off-hours
  multiplies the interval. **Floor of 5 seconds** as a defense
  against misconfiguration causing tight-loop spam.
- `_start_watchlist_poll_loop()` — arms the first tick via
  `_track_after`. Called once from `ChartApp.__init__` after the
  initial `_kick_watchlist_preloads()`. Idempotent
  (`getattr(self, "_watchlist_poll_job", None) is not None` ⇒
  no-op). When polling is disabled, sets `_watchlist_poll_job =
  None` and returns.
- `_watchlist_poll_tick()` — re-runs `_preload_watchlist` +
  `_preload_watchlist_daily` and re-arms itself. **Sandbox guard**:
  while a replay session is active the engine drives clock
  advancement, so we skip the preload body BUT still re-arm so
  polling resumes immediately on sandbox exit (different from
  `_schedule_next_bar_fetch`, which drops the timer entirely on
  sandbox). **Visibility guard (qw-watchlist-visguard)**: the preload
  body is also skipped when `_watchlist_tab_visible()` is False (the
  Watchlist outer-notebook tab is off screen) — the fetch + snapshot
  work competes with chart interaction and isn't visible anyway; the
  tick still re-arms, so the data refreshes within one poll interval
  of the user returning to the Watchlist tab. The preload helpers own
  their own cache-freshness + in-flight dedup, so a tick on a
  fully-cached watchlist during RTH costs zero HTTP calls. A tick
  after a transient fetch failure re-submits the missing tickers and
  clears the visible orphan.
- `_watchlist_tab_visible() -> bool` — `True` when the Watchlist
  outer frame `winfo_viewable()` is truthy. Defaults to `True` when
  `_watchlist_outer_frame` is missing or Tk geometry can't be probed
  (early init / headless harness) so a visible watchlist is never
  starved.
- **Orphan-snapshot recovery** in `_preload_watchlist` /
  `_preload_watchlist_daily`: when the disk-cache is fresh but the
  `_watchlist_snapshot` row is missing `last` / `change_1d` /
  `pct_1d`, rebuild from cached intraday Last plus the cached daily
  tail. A daily-only fallback is tagged with `_last_source="daily"`
  so the next intraday repair or fetch overwrites it and recomputes
  Change. When any orphan repair runs, a
  `_schedule_watchlist_tab_refresh()` nudge ensures the repaint
  catches up.

### Configurable columns (signal columns)

- `_watchlist_columns(name) -> list[WatchlistColumn]` — the effective
  ordered columns for a pinned sub-tab: `WatchlistManager.columns_for(name)`
  when set, else `watchlists.columns.default_columns()` (today's
  Ticker/Last/Change/Change%/Next). Drives `_make_watchlist_tree` (dynamic
  `columns=` + `header_label` headings) and the per-row value tuple.
- `_watchlist_cell_text(col, ticker, snap, now_ms) -> str` — one cell's
  display string. System columns read `_watchlist_snapshot` as before;
  signal columns read `snap["_sig"][col.id]` (a `signals.ColumnValue`),
  showing `…` until the first evaluation and `–` on insufficient data.
- Signal sort: `_watchlist_sort_key` falls through to
  `snap["_sig"][col].raw` (blanks last) for non-system columns.
- **Off-thread evaluation** (no-op when no signal columns configured):
  `_preload_watchlist_signals()` collects the union of signal columns via
  `_pinned_signal_columns()`, dedupes to one in-flight job
  (`_watchlist_signals_inflight`), and submits `_compute_watchlist_signals`.
  The worker drives a cached `watchlists.signals.WatchlistSignalEvaluator`
  (rebuilt when `source_var` changes) whose `bars_provider` is
  `_signal_bars` (prefers `_full_cache`, else the data-source fetcher;
  slices to `_sandbox_watchlist_clock()` during replay). Results are
  written into `snap["_sig"]` directly (worker-owned snapshot write, same
  pattern as `_preload_one_last`) then a `("refresh", None)` inbox nudge
  repaints. Triggered from `_watchlist_poll_tick` (live + sandbox when
  visible), `_kick_watchlist_preloads`, and `_refresh_watchlist_for_sandbox`.
- `_open_watchlist_columns_dialog(name)` — opens
  [`gui/watchlist_columns_dialog`](watchlist_columns_dialog.spec.md) via
  `open_columns_dialog(self, name)`; wired into the sub-tab right-click
  menu ("Columns…"). On apply: `set_columns` → row-cache drop →
  `_rebuild_watchlist_subtabs` → `_preload_watchlist_signals`.

## Dependencies

- Internal: `..data.DATA_SOURCES`,
  `..constants.BULL_COLOR`/`BEAR_COLOR` (late-imported),
  `..watchlists.columns` (`default_columns`, `header_label`, `KIND_SIGNAL`),
  `..watchlists.signals` (`WatchlistSignalEvaluator`, lazy in the worker),
  `.watchlist_columns_dialog.open_columns_dialog` (lazy).
- External: `tkinter`, `tkinter.ttk`.

## Design Decisions

- **Nested notebook**: keeps top-level notebook stable (Primary /
  Compare / Watchlist).
- **Full rebuild on pin changes** (cheap for ≤5 sub-tabs).
- **Per-tab sort state**; stale entries pruned during rebuild.
- **Shared `_watchlist_snapshot`** keyed by ticker — one ticker's
  data is the same in every sub-tab; preload fires once per
  unique ticker via `_pinned_ticker_union`.
- **`_watchlist_tree` back-compat alias** for smoke tests + theme
  loop; `_sync_watchlist_tree_alias()` keeps it pointed at the
  selected pinned tree.
- **`_apply_theme` loops `_watchlist_trees.values()`** so every
  tree gets bull/bear tag colors.
- **Change columns pinned to 1d** regardless of chart interval.
  The displayed move matches broker ticker semantics, not the
  currently selected chart aggregation.
- **Live Change anchor rules**: live Change and Change Pct use
  `Last − prior_session_close`. `Last` is the most recent intraday
  close when available. The prior close is the latest daily bar whose
  session date is strictly before the intraday Last's session date,
  so a provider-emitted current-day partial daily bar is never used
  as the reference. If intraday Last is unavailable, daily closes
  provide a temporary day-over-day fallback until intraday refresh
  lands.
- **Sandbox-aware watchlist values**: while sandbox active, both
  worker fetchers slice the cached series against
  `ChartApp._sandbox_watchlist_clock()` (returns `(active,
  clock_ts, session_date)`) before writing to the snapshot —
  otherwise watchlist shows today's live values during a
  historical replay. `_preload_one_last` uses close of latest
  intraday bar whose timestamp ≤ `clock_ts`. `_preload_one_daily`
  filters to bars whose `date.date() < session_date`; computes
  `change_1d = last_intraday − prior_session_close` (matches a
  real broker ticker at that historical moment). If intraday Last
  has not landed, it falls back to filtered day-over-day.
  `_refresh_watchlist_for_sandbox()` clears clock-dependent fields
  and resubmits both preloads; called on (a) sandbox start,
  (b) every `next_bar` advance.
- **Double-click preserves tab focus** (user-requested).
- **Configurable signal columns** (feature `watchlist-columns`): a
  watchlist's columns are user-chosen scanner `FieldRef`s evaluated at the
  latest bar (reusing the scanner engine — no watchlist-specific math).
  Zero cost when unused: a watchlist with only system columns never submits
  a signal job. `ticker` stays first + locked. Values are sandbox-clock
  sliced so signal columns carry no look-ahead in replay. See
  `docs/WATCHLIST_COLUMNS.md`.

## Invariants

- `_watchlist_snapshot` keys are upper-cased ticker symbols.
- Row tags are `("bull",)`, `("bear",)`, or `()` only.
- `_populate_watchlist_tab(name)` makes the Treeview's visible rows
  equal the desired (sorted) row set — via an incremental diff
  (`_diff_watchlist_rows`) that updates only changed cells when the
  ordered ticker list is unchanged, else a full ticker-iid rebuild.
- Sort with missing values: blanks at bottom regardless of dir.
- `_watchlist_trees.keys() == WatchlistManager.pinned_names()`
  after every rebuild (or `{}` in empty state).
- `watchlist_var.get()` equals visible sub-tab's name (or last
  selected name immediately post-rebuild).
- **Workers must not touch Tk widgets, StringVars, or call
  `self.after`**. All worker→UI marshalling via `_worker_inbox`.
- **`WatchlistManager` is NOT observed** — refresh requires
  explicit `_rebuild_watchlist_subtabs` after mutation.
- **Signal columns are no-op-safe**: `_preload_watchlist_signals()` returns
  immediately when no pinned watchlist has a signal column, so the legacy
  system-only refresh path is unchanged. `snap["_sig"]` is a
  `{col_id: ColumnValue}` dict written only by the signal worker.

## Data Flow

```
_rebuild_watchlist_subtabs():
    pinned = manager.pinned_names()
    remember current selection
    teardown all sub-tab widgets
    prune _watchlist_sort_by_name keys not in manager.list_names()
    if not pinned:
        render empty-state placeholder
        _add_plus_subtab()
        return
    for name in pinned: make tree, add to subnotebook
    _add_plus_subtab()             # hidden at MAX_PINNED
    restore selection (by name) or fall back to pinned[0]
    _apply_theme()                 # tag colors on new trees
    populate every sub-tab
    _sync_watchlist_tree_alias()
    _kick_watchlist_preloads()
```

The `"+"` sub-tab opens a modal picker of **unpinned but existing**
watchlists (not a new-name prompt — brand-new creation is owned
by the Watchlists toolbar button). Hidden only when
`len(pinned) >= MAX_PINNED`; when no unpinned candidates remain
it stays visible (discoverability) and clicking surfaces a hint
pointing at the Watchlists button.
