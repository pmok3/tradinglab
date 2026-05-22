# app.py — Spec

## Purpose
Top-level Tk + matplotlib application. Owns all runtime state (Tk widgets, `Figure`, caches, stream/fetch tokens, worker pool) and orchestrates the data → render → stream pipeline. `ChartApp` is composed of `tk.Tk` + a stack of mixins (each owns a concern documented in its own `*.spec.md`).

## Public API
- `class ChartApp(PollingMixin, InteractionMixin, WatchlistTabMixin, WorkerPoolMixin, IndicatorMenuMixin, SandboxMenuMixin, DrilldownMixin, EntriesAppMixin, ExitsAppMixin, HelpMenuMixin, FirstRunBannerMixin, tk.Tk)`
- `ChartApp()` — construct + open the window.
- `_load_data()` / `_load_data_async()` — synchronous / executor-backed fetch + render.
- `_render()` — rebuild figure from in-memory series. Sole site of `figure.clear()`.
- `_reset_view()` — switch to `1d`, clear preserve-xlim, snap to right-edge 200-bar window.
- `set_worker_count(n)` — swap the executor.
- `_on_close()` — cancel `after` jobs, stop streams, shut down executor, destroy.
- `main()` — instantiates `ChartApp` and calls `mainloop()`.

Tk control surface (StringVars / BooleanVars driven by tests and dialogs):
`ticker_var`, `compare_ticker_var`, `compare_var`, `source_var`, `interval_var`, `prepost_var`, `dark_var`, `log_price_var`, `watchlist_var`, `status`.

### Sandbox integration surface
Narrow contract for `backtest/replay.py` (controller never touches `_series_cache` / indicator cache directly):
- `_install_sandbox_primary_series(symbol, candles, *, full_session_length=None)` — replace primary's bound list, optionally pre-allocate xlim (`_sandbox_full_session_xlim`). Clears `_series_cache` + `_indicator_cache`. Compare slot untouched (lifecycle handled separately).
- `_install_sandbox_compare_series(symbol, candles)` — equivalent for compare.
- `_sandbox_reset_compare_for_session_start()` — one-shot at start_session. Sets `compare_var=False`, resets compare lists, seeds `compare_ticker_var` to `_DEFAULT_COMPARE` ("SPY").
- `_sandbox_sync_compare_to_var()` — routes typing / cycle-driven compare changes through `register_ticker` + `_install_sandbox_compare_series`. No-ops when desired matches installed, when compare is off, or when desired == primary.
- `_sandbox_register_compare(symbol)` / `_sandbox_register_and_focus(symbol)` — entry-point routing; both gated by `_sandbox_can_register(sym)`.
- `_sandbox_can_register(symbol)` — strict-offline gate (universe allow-list when armed; SPY implicitly added at start).
- `_on_menu_sandbox_prepare_universe()` — opens `UniversePrepareDialog`. Refuses while a session is active.
- `_invalidate_focused_panels(visible_list, ...)` — forming-bar upsert cache drop (`_series_cache` + `_indicator_cache`).
- `_notify_focused_panels_appended(visible_list)` — append-only sibling. Drops `_series_cache` but leaves indicator cache for incremental extension.
- `_repaint_visible_slot_glyphs()` — color-only repaint for HA-flat / key-bar toggles. No `figure.clear()`.
- `_refresh_view_after_append(slot)` — per-tick repaint; re-snaps xlim for both slots (compare shares xaxis).
- `_on_menu_sandbox_start` / `_on_menu_sandbox_end` — session orchestration.
- `_sandbox_handle_interval_change` — toolbar interval gate; valid set = `{primary} ∪ display_intervals ∪ {"1d"}`.
- `_restrict_toolbar_intervals_for_sandbox` / `_restore_toolbar_intervals_from_sandbox`.
- Toolbar `Ticker` and `Compare` are read-only `ttk.Label` (textvariable-bound), not `Entry`.

### Slot taxonomy
Slots are axes-grouping IDs: `"primary"` / `"compare"`. Drilldown reuses the primary slot (not a third slot). Indicator scope is orthogonal — `IndicatorConfig.scopes ⊆ {"main", "compare", "drilldown"}`; primary renders `"main"` (or `"drilldown"` while drilled), compare renders `"compare"`.

### `_panel_state[slot]` schema
13 fixed keys populated by `_render`, consumed by every interaction handler:
- `candles` — `list[Candle]` of the rendered slice.
- `offset` — index in the full series of slice[0] (`full_index = offset + slice_index`).
- `price_ax`, `vol_ax` — `Axes` for candles / volume.
- `render_start`, `render_end` — `[start, end)` full-series bounds of the rendered window.
- `price_wicks`, `price_bodies` — `LineCollection` / `PolyCollection` (H1 fastpath caches segments on these).
- `vol_bars` — `PolyCollection`.
- `price_shades`, `vol_shades` — axvspan `Polygon` lists (pre/post / weekend shading).
- `ind_axes` — `dict[scope, list[Axes]]` for sub-panel indicators.
- `ind_scope` — this slot's current scope (`"main"` / `"compare"` / `"drilldown"`).
- `ind_state` — per-slot indicator render state, owned by `indicators/render.py`.

### Indicator event subscription (`_on_indicator_event`)
Subscribes once to `IndicatorManager`. Filters to `{"add", "remove", "update", "clear", "reorder", "preset_loaded", "loaded", "redraw"}`. `"preset_saved"` / `"preset_deleted"` are intentionally excluded (no chart-state change; menu cascade rebuilds via `postcommand`). Reference-data arrivals (RRVOL compare-symbol bars) clear `IndicatorCache` and route through this same `"redraw"` path so a slot first rendered as all-NaN repaints as soon as the secondary symbol lands.

## Dependencies
- Internal: `. (constants, models, formatting, settings, disk_cache, rendering)`, `.core.*`, `.data.*`, `.indicators.* (factories, manager, render)`, `.streaming.*`, `.watchlists.*`, `.gui.* (dialogs, interaction, workers, watchlist_tab, polling, x_axis_locator, chartstack, geometry_store, live_price_overlay, ...)`, `.backtest.replay`.
- External: `tkinter`/`ttk`, `matplotlib` (Figure, FigureCanvasTkAgg, dates, ticker), `numpy`, `threading`, `queue.Queue`, `concurrent.futures`.

## Design Decisions

### Composition: mixins, not inheritance chains
Mixins have **no `__init__`** and **no `super()`** — `ChartApp.__init__` is the single point where state is initialized. Lets every attribute be found in one file and keeps MRO flat.

File structure:
- `app.py` — class body (lifecycle, rendering, data load, sandbox bridge, themes, menus).
- `gui/polling.py` — `PollingMixin` + scheduler helpers (`_market_window_et`, `_postpone_past_closed_market`, `_next_daily_close_epoch`, `_compute_fetch_delay_ms`); owns `_track_after`, stream-queue / worker-inbox drains, `_schedule_reload`, `_schedule_next_bar_fetch`.
- `gui/x_axis_locator.py` — `_AdaptiveXLocator` + `_make_x_formatter`.
- `gui/{drilldown,interaction,workers,watchlist_tab,indicator_menu,sandbox_menu,entries_app,exits_app,help_menu,banner}.py` — other mixins.

### Two-phase data load (`_load_data`)
1. Submit worker that calls the source fetcher.
2. On empty/error, fall back to `disk_cache.load`.
3. Token-gated callback: `_fetch_token` bump on submit; callback drops if mismatched.
4. `_full_cache[(source,ticker,interval,prepost)]` — OrderedDict, LRU, soft cap `_FULL_CACHE_MAX=16`. Pinned entries never evicted by trim.
5. `_series_cache[id(candles)]` — memoizes `_build_series_safe(...)`; verified via `sa._candles is candles` to defend against id-reuse.
6. `_prefetched_raw` ingests executor-fetched bars without a second
   provider call. When it supplies fresh primary/compare data,
   `_load_data` invalidates indicator entries for the prior visible
   lists before rendering. This prevents stale fingerprint hits from
   rebinding onto replacement lists.
7. Compare-mode pre-fetch via `_ensure_compare_prefetched`.

### Cache staleness (`_cache_is_stale`)
Interval- and session-aware:
- **Intraday (`1m`–`1h`)**: outside Mon–Fri 04:00–20:00 ET, never stale (sealed yfinance bars are immutable). In-session: `now − last_ts > 2 × interval_sec`. Session classification via `zoneinfo`; falls open if `tzdata` missing.
- **Daily+**: `now − last_ts > 2 × interval_sec` (1d → 2 days, absorbs weekend visits).

### Streaming dispatch
- `_start_stream_if_applicable()`: intraday only; bumps `_stream_token`; subscribes; transactional on error.
- Stream callbacks enqueue `(token, slot, src, ticker, interval, kind, bar)` to `_stream_queue`.
- `_drain_stream_queue` (Tk-thread, `after(30)`): dispatches `"tick"` → `_apply_stream_tick` (rightmost in-place mutation, preserves identity), `"rollover"` → `_apply_stream_rollover` (upsert/append). Stale-token events silently dropped. Slot prefix `"card:N"` routes to ChartStack panel.

### Rendering (`_render`)
- `figure.clear()` lives here only.
- Topology by mode: plain = `[price, volume, rsi]` (`[6, 1.5, 2]`); compare = `[primary_price, compare_price, volume, rsi]`.
- `_preserve_xlim_on_render` — capture xlim before clear, restore after. Never auto-cleared; reset only by `_reset_view` / `_do_scheduled_reload`.
- `_slide_xlim_to_right_edge` — one-shot, consumed at top of `_render`; shifts the preserved xlim forward so right edge = `n-0.5`. Set by `_next_bar_fetch_tick` when user was glued to the right edge.

### Adaptive x-axis locator (`_AdaptiveXLocator`)
Picks labels from `_PERIODS` ladder (1min … 5y); chooses smallest period with `span/period ≤ 12`. Intraday spans use `visible_bars × bar_secs`; daily+ uses calendar delta. `_safe_delta_seconds` strips tzinfo on lone-aware side to survive tz-mix lists (`check_d44`).

### Log-price axis (`_apply_price_scale`)
`ylim_changed` callback `_refresh_log_ticks` picks round numbers (`1, 2, 5 × 10^k`). Reinstalled on every `_render`.

### Lifecycle
- All `self.after(...)` ids tracked in `self._after_jobs: set`; `_on_close` cancels them all.
- Streams stopped before executor shutdown; executor shutdown is `wait=False`.

### Next-bar poll (bar-close aligned, market-aware)
Active when no stream is registered for the source. Delay computed by pure helper `_compute_fetch_delay_ms(interval, last_bar_epoch, now_epoch, include_extended, min_backoff_ms)`:
- Intraday: `target = last_bar_epoch + interval_sec + 5s`; `_postpone_past_closed_market` skips weekends / overnight.
- Daily+: `_next_daily_close_epoch` → 16:05 ET next weekday.
- Missing `zoneinfo` falls through unchanged.

**Poll retry on API-not-ready**: when `last_bar_epoch < _poll_retry_expected_min_ts` and `_poll_retry_count < _POLL_RETRY_MAX(=2)`, arm a 5 s retry (bypasses `_MIN_POLL_BACKOFF_MS=30_000`). Up to 3 fetches per bar close. Daily+ never retries.

**Async poll fetch**: `_next_bar_fetch_tick` submits the fetch on `_fetch_executor`; result returned via `self.after(0, _finish)` and consumed by `_load_data` through the one-shot `_prefetched_raw` slot. User-triggered loads stay synchronous.

### Companion-interval prefetch
End of every successful `_load_data` fires background prefetches for `{"5m", "1d"} − {current_interval}` on primary + compare via `_prefetch_companion_intervals`. Dedup via `_prefetch_inflight`, capped at `_PREFETCH_INFLIGHT_MAX=4`. Each prefetch: fresh-cache early-out → dedup → cap → disk-prime → executor submit → stale-overwrite guard (refuse to stomp newer in-memory) → disk merge + save.

### Today's-bar upsampling on the daily chart
Most data providers lag today's daily bar until after the close, so a mid-session user on a 1d chart sees "everything up to yesterday" while the 5m chart shows the live forming bar. `_maybe_upsample_today_daily(candles, source, symbol, interval)` layers a synthetic today-bar onto a daily series by aggregating whatever intraday data is already cached (finest interval wins — see `data/today_upsample.find_best_intraday_source`). Called from `_load_data` AFTER the truthful cache store (so `_full_cache` keeps the provider's raw lagged data, ready to overwrite the synth bar on the next render boundary) and from the compare-on cache-hit branch of `_on_compare_toggle`. When an intraday companion-prefetch lands, `_refresh_daily_synth_for_active_view(prefetched_symbol=...)` re-runs the upsample + pair-filter + render path (no network, no indicator-cache clear — forming-bar invalidation via `_invalidate_focused_panels` covers the right edge). The polling tick on 1d redirects to a 5m prefetch (see `gui/polling.spec.md`). Scope: 1d only; 1wk/1mo deferred (see `data/today_upsample.spec.md`). Audit `daily-today-upsample`.

### Notebook tab labels
`_refresh_tab_labels` updates Primary / Compare titles to reflect `ticker_var` after successful load and bad-ticker revert (`_tab_label_for_primary` / `_tab_label_for_compare`).

### Bad-ticker handling
Revert StringVar to `_confirmed_*_ticker`. Status: `Ticker '{raw}' not found. Check the spelling or try a different data source.` Vendor name omitted intentionally.

### Status messages avoid `repr()`
`_status.info/warn/error` use `{exc}`, not `{exc!r}` (`!r` renders like a crash dump). Short symbolic identifiers (`{scan.name!r}` etc.) keep `!r` for disambiguation. Locked by `tests/unit/test_status_bar_repr_leak.py`.

### Customizable palette
16-slot theme dicts in `constants.py` are the base. `constants.CUSTOMIZABLE_THEME_KEYS` (`win_bg`, `ax_bg`, `text`, `grid`, `bull_row_bg`, `bear_row_bg`) overridable via Settings; merged sparsely under `settings.json["theme_overrides"]` via `constants.resolve_theme`. Public: `set_theme_override` / `clear_theme_overrides` / `replace_theme_overrides`. `_apply_theme` cascades into modeless dialogs (indicator dialog, every per-indicator popup) that own non-ttk widgets the global ttk style doesn't manage.

### Per-indicator settings popups
- `_per_indicator_dialogs: Dict[int, _PerIndicatorDialog]` — singleton registry keyed on `IndicatorConfig.id`.
- `OverlayLegend(on_row_dblclick=…, on_row_context_menu=…)` routes dbl-clicks to `_open_per_indicator_dialog(config_id, slot)` and B3 to `_show_legend_context_menu(...)`.
- Menu: `Edit Settings…` / `Change Color…` (single or cascade per output_key) / `Duplicate` / `Hide ↔ Show` / `Remove`. All delegates swallow exceptions defensively.
- `_apply_theme` and `_on_close` cascade into the registry. Self-eviction via the popup's own `_on_close`.

### Horizontal-line drawings (Feature C)
TradingView-style Alt+H places a price line; double-click → edit dialog; right-click → 7-item canvas menu or 2-item per-line menu. Per-ticker, interval-agnostic, persisted across restarts.

- `_drawings: DrawingStore` — source-of-truth; coalesced `_on_drawing_event` collapses mutations into one `after_idle(_render)` + best-effort `flush()` to `<app_data>/drawings.json`.
- `_drawing_dialogs: Dict[str, DrawingDialog]` — singleton registry keyed on `Drawing.id`.
- `_last_drawing_color: str` — session-sticky last-used color; updated only on `update` events (not `add`, since `add` reads it).
- `bind_all("<Control-h>")` + `<Control-H>` + `<Alt-h>` + `<Alt-H>` → `_on_alt_h_placement`. Focus suppression: if focused widget class in `{Entry, TEntry, TCombobox, Combobox, Spinbox, TSpinbox, Text, TText}`, returns `None` (NOT `"break"`) — must not steal keystrokes. The Help cascade is built with `underline=-1` (see `gui/help_menu.spec.md`) so the Alt+H keystroke no longer opens the menu and is free to fire the drawing placer.
- `_on_alt_h_placement` reads `_last_cursor_px` (set by the mpl motion-event handler) for the cursor pixel position. When that cache is `None` (user hadn't moved the mouse over the chart since the last re-render — a real regression report), it falls back to `_resolve_cursor_px_fallback`, which translates `winfo_pointerxy()` into mpl figure pixels (origin bottom-left, y flipped from Tk's top-down) by subtracting the canvas widget's root xy and using `canvas.figure.bbox.height`. Returns `None` if the pointer is outside the canvas — the keystroke then no-ops gracefully instead of drawing off-axis.
- `_open_drawing_dialog` / `_show_drawing_context_menu` unpack `store.get(id) -> tuple[str, Drawing]` before accessing fields (early bug: forgot tuple unwrap, `except Exception: pass` masked it).
- `_show_chart_canvas_menu` builds: `Add Horizontal Line Here` / `Copy Price` / `Copy Price + Time` / `Reset Zoom` / `Snapshot Chart…` / `Clear All Drawings on <TICKER>`. Bulk uses **Clear**; single-item uses **Delete** (`remove-vs-delete-verb`). Confirm dialog (`messagebox.askyesno`, default NO, WARNING icon) before `clear_symbol`; skipped when zero drawings.
- `_show_drawing_context_menu` builds the 2-item per-line menu: `Edit Properties…` / `Delete This Line`. Posted from the B3 click-no-drag handler when the release was on a line.
- `_redraw_drawings_overlay()` — called inside `_render` after `_draw_slice`. Draws `Line2D` at `zorder=3.5` per slot. No tracking dict needed (next `fig.clear()` removes them).
- `_redraw_live_price_overlay()` — called after `_redraw_drawings_overlay`. Owns `self._live_price_overlay: LivePriceOverlay`. For every slot in `_panel_state`, resolves freshest price via `gui.live_price_overlay.resolve_price(symbol, last_stream_price=self._last_stream_price, panel_state_slot=ps)` and renders dotted neutral line + boxed badge at `zorder=3` / `zorder=4`. Always-on. See `gui/live_price_overlay.spec.md`.
- `_update_live_price_overlay_for_slot(slot)` — fast-path inside `_refresh_view_after_tick`; mutates artists without re-render.
- `self._last_stream_price: dict[str, float]` — symbol → latest stream-tick close, populated by `_apply_stream_tick` / `_apply_stream_rollover` inside a try/except.
- `_repaint_drawings_only()` — fast-path triggered by `_on_drawing_event`. Per slot: `clear_drawing_artists(ax)` then re-render drawings + `canvas.draw_idle()`. Falls back to `_render` on raise.
- `_on_close` closes every drawing dialog and flushes the store.

Persistence: `<app_data>/drawings.json`, format `"tradinglab-drawings"` v1, atomic tempfile + `os.replace`. `flush()` is best-effort.

### Tools menu — BYOD entries
The Tools menu includes two BYOD entries that delegate to the
helper-mixin methods on `HelpMenuMixin`:

- `Tools → Configure Local Data…` → `_on_help_configure_local_data`
  opens `gui.local_data_dialog.LocalDataDialog`. On save the dialog
  calls back via `on_changed` → `_refresh_data_source_combobox()` so
  the toolbar source selector reflects newly-registered BYOD entries.
- `Tools → Export Bars to CSV…` → `_on_tools_export_bars_to_csv` opens
  `gui.export_cache_dialog.ExportCacheDialog` over the current disk
  cache.

`_refresh_data_source_combobox()` delegates to
`self._toolbar.set_sources(tuple(DATA_SOURCES.keys()))` — defined on
`ToolbarController` for this purpose.

### Startup parameters (persisted defaults)
Settings dialog → "Startup parameters" sub-frame. Builtins: `constants.BUILTIN_STARTUP_DEFAULTS` (AMD / SPY / 1d / yfinance / light). Stored sparsely under `settings.json["startup_defaults"]`. `constants.resolve_startup_defaults(...)` validates per-key (interval / source allow-lists, theme ∈ {light, dark}, ticker upper-strip). Public: `set_startup_default` / `clear_startup_defaults` / `replace_startup_defaults`. Changes apply on next launch.

### Display timezone
Settings dialog → "Display timezone" combobox. Stored under `settings.json["display_tz"]`, read into `self._display_tz`. Used by `formatting.format_dt(...)` at three intraday display sites: x-axis `%H:%M` ticks, `_format_candle_date`'s intraday branch, OHLC table rows. Daily+ never converts (a daily bar is a date label, not an instant). `set_display_tz(tz_name)` persists, clears `_SeriesArrays._tooltip_cache`, calls `_refill_table`. Bad IANA names silently fall through to raw `strftime` via `format_dt`'s try/except.

### Pinned watchlist sub-tabs
Top-level `Watchlist` tab hosts a nested `ttk.Notebook` of pinned lists (cap `MAX_PINNED=5` of ~100 in catalog). `_rebuild_watchlist_subtabs()` rebuilds on pin-set change. `_watchlist_tree` back-compat alias points at the selected sub-tab's Treeview. `_apply_theme` loops over `_watchlist_trees.values()`. Preload pipeline iterates `_pinned_ticker_union()` (deduped). Full per-method contract: `gui/watchlist_tab.spec.md`.

### Scanner tab integration
5th right-side tab `Scanner`, built by `_build_scanner_tab()`. Wired with three callbacks:
- `_on_scanner_scan_saved(scan)` → `scanner.storage.save` (debounced 250 ms by `ScannerTab`).
- `_on_scanner_scan_deleted(scan_id)` → `scanner.storage.delete` + `runner.reset_history(scan_id)`.
- `_on_scanner_row_action(symbol, kind)` routes `"primary"` / `"compare"` / `"watchlist"` per sandbox/live state.

Startup opens at most one sub-tab (most-recently-updated); others reachable via "Load…". `_refresh_scanner_for_sandbox()` runs each sandbox tick on the Tk thread (safe — both reads and writes are Tk-bound). `_reset_scanner_state()` resets history on session end. Live mode = v1.1.

### Heikin-Ashi candle display
View → Heikin-Ashi → Show Heikin-Ashi Candles. Substitution is **candle wick/body draw site only** — volumes, indicators, autoscale ranges, OHLC table continue to consume real candles. Hover shows real OHLC but y-axis hit-test uses displayed list (HA bodies often extend past the real `[low, high]`). State: `_ha_display_var: tk.BooleanVar`, persisted under `"heikin_ashi"`. Toggle handler writes setting, calls `_render`, then forces `_autoscale_y_to_visible()` + `draw_idle()` because HA range can exceed real range. H1 stream-tick fastpath bails when on (HA recurrence needs full prefix). Scanner-side HA support is independent (dedicated `ha_*` fields). Audit `ha-menu-cascade` (2026) moved this from a top-level View entry to a child of the `Heikin-Ashi` cascade so the candle-style toggle and the flat-bar overlay share a hierarchy.

### Highlight Key Bars (RDT-style)
View → Highlight Key Bars. Hollow rendering for bars where TR > 1.0× baseline, RVOL > 1.1×, body > 69%. State: `_highlight_key_bars_var`, settings key `"highlight_key_bars"`. Toggle: `_on_menu_toggle_highlight_key_bars` writes setting, re-renders, **then `_autoscale_y_to_visible()` + `draw_idle()`** — defense-in-depth against y-axis "jump" caused by `floor/ceil` vs `ceil/floor+1` between render-path and pan/zoom-path autoscale. H1 fastpath bails when on. Scanner parity via 9 `key_bar*` fields.

### Highlight Flat HA Candles
View → Heikin-Ashi → Highlight Flat Bars. HA-only direction-aware: bull `HA_low == HA_open` or bear `HA_high == HA_open`. **Default OFF** (changed from previously ON in the dark-mode parity sweep — the cross-hatched overlay surprised first-launch users). The HA cascade entry is always enabled/clickable, and `_highlight_ha_flat_var` persists independently of `_ha_display_var`; `_sync_highlight_ha_flat_menu_state` only normalizes the menu entry to `state="normal"`. Audit `ha-menu-cascade` (2026) replaced the previous top-level "Highlight Flat HA Candles" entry with this cascade-nested form. Rendering is gated by **HA mode AND the flat-highlight toggle**: when both are on, renderer layers a hatched `PolyCollection` per side; when HA is off, the remembered flat-highlight preference produces no visible overlay until HA is turned back on. Hatch line color derives from `BULL_COLOR` / `BEAR_COLOR` via `darker_shade` (light) / `brighter_shade` (dark). Key bars take priority — hatch omitted for hollow bars. Scanner parity via three `ha_flat_*` fields sharing the same compute and `eps`.

### 1d → 5m drilldown (double-click zoom)
Double-clicking a candle while `interval=1d` switches to `5m` and tightens xlim to that day's bars (either panel; primary and compare share x). Dispatch on `ChartApp` as `_zoom_5m_for_date(day)`; helpers `_do_drilldown(day)` (interval switch + load) and `_zoom_primary_to_date(day)` (xlim + render).

Three branches:
1. **Cache hit + day covered** — sync drill.
2. **Cache hit, day not covered** (~60d beyond yfinance intraday limit) — status WARN; no fetch (hard upstream limit).
3. **Cache missing** — create `_DrilldownRequest` (`request_id`, `fetch_token`, `src`, `ticker`, `day`); INFO log; schedule `_DRILLDOWN_PREFETCH_GRACE_MS=1500ms` grace; `_retry_drilldown_after_prefetch` re-checks and either drills, surfaces limit, or falls through to `_drilldown_sync_fetch`.

**Latest-click-wins retargeting**: at most one request outstanding. Second click on same `(src, ticker)` bumps `request_id`, updates `day`, cancels and reschedules grace from now.

**Sync fetch fallback** (`_drilldown_sync_fetch`): reuses in-flight prefetch future if present, else submits to `_executor`. Wait cursor + INFO log + 5 s UI deadline (`_DRILLDOWN_SYNC_UI_TIMEOUT_MS`). UI deadline restores cursor + ERROR log; request is **not** cleared so an eventual completion can still drill.

**Validation**: a request is valid iff it `is self._drilldown_request`, `fetch_token == self._fetch_token`, and `(src, ticker)` still matches live vars.

**Centralized cleanup** (`_finish_drilldown_request`): cancels timers, restores cursor, clears request. Called from every terminal branch and from `_on_close`.

Status visibility: every transition emits a typed status log entry (queued / retargeted / attaching / fetching / drilled / no-op coverage limit / UI timeout / fetch error).

Gating (ordered cheap-to-expensive): `interval == "1d"` → axes is a price/vol axis in `_panel_state` → `event.xdata` rounds to a real bar → bar is non-gap → click within ±0.3 columns of bar center. `_preserve_xlim_on_render = True` after a successful drill. `_zoom_primary_to_date` calls `_ensure_rendered_for_view(slot)` per slot before final `draw_idle` (preserve flag means OLD xlim was reused — artists for the new visible slice need to be built).

### Drilldown day persistence across ticker change
`_drilldown_day` records the calendar date on success and survives ticker changes. `_do_scheduled_reload`, `_on_watchlist_double`, `_on_chartstack_promote` route through `_reload_preserving_drilldown(load_fn)` when `_drilldown_day` is set AND `interval == "5m"`. Falls back to most-recent non-gap day if the new ticker has no bars on the exact day; abandons drill only when the new series has no real bars.

Cleared by `_reset_view` and `_on_explicit_axis_change` (source / interval combobox). Pre/Post has its own handler `_on_prepost_toggle` (render-scope, not view-scope) that drills via `_reload_preserving_drilldown` and re-zooms to fit the new bar count.

### Main-window startup layout
Hardcoded ratio every launch via `constants.compute_main_paned_sashes(main_w, chartstack_visible=...)`. **Notebook width is pinned at `max(280, main_w - int(main_w * CHART_PANE_STARTUP_RATIO))` in both 2-pane and 3-pane modes** — toggling ChartStack only steals pixels from the chart, never from the notebook. `_build_ui` dispatches via `self.after_idle(lambda: self._apply_forced_sash(self._main_paned, sashes))`. Helper `_apply_forced_sash(paned, positions, *, attempts=0, max_attempts=40, poll_interval_ms=25)` polls `winfo_width` until the paned is wide enough to accept the position. `_toggle_chartstack` uses the same helper on both show and hide so the canonical layout is reapplied across mid-session toggles. Mid-session drags work but do NOT persist (the `main_paned_2pane`/`main_paned_3pane` geometry-store keys are bypassed end-to-end). Rationale: prior persisted-sash drift caused the watchlist to monopolise the space, and the legacy 3-pane default surfaced a 30/70 notebook:chart split that made the notebook grow on first toggle.

### Window geometry persistence
`gui/geometry_store.py` owns the toplevel window geometry (size + position). The main window falls back to the `defaults.py` `startup_width_pct` / `startup_height_pct` percentages (0.90 / 0.90 by default), centered on the current screen with an 80 px taskbar-height cap. Saved main-window geometry is accepted only when it remains on-screen and at least the startup minimum size (1200×780 on normal displays); stale 1100×700-era saves fall back to the percent default and are overwritten by the next debounce. `<Configure>` bursts debounced at 500 ms. Persistence: `<app_data>/geometry.json`. **Main paned sash is NOT persisted** (see above). Other sashes (entries / exits inner sashes, drilldown panes) still use the store.

### ChartStack
Opt-in mini-chart sidebar (`gui/chartstack/panel.py`). Mounted as the leftmost pane of `_main_paned` when `chartstack.enabled=True`. Cards drive their own fetches via `CardController` → `_worker_inbox` → `panel.apply_card_stash`. Streams flow via shared `_stream_queue` with `"card:N"` slot prefix; per-card-bbox blitting via `mpl_connect("draw_event")` snapshot + `canvas.blit(card.ax.bbox)`. `mpl_connect("button_press_event")` → left-click promotes to primary via `_on_chartstack_promote` (sets `ticker_var`, runs `_on_explicit_axis_change`, then `panel.demote_to(promoted, previous)`).

### Worker → cache hand-off (`_stash_full_cache`)
Background workers marshal results to the Tk thread via `self.after(0, _stash_full_cache, key, bars)`. Sink skips writes if a fresher non-stale entry is already present, promotes new entry via `move_to_end(key, last=False)` so it's LRU-older, and calls `_trim_full_cache(protected_key=key)` so the all-pinned fallback can't evict the just-stashed key.

### Worker-inbox queue (`_worker_inbox` + `_drain_worker_inbox`)
Tk's `createcommand` blocks indefinitely from non-main threads on this build. Workers therefore deposit `("stash"/"card_stash", payload)` on a `queue.Queue`; a periodic 80 ms `_drain_worker_inbox` tick (re-armed via `_track_after`) applies them on the Tk thread. Same-thread fastpath inlines the apply.

### Volume time-of-day shading
Opt-in `_volume_tod_enabled` (default `False`, settings top-level). Settings and View → `Volume time-of-day shading (1d bars)` both drive `set_volume_tod_enabled`, which persists, syncs `_volume_tod_var`, warms the 5m companion cache, and redraws. Adds two collections on every 1d volume bar: a "realized" fill height-scaled to *minutes elapsed / RTH span*, plus a darker envelope at full-day height. Time source is `_now_ms_for_slot(slot)` (sandbox clock or wall-clock). Per-slot artists tracked on `panel_state[slot]['vol_tod_artists']` / `'vol_tod_patches'`. Intraday prefetch arrival calls `_refresh_volume_tod_for_prefetch(...)` so a cold first render repaints when the 5m cache lands. Math contract + degrade paths: `gui/volume_tod_overlay.spec.md`.

### Floating crosshair price + top-left OHLCV readout
`InteractionMixin._ensure_overlay_artists` populates per-price-axes overlays:
- `_price_label_artists: Dict[ax, Annotation]` — left-spine y-tracking floating label with opaque round bbox occluding the baked y-ticks.
- `_readout_artists: Dict[ax, AnchoredOffsetbox]` — top-left `O … H … L … C … Vol …` + signed-pct (`_main_text` neutral, `_pct_text` bull/bear-tinted).

`_apply_overlay_artists(theme)` repaints box/text colours on theme switches; pct color is set per-refresh by sign.

### Mouse-wheel zoom (TradingView-style)
`InteractionMixin._on_scroll_zoom` wired in `_build_ui` via `mpl_connect("scroll_event", …)`. DOWN zooms IN, UP zooms OUT; cursor anchor stays fixed in screen space. Sets `_preserve_xlim_on_render = True`, clears `_slide_xlim_to_right_edge`. Gated off during pan/zoom gestures. `|step|` clamped to ≤2. 3-bar minimum width. `scroll_zoom_invert` setting flips the convention.

## Invariants
1. `_fetch_token` monotonically increases; fetch callbacks check against it.
2. `_stream_token` monotonically increases; stream drain checks against it.
3. `_full_cache` size ≤ `_FULL_CACHE_MAX=16` for non-pinned entries; pinned entries never evicted by trim. Read sites in `_load_data` promote the accessed key.
4. `_series_cache` entries rebuilt on id-reuse (`sa._candles is not candles`).
5. `figure.clear()` only inside `_render`.
6. `_preserve_xlim_on_render` is never auto-reset; only `_reset_view` / `_do_scheduled_reload` clear it.
7. `_slide_xlim_to_right_edge` is one-shot — consumed-and-cleared at top of `_render`.
8. `_after_jobs` contains every pending `after` id; `_on_close` cancels all.
9. Stream tick mutates rightmost bar in-place (preserves `id()`).
10. Rollover with matching date is upsert (no duplicate last bar).
11. Bad-ticker path reverts the StringVar AND calls `_refresh_tab_labels`.
12. Token bump on `_start_stream_if_applicable` drops stale subscription events.
13. Compare-mode toggle without fresh data uses pre-fetched cache when available.
14. `_prefetched_raw` is one-shot: set by poll-tick callback, consumed by `_load_data`, cleared in `finally`.
15. `_poll_retry_count` / `_poll_retry_expected_min_ts` reset on bar advance, retry exhaustion, non-intraday, or explicit reload.
16. Poll retries (5 s × 2) bypass `_MIN_POLL_BACKOFF_MS=30_000`; aligned schedule respects it.
17. `_ensure_prefetched` never touches UI state — only `_full_cache` + disk.
18. `_prefetch_inflight` dedups by `(src, ticker, interval)`, capped at `_PREFETCH_INFLIGHT_MAX=4`.
19. Stale-overwrite guard: prefetch discarded when `_full_cache[key]` last-ts > fetched last-ts.
20. After `_rebuild_watchlist_subtabs`, `set(_watchlist_trees.keys()) == set(_watchlists.pinned_names())` (or empty + placeholder).
21. Sandbox lifecycle: `_sandbox` is `None` outside a session; `_sandbox_universe = frozenset()` outside; `_sandbox_full_session_xlim` is `None` outside; `_preserve_xlim_on_render` resets `False` on end regardless of teardown path; Sandbox tab is `hidden` outside, `normal` while active.
22. `_indicator_cache` is Tk-thread-only (plain `OrderedDict`). Workers must never touch it; cross-thread results funnel through `_worker_inbox`.

## Data Flow

### User-triggered load (synchronous)
```
_load_data():
    token = ++_fetch_token
    memory probe → if fresh: use cached, skip fetcher
    if _prefetched_raw matches: consume          # poll-tick hand-off
    else: candles = fetcher(...)                  # blocks main thread
    if empty: revert StringVar; _refresh_tab_labels(); return
    merge with disk cache; _full_cache[key] = candles
    apply pair filter + align
    _render(); _refresh_tab_labels()
    _schedule_next_bar_fetch(); _start_stream_if_applicable()
```

### Poll-tick (off-thread fetch)
```
_next_bar_fetch_tick():
    _preserve_xlim_on_render = True
    _slide_xlim_to_right_edge = not _user_has_panned_x()
    _poll_retry_expected_min_ts = last_bar_epoch + interval_sec
    evict src/ticker/interval from _full_cache
    token = ++_fetch_token
    fut = _fetch_executor.submit(fetcher, ...)
    fut.add_done_callback(lambda f: after(0, _finish))

_finish():
    if token != _fetch_token: drop
    _prefetched_raw = {...}
    try: _load_data()
    finally: _prefetched_raw = None
```

### Scheduler delay
```
_compute_fetch_delay_ms(interval, last_bar_epoch, now_epoch, include_extended, min_backoff_ms):
    if not is_intraday(interval): return _next_daily_close_epoch(now)  # 16:05 ET next weekday
    target = last_bar_epoch + interval_sec + 5s_grace
    target = _postpone_past_closed_market(target, include_extended)
    return max(min_backoff_ms, (target - now_epoch) * 1000)

_schedule_next_bar_fetch():
    if retry armed AND last_bar < expected AND _poll_retry_count < 2:
        _poll_retry_count += 1; delay = 5_000
    else:
        reset retry state; delay = _compute_fetch_delay_ms(...)
    self.after(delay, _next_bar_fetch_tick)
```

### Lifecycle (splash + bundles)
```
main():
    splash = make_splash()    # PyiSplashController in frozen; NullSplashController in dev
    splash.report(STAGE_SETTINGS)

    # Security audit M1 / L5 — DPAPI prime BEFORE ChartApp construction
    # so vendor-credential reads see env vars on the first attempt.
    _dpapi_prime_result = prime_environment_from_dpapi()
    # Returns one of: "loaded" / "missing" / "dpapi_unavailable" /
    # "decrypt_error" / "io_error" / "import_error". Never raises.

    try:
        app = ChartApp(splash=splash)   # __init__ pushes STAGE_BUILDING_UI / STAGE_FETCHING
                                        # and queues splash.close via after_idle
                                        # (first paint precedes dismiss → no blank frame).
                                        # Also queues _maybe_prompt_sandbox_resume and
                                        # kicks start_update_check on a daemon thread.
    except BaseException:
        splash.close(); raise           # never leave splash above a crash dialog

    # After construct, surface decrypt/io issues via the status bar so the
    # user knows their saved credentials weren't applied this session
    # (most likely cause: the DPAPI entropy v1→v2 bump — re-enter once).
    if _dpapi_prime_result == "decrypt_error":
        app._status.warn("Saved credentials could not be decrypted — "
                         "please re-enter via Tools → Configure Credentials…")
    elif _dpapi_prime_result == "io_error":
        app._status.warn("Saved credentials file could not be read.")

    app.mainloop()

ChartApp._on_close():
    _maybe_write_sandbox_resume_metadata()  # atomic write of sandbox_last.json
    ... teardown
```

`__main__.py` wraps `main()` in `single_instance_guard()`. Double-launch resolves the existing `TradingLab v…` window via `EnumWindows` (Tk class is `"Tk"` so we match on title prefix) and brings it forward.

Update check (`_update_check.start_update_check`) is env-var gated by `TRADINGLAB_UPDATE_URL`. Accepts `{"version": ...}` and `{"tag_name": ...}` shapes. Hits marshal to Tk via `self.after(0, ...)` and pack an idempotent banner via `_show_update_banner` (guarded by `self._update_banner_frame`).

## Testing
~125 smoke checks exercise this file directly or transitively. Representative anchors:
- `check_00_import`, `check_10_state_vars`, `check_20_themes`, `check_30/40/50_render_topology` / `virtualized_render` / `compare_mode`.
- `check_d1_log_price_scale`, `check_60/70/80_*` (pair-filter, executor, scheduler).
- `check_90*_stream*` — streaming dispatch + token gating + coalescing.
- `check_a0/b0` — hover/crosshair + click-to-type.
- `check_c0/c5/c6` — watchlist tab + 5-tab notebook + bad-ticker revert.
- `check_d2_preserve_xlim_across_compare_toggle`, `check_d5_x_axis_pan_stability`, `check_d7_slide_to_right_edge`.
- `check_d8_scheduler_aligns_to_bar_close`, `check_d9_poll_retry_when_bar_not_ready`, `check_d10_offload_to_executor`.
- `test_prefetched_load_invalidates_prior_visible_indicator_entries` — `_prefetched_raw` reloads do not reuse stale indicator results through fingerprint fallback.
- `check_d11_tab_labels`, `check_d12_companion_prefetch`, `check_d13_watchlist_pinned_subtabs`, `check_d14_theme_overrides`, `check_d15_pin_kicks_preload`.
- `check_d16_startup_defaults`, `check_d17_drilldown_to_5m`, `check_d18_display_timezone`.
- `check_d23_perf_h3_h6_m2_m4`, `check_d24_async_user_load`, `check_d25_scroll_wheel_zoom`, `check_d26_scroll_invert`, `check_d27_floating_price_label`, `check_d28_readout_strip`.
- `check_d30_drilldown_ylim_no_deferred_render_race`, `check_d34_compare_toggle_after_drilldown_ylim`, `check_d42_indicator_scope_picker`, `check_d44_locator_tz_mix`, `check_d45_prepost_toggle_rescales_drilldown`, `check_d47_cache_stale_session_aware`, `check_d72_chartstack_promote_preserves_view`, `check_d80_horizontal_lines`.
- `check_b9/b11/b12/b13/b14-b23` — sandbox toolbar, xlim pre-alloc, per-tick compare refresh, clock tz, identity stability, master clock, lookback boundary, memento restore, reentrancy, end-of-session, in-process restart, Entry suppression, mid-session compare toggle/swap, cache invalidation.

Sandbox integration coverage lives in `backtest/replay.spec.md`.

## Known limitations
- `_render` ≈ 600 lines; further per-axes decomposition is the obvious next refactor.
- No multi-chart tiling.
- No real yfinance stream (synthetic only).
- No US market holiday calendar; half-days unhandled.
- Weekly polls every weekday at 16:05 (could tighten to Fridays-only).
- User-triggered `_load_data` is synchronous (poll path is async).
