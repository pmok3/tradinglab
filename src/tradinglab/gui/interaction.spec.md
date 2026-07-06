# gui/interaction.py — Spec

## Purpose

`InteractionMixin` — the entire interactive chart subsystem: pan,
zoom (rubber-band + scroll-wheel), hover tooltip, crosshair,
click-to-type ticker entry (accepts letters plus `. _ - /`; `/` enables ratio symbols like `AMD/NVDA`), and the drawings hit-test bridge.
Kept as one mixin because all features share `_blit_bg`, the
animated-artist list, the pixel cache, and the mpl event wiring.

## Public API

All methods are private. Registered into matplotlib event callbacks
by `ChartApp._build_ui`.

### Event dispatchers

- `_on_button_press(event)` — dispatch to `_pan_begin` (left) /
  `_zoom_begin` (right); record `_drag_press` for click-vs-drag;
  grab keyboard focus. Also routes:
  - `event.button == 1 and event.dblclick == True` on a drawing →
    `ChartApp._open_drawing_dialog(drawing.id)` BEFORE the
    1d-drilldown check (line over candle wins).
  - B1 on a lower-pane indicator label → `_open_per_indicator_dialog(config_id, slot)`; B3 on the same label → `_show_legend_context_menu(...)`.
  - B1 / B3 on an in-readout overlay legend row (`_maybe_handle_readout_legend_click`) → B1 `_open_per_indicator_dialog(config_id, slot)`; B3 `_show_legend_context_menu(...)`. Gated before pan/zoom so a legend click never starts a pan. Hit-test via `_readout_legend_row_hit` (per-row `HPacker.get_window_extent` pixel test — the whole condensed row maps to one indicator config_id).
  - Double-click on a 1d candle (primary OR compare) →
    `_maybe_handle_dblclick_drilldown` → `_zoom_5m_for_date(day)`.
- `_on_button_release(event)` — terminate pan / zoom; detect click
  (< 3 px) to start click-to-type. B3 click-vs-drag branch reuses
  the squared-distance test: a B3 release within 3 px snaps to
  per-line context menu (release on drawing) or canvas context
  menu (on background). B3 drags fall through to rubber-band zoom
  via `_zoom_end`'s no-drag short-circuit.
- `_on_mouse_move(event)` — pan drag prio; else zoom; else cache
  cursor pixel + `_dispatch_hover`. Throttled to ~60 Hz
  (`_HOVER_THROTTLE_MS = 16`) via `_hover_pending_event` slot +
  single `_track_after` job. Pan/zoom drags short-circuit the
  throttle.
- `_on_draw_event(event)` — capture full-figure `_blit_bg` after
  every full mpl redraw. **Short-circuits when `_suspend_draw_capture`
  is set** (the tick-blit seed below issues a hidden full draw that must
  NOT overwrite `_blit_bg`). A genuine redraw also drops `_tick_blit_bg`
  (decorations were repainted, so the data-less snapshot is stale).
- `_on_key_press(event)` — accumulate keystrokes into
  `_typing_buffer` (letters + `._-/` only; digits ignored to avoid
  phantom buffers). Enter commits, Escape cancels, Backspace
  deletes. **Space (`keysym == "space"`)** returns early — the
  watchlist-cycle hotkey is owned by app-level
  `bind_all("<KeyPress-space>")`. Handling here too would
  double-cycle.

### Pan / zoom / drilldown

- Pan: `_pan_setup_blit`, `_pan_begin`, `_pan_drag`,
  `_pan_redraw_tick`, `_pan_end`,
  `_pan_rebind_animated_after_slice`.
- Zoom (rubber band): `_zoom_begin`, `_zoom_drag`, `_zoom_end`.
- Scroll-wheel: `_on_scroll_zoom`.
- Drilldown: `_maybe_handle_dblclick_drilldown(event)` —
  gate-and-dispatch for 1d → 5m. Returns `True` when consumed.
  Gates: `interval=="1d"`; `event.inaxes` is any panel's
  price-or-volume axes (sharex propagates from primary); rounded
  x-index within candle list AND within ±0.5 column of bar center
  (snap-to-nearest, whole column clickable); non-gap bar. On hit:
  `self._zoom_5m_for_date(c.date.date())`.

### Y-autoscale

- `_autoscale_y_to_visible` — recomputes Y from visible X. Called
  end of pan/zoom/tick refresh. Uses `ceil(lo_f)`/`floor(hi_f)`
  with epsilon so only bars whose centers lie inside the xlim
  contribute (half-overlapping neighbors excluded — matters for
  drilldown across day-gaps).
- `_autoscale_indicator_pane(ax, lo, hi)` — refits ONE pane during
  pan/zoom. Unions the lines of EVERY config whose `state.panes[cid]
  is ax` (not just the first), so a shared pane (RVOL Cumulative +
  ToD) fits both series. Reference axhlines are excluded (they live on
  `ax.lines`, not `pane_lines`). Mirrors the full-render path in
  `ChartRenderer.autoscale_indicator_panes_for_slot`.

### Hover, crosshair, value labels

- Hover: `_ensure_overlay_artists`, `_dispatch_hover`,
  `_show_hover`, `_hide_hover`, `_hide_hover_only`,
  `_indicator_lines_at`, `_find_indicator_panel_for_axes`,
  `_line_value_at`. Lower-pane indicator labels use the same hover
  dispatch to show a `hand2` cursor while clickable. On a non-overlay
  pane, `_indicator_lines_at` enumerates EVERY config whose
  `state.panes[cid] is ax` (so a shared RVOL pane reads out BOTH
  Cumulative and ToD values), not just the first.
- `_pane_indicator_label_hit` iterates the pane's per-config name
  artists (`ax._sc_pane_label_artists`) and returns the config of the
  name under the cursor (each carries a length-1
  `_sc_pane_label_config_ids`), so clicking a name on a shared pane opens
  THAT indicator. Falls back
  to the legacy singular `_sc_pane_label_artist` when the list is
  absent.
- Crosshair: `_update_crosshair`, `_update_crosshair_pixels`
  (revives after re-render using cached pixel coords).
- Price value label: `_format_price_for_label(ax, value)` —
  **kind-aware** branch using `self._ax_candle_map.get(ax)[1]`. For
  price axes it returns `f"{value:,.2f}"` (forced 2-decimal with
  thousands separator) so the badge stays at the user-expected
  precision even when the matplotlib formatter would otherwise
  truncate trailing zeros (e.g. `$172.50` rendering as `$172.5`).
  For volume and indicator axes it delegates to the axis's
  installed major formatter (`format_data_short` first, then
  `fmt(value, None)`, then a `f"{v:,.2f}"` fallback) so
  on-axis-tick parity is preserved. Audit
  `hover-price-2-decimals`. Animated `Text` per **price OR
  volume** axes, stored in `self._price_label_artists[ax]`.
  Anchored axes-fraction x=0 via blended transform; opaque round
  bbox occludes y-tick labels.
- Time label: `_format_time_for_label(ax, xdata)` — **one
  annotation per pane (slot)**, stored in
  `self._time_label_artists[slot_key]`, each anchored to that
  pane's bottom-most axes. `_update_crosshair` shows the badge for
  the hovered pane (`_slot_key_for_axes(current_ax)`) and hides the
  others, so in compare mode the time badge appears under the
  hovered chart, not always the globally lowest chart.
  `self._time_label_artist` is a back-compat alias onto the
  `"primary"` pane's badge (single-chart mode has only that one, on
  the figure-bottom axes — what older callers/tests read). Axes that
  resolve to no slot fall back to a single `None`-keyed badge on the
  global bottom (degenerate / pre-panel-state render). Intraday →
  `YYYY-MM-DD HH:MM` (via `formatting.format_dt` with display tz);
  daily/weekly/monthly → `YYYY-MM-DD`. Empty when xdata out of
  candle range (caller hides).
- `_blit_overlays` — composes hover + crosshair + value labels + typing
  preview on top of a cached background, using the two-layer overlay
  cache (see **Crosshair overlay cache** below).
- `_hide_overlays` — universal hide.

### Live-tick blit fast path (cluster 1)

- `_paint_tick_frame(slot) -> bool` — repaints a streaming tick via blit
  instead of a full `canvas.draw_idle()`. The forming (rightmost) bar
  shares one Collection with every sealed bar, so a naive "restore full
  background + redraw" **ghosts** when the bar's body shrinks. Instead we
  keep `_tick_blit_bg` — a snapshot of the figure with ALL data artists
  hidden (pure axes decorations) — and redraw the data on top each tick;
  the background never contained the data, so there is no ghost.
  - **Lazy seed.** When `_tick_blit_bg is None`, hide every data artist,
    set `_suspend_draw_capture=True`, issue one `canvas.draw()`,
    `copy_from_bbox` → `_tick_blit_bg`, then restore visibility. The
    suspend flag keeps `_on_draw_event` from clobbering `_blit_bg` with
    the hidden frame.
  - **Blit.** `restore_region(_tick_blit_bg)` → `draw_artist` every data
    artist → capture the buffer (decorations + data, no overlays) as the
    fresh `_blit_bg` → `_blit_overlays()` composites the always-on readout
    / crosshair and blits. Bumps the `_tick_blit_fires` counter (a
    silent-fallback regression guard).
  - Returns `False` (→ caller does `draw_idle`) on no canvas/figure, no
    data artists, or any exception (the partial snapshot is dropped).
- `_collect_tick_blit_artists() -> [(ax, artist)]` — every Collection /
  Line2D / Text on each slot's price / volume / indicator axes (candles,
  volume, indicators, reference levels, drawings, the live-price line +
  label), MINUS `_overlay_artist_ids()`, deduplicated by `id()`. The
  live-price overlay's `(line, label)` are also added explicitly.
- `_overlay_artist_ids() -> set[int]` — `id()`s of the always-on / hover
  overlays (`_crosshair_artists`, `_price_label_artists`,
  `_time_label_artists`, `_readout_artists`, `_pane_value_labels`,
  `_hover_ann`, typing preview). Excluded from the tick data set so they
  are not baked into `_blit_bg` (which would make a crosshair "stick").
- The live-price overlay is slid to the fresh price by
  `ChartApp._refresh_view_after_tick` **before** the renderer repaints, so
  the blit paints it at the new price (it would otherwise lag one tick).

### Click-to-type

`_begin_click_to_type(ax)`, `_refresh_typing_preview`,
`_commit_click_to_type`, `_cancel_click_to_type`.

**`_refresh_typing_preview` composites via the blit fast path** (audit
`typing-preview-blit`). The grey preview letters are a big `Text` artist
created with `animated=True` and rendered through `_blit_overlays`
(restore `_blit_bg` → `draw_artist` → `blit`, ~1-2 ms) — NOT a full
`canvas.draw_idle` re-raster (tens of ms, and worse the heavier the
chart). This is what made typing a ticker feel laggy after a complex
chart (e.g. a ratio + the daily-levels preset, or an intraday chart with
several indicator panes): the per-keystroke cost is now O(overlay), flat
across chart complexity, instead of O(figure). The preview artist is:
- registered in `_overlay_artist_ids()` so a stream tick during typing
  cannot bake it into `_blit_bg` (it would otherwise "stick"), and
- drawn inside `_blit_overlays` alongside the crosshair / readout / hover
  overlays.
`animated=True` keeps it out of the captured `_blit_bg`, so leaving
typing mode (`_typing_target = None`) just re-runs `_blit_overlays` to
composite a clean, preview-free frame (no full redraw). Cold start
(`_blit_bg` not yet captured) falls back to a single `draw_idle`; the
subsequent `_on_draw_event` re-composites the preview one frame later.

### Crosshair overlay cache (`crosshair-readout-cache`)

`_blit_overlays` composes in **two layers** so a moving crosshair does not
pay to re-rasterise the (expensive, indicator-heavy) top-left readout box
every frame:

- **Layer 1 — `_overlay_bg`** (cached): `_blit_bg` with the always-on
  top-left OHLCV / indicator-legend readout box(es) **plus the per-pane
  value badges** (`_pane_value_labels`, see below) baked in. The readout
  is the costly artist — its `draw_artist` cost scales with the number of
  indicators (the daily-levels preset added ~5.5 ms/frame) — yet its
  content only changes when the hovered *bar* changes, not when the
  crosshair moves within a bar. So it is drawn once and captured via
  `copy_from_bbox` → `_overlay_bg`, keyed by a fingerprint
  `_overlay_bg_fp = (_last_readout_key, <readout box visibilities>,
  <pane value-badge visibilities>)`.
- **Layer 2 — moving overlays** (per frame): crosshair v/h lines, price /
  time badges, hover tooltip, and the click-to-type preview, drawn on top
  of the restored `_overlay_bg` every frame. Cheap.

**Cache validity / invalidation.** The cache is rebuilt when
`_overlay_bg is None` or the fingerprint changes:
- A different hovered bar changes `_last_readout_key` (set in
  `_dispatch_hover` via the qw-hover-cache gate) → fingerprint flip.
- Readout hide/show (cursor-leave, revival) flips a visibility bool in the
  fingerprint.
- Any full redraw (`_on_draw_event`) or live tick (`_paint_tick_frame`)
  recaptures `_blit_bg` and **explicitly nulls `_overlay_bg`** at that
  site, so content changes that don't move the readout key (theme, resize,
  indicator add, streaming forming-bar) still rebuild.

Because the readout is bar-indexed (identical content for any cursor-x
within one bar), reusing `_overlay_bg` while the crosshair sweeps *within*
a bar or moves *vertically* is bit-identical to redrawing it. Measured:
in-bar hover with the daily-levels preset dropped from ~11.7 ms to
~5.2 ms/frame (−55 %), now flat regardless of indicator count; the
`copy_from_bbox` fires once per bar instead of once per frame. Z-order
note: the crosshair now draws **over** the readout box (it used to draw
under) where they overlap at the far-left edge — acceptable and standard
for trading crosshairs.

### Per-pane inline value labels (`pane-value-readout`)

Mirrors how the price pane's readout surfaces overlay-indicator values on
hover, extended to **every dedicated indicator pane** (`overlay=False`
indicators: RVOL, RRVOL, RSI, MACD, ADX, ATR, LRSI, SMI, overlap-score, …).
`_pane_value_labels` is a `dict[axes, list[(config_id, Text)]]` of animated
`Text` artists — **one value artist per visible config**, positioned
**inline right after that config's name label** — built in
`_ensure_overlay_artists` and refreshed by `_update_readout`:

- **Placement.** Each value sits `ha="left"` at the x recorded by
  `indicators.render._render_pane_labels` in `ax._sc_pane_value_x_by_cid`
  (right after the config's name), so the pane top reads
  `RVOL Cum(20) 1.23   RRVOL Cum(20) 1.00` — name then value, flowing left
  to right. This **replaced** the old single right-aligned combined badge,
  which collided with the left-hand names on a narrow **shared** pane
  (RVOL + RRVOL). Because value artists are keyed by config id and the name
  label already identifies each one, the value text is **just the number(s)**
  — no indicator prefix.
- **Colour.** A single-value config is coloured by its line (matches the
  pane's curve); a multi-output config uses the neutral text colour.
- **Per-config value** comes from `_pane_config_values(ax, idx) ->
  {config_id: (text, color)}` (`state.pane_lines`, `_line_value_at` per
  line). Multi-output configs (MACD's macd/signal/hist) prefix each value
  with its output key, dropping a key that merely repeats the `kind_id`.
  A config with no defined value at `idx` (warmup) is omitted → its label
  is hidden. (`_pane_indicator_readout` — the old single-string combined
  formatter — is retained for the unit tests + any legacy caller.)
- **Volume pane** → **no** labels. Volume is already shown in the price
  pane's OHLCV readout strip, so a volume-pane value would be redundant.

The gate is intentionally allowlist-free: value labels are created (in
`_ensure_overlay_artists`) for every axes whose `_ax_candle_map` kind is
`"indicator"` — which is set during rendering for any `overlay=False`
indicator. So a **new pane indicator is covered automatically**, with no
per-kind table to maintain. This universality is pinned by the
registry-driven meta-test `check_d51c_all_pane_indicators_value_badge`
(walks every `overlay=False` indicator, asserts a numeric value; asserts a
price-overlay indicator gets none).

Shared bar resolution: `_readout_bar_idx(ax, xdata)` returns the in-render-
window non-gap bar under the cursor, else the latest non-gap bar (so a
badge is never blank), else `None`. The badges are **static-per-bar**, so
they live in the `_blit_overlays` **cached layer** alongside the readout
box (their visibilities join the `_overlay_bg` fingerprint) and are in
`_overlay_artist_ids` (excluded from the tick-blit data set). Rebuilt every
render; recoloured on theme swap by `theme_controller._apply_overlay_artists`.

### Drawings bridge (Feature C)

`_pick_drawing_at_event`, `_maybe_handle_drawing_dblclick`,
`_maybe_handle_b3_click_menu`, `_update_drawing_hover_cursor`,
`_reset_drawing_hover_cursor`. Route mpl events to
`tradinglab.drawings`. All swallow exceptions and early-return
when `self._drawings` is missing. `_pick_drawing_at_event`
short-circuits when the ticker's bucket is empty (O(1)
`DrawingStore.count`) and caches last successful pick by
`(slot_key, ticker, int(x_px), int(y_px), store.revision())`.
Restricted to price axes only.

Drawings drag-to-move: `_maybe_begin_drawing_drag`,
`_drawing_drag_motion`, `_maybe_end_drawing_drag`. B1 press on a
line sets `_drawing_drag_state` and suppresses pan; motion
updates price via `store.update(id, price=snapped)` ($0.01 snap);
release commits final price via `_compute_snapped_drawing_price`
(grid snap + optional OHLC magnet). Cursor `sb_v_double_arrow`
during drag.

## Dependencies

- Internal: `..core.viewport.y_limits_for_slice`,
  `..formatting.fmt_volume`. Reads `ChartApp._panel_state[slot]`.
- External: `tkinter` (`TclError`), `numpy`,
  `matplotlib.patches.Rectangle`.

## Design Decisions

- **One mixin** — pan/zoom/hover/crosshair/click-to-type share
  `_blit_bg`, animated-artist list, pixel cache, mpl connections.
- **Pan: deferred blit setup** — `_pan_begin` does NOT call
  `_pan_setup_blit`; that runs lazily on first `_pan_drag`. Avoids
  the click-flicker from `canvas.draw()` against `animated=True`
  data artists when a press resolves to a pure click.
- **blit-based pan**: mark every data artist (Collections / Lines
  / Patches / Texts / X/Y axes) `animated=True`; `canvas.draw()`;
  `copy_from_bbox(figure.bbox)`. On drag: `restore_region(bg) +
  draw_artist(each) + blit(bbox)`. 16 ms target. ~10× faster than
  `canvas.draw_idle()`.
- **Tick labels + gridlines marked animated** — otherwise they'd
  bake into `_pan_bg` and freeze while data shifts under.
- **Initial blit on press-and-hold**: after `canvas.draw()` +
  `copy_from_bbox`, `_pan_setup_blit` immediately `draw_artist`s +
  blits once so press-and-hold without motion doesn't go blank.
- **Slice-refill via `_pan_rebind_animated_after_slice`**: when
  pan crosses the virtualized-render safe-zone, walk new artist
  topology, mark new data artists `animated=True`, refresh
  `_pan_animated` + `_pan_anim_fingerprint`, and DO NOT call
  `canvas.draw()`. Reuses existing `_pan_bg` (only static
  decorations). Caller falls through to normal blit path. Falls
  back to `_pan_setup_blit` if `_pan_bg is None`.
- **Pan-setup fingerprint reuse**: `_pan_setup_blit` hashes
  figure topology by `id()` into `_pan_anim_fingerprint` (tuple of
  ints over collections + visible lines + visible patches + visible
  texts + axes spines). On entry, matching fingerprint + non-None `_pan_bg` +
  non-empty `_pan_animated` → skip the full setup. Pan → release
  → pan saves ~30–80 ms.
- **Per-frame Y autoscale** during pan (~0.25 ms; affordable at 60 FPS).
  **qw-pan-autoscale memo**: `_pan_drag` skips `_autoscale_y_to_visible`
  when the panned axis's integer bar range `(ceil(lo_f-eps),
  floor(hi_f+eps))` is unchanged from the previous frame AND the
  virtualized render slice didn't change. The Y-fit is a pure function
  of the integer bar slice (candle data is frozen for the gesture) and
  all price axes are sharex with offset 0, so the panned axis is
  representative. Memo `_pan_last_bar_range` is reset in `_pan_begin`
  (fresh gesture → first frame recomputes) and cleared in `_pan_end`
  (whose final settle autoscale always runs unconditionally).
- **Fallback non-blit path** if `_pan_setup_blit` failed (very
  early startup): 16 ms `after()` doing `canvas.draw_idle()`.
- **Rubber-band zoom via `Rectangle` patch** — keeps custom styling.
- **Scroll-wheel zoom (`_on_scroll_zoom`)**: cursor-anchored,
  primary x-axis. Scroll DOWN zooms IN, UP zooms OUT (`factor =
  1.15 ** step` with step negated). Anchor:
  `new_lo = x - (x - lo) * factor`, `new_hi = x + (hi - x) * factor`
  so bar at `event.xdata` stays at the same pixel. Sets
  `_preserve_xlim_on_render=True` and
  `_slide_xlim_to_right_edge=False`. No-op when `event.inaxes is
  None`, when pan/zoom states are active, or `step == 0`.
  `|step|` clamped ≤ 2. Floor: 3-bar min width with cursor anchor
  preserved by recomputing `new_lo`.
- **Invert toggle**: `_on_scroll_zoom` consults
  `_scroll_zoom_invert` (from settings.json); negates `event.step`
  when True. `set_scroll_zoom_invert(bool)` persists.
- **Manual zoom / pan preserve xlim across compare toggle**:
  `_zoom_end` and `_pan_end` set `_preserve_xlim_on_render=True`
  and `_slide_xlim_to_right_edge=False` so a subsequent re-render
  (e.g. `_on_compare_toggle`) doesn't snap xlim back to data
  extent. Flag stays sticky until Reset View / source-flip /
  interval-flip / pre-post-flip clears via
  `_clear_drilldown_state`.
- **Pan-end blit-bg invalidation**: `_pan_end` clears `_blit_bg
  = None` so a candle-less snapshot captured by `_on_draw_event`
  during `_pan_setup_blit`'s `canvas.draw()` (with data artists
  `animated=True`) can't be restored by the next hover.
- **Compare-toggle ylim safety net**: `_on_compare_toggle` calls
  `self._autoscale_y_to_visible()` after `_render()` in all three
  branches (defense-in-depth against stale ylim).
- **Dynamic ratio rebase-to-100 re-anchor**: `_autoscale_y_to_visible`
  first calls `self._apply_dynamic_ratio_rebase()` (the universal
  view-change hook) so a ratio chart's 100-index re-anchors to the
  leftmost visible bar on every zoom / pan-end / drilldown. Skipped
  while `_pan_state` is active (re-bakes once on release). **Live
  during a pan drag** the y-axis instead relabels via the
  `_ratio_rebase_y_scale` tick formatter — `_pan_setup_blit` marks
  `ax.yaxis` an animated artist, so it's redrawn every blit frame and
  the left edge reads 100 with no snap on release. No-op for non-ratio
  / rebase-off charts. See `app.spec.md` → ratio rebase.
- **Click-to-type detection**: `< 3 px` (squared distance, no sqrt) +
  same axes. Target = clicked axes's slot; keystrokes with nothing
  clicked default to `_last_clicked_slot or "primary"`.
- **Typing preview** = grey translucent `Text` (fontsize 56,
  alpha 0.55), centered on the clicked price axes; rebuilt per
  keystroke (cheap at ~5/sec).
- **Hover annotation reparent** lazily per-axes (`remove()` + re-
  `annotate` on the new ax) so `draw_artist` hits the right axes.
- **Hover position flipping**: `rx >= 0.8 → right`, `ry >= 0.6 →
  top` (prevent tooltip running off canvas edges).
- **Crosshair**: vertical on every axes; horizontal only on the
  hovered axes (TradingView convention).
- **Crosshair revival** (`_update_crosshair_pixels`): inverse-
  transform cached pixel coords back to data after `figure.clear()`
  destroys the artists, then redraw.
- **Hit test x-tolerance**: 0.3 bars (matches body width 0.6).
  Hover short-circuits on `is_gap`, out-of-render-range, and
  out-of-Y-envelope (keeps crosshair, hides hover).
- **Always-on OHLCV / %change readout + in-readout overlay legend**:
  one `AnchoredOffsetbox` per `kind == "price"` axes (stored in
  `_readout_artists[ax]`). Its child is a `VPacker` stacking the OHLCV
  `HPacker` (row 0) over one transparent `TextArea` per overlay legend
  row (rows 1..N). The OHLCV `HPacker` holds two `TextArea` children
  (`_main_text` neutral O/H/L/C/Vol; `_pct_text` bull/bear-coloured pct).
  The pct color reads `constants.BULL_COLOR`/`BEAR_COLOR` **live** (via
  `_constants.*` at paint time) so it follows the Okabe-Ito color-blind
  palette toggle. Audit `color-blind-palette-audit`.
  Anchored axes-fraction `(0, 1)`, `frameon=False`, `animated=True`,
  `zorder=11`. `_update_readout(xdata)` from `_dispatch_hover`,
  `_hide_overlays`, and once at `_ensure_overlay_artists`. Falls back to
  latest non-gap bar inside rendered window so the strip is never blank.
  Pct color via `box._pct_text._text.set_color(...)` (`TextArea` has no
  public `set_color`).
  - **qw-hover-cache**: `_dispatch_hover` only calls `_update_readout`
    when the cursor's bar changed. It keys on `(id(candles), ro_idx)`
    where `ro_idx = round(xdata - offset)` (offset is 0 on every axes,
    so the same index applies to all panes), and gates on
    `ro_idx < len(candles) - 1` — sealed bars are immutable so the OHLCV
    + %chg + per-indicator value strings are byte-identical, while the
    forming/last bar (which streams in place) is never cached. The memo
    `_last_readout_key` is reset whenever `_update_readout(None)` runs
    (post-render artist rebuild / streaming revival / cursor-left) so the
    next in-bar hover repaints against fresh data. The crosshair updates
    every event regardless — only the string churn is skipped.
  - **Overlay legend rows** (TradingView-style; replaces the retired Tk
    `OverlayLegend` pill). Built by `_build_readout_indicator_rows(ax,
    theme)` which enumerates via the pure
    `gui.readout_legend.build_overlay_legend_rows`. As of the
    `legend-condensation` sprint each row is an **`HPacker` of
    `TextArea`s** representing ONE indicator config — multi-output
    indicators (Bollinger / AVWAP-with-bands / Keltner / Donchian)
    render as `LABEL upper <v1> middle <v2> lower <v3>` on a single
    visual row with each band's value in its own colour. Row meta on
    `box._ind_rows` is now
    `{"config_id", "label", "label_textarea": TextArea, "visible",
    "container": HPacker,
    "outputs": [{"output_key", "color", "line", "value_textarea",
    "key_label", "notset"}, ...]}` where `outputs` enumerates the visible bands
    in indicator-declared top-down order (via
    `Indicator.effective_output_keys(params)`). `label_textarea` is the
    "NAME(params) " prefix artist, stashed so a live theme swap can recolor
    just the indicator name in place — see
    `theme_controller._apply_overlay_artists` ("Live theme swap" below).
    `_update_readout`
    walks `outputs` per row and writes `_line_value_at(line, idx)` into
    each segment's `value_textarea` (visible rows) or leaves the
    placeholder + greyed label (hidden rows / hidden bands).
    **AVWAP "Not set":** when a row is an `avwap` config whose effective
    anchor for THIS slot's symbol is empty (resolved via
    `indicators.avwap.resolve_anchor_ts(cfg.params, slot_symbol)`), the
    `notset` flag is set on every output meta; the value `TextArea` then
    reads `"Not set"` (both the initial seed and on every
    `_update_readout`) instead of a blank — an unanchored AVWAP draws no
    line, so there is no value to show. The slot symbol comes from
    `_slot_symbol(slot_key)`. Slot→scope
    via `_READOUT_SCOPE_FOR_SLOT` (`primary`→`main`,
    `compare`→`compare`). Transparent background (no overlap with the
    OHLCV strip). Click routing: see `_maybe_handle_readout_legend_click`
    / `_readout_legend_row_hit` above.
  - **Live theme swap.** The OHLCV `_main_text` AND every overlay legend
    row's name/segments bake their colour at build time, so a light↔dark
    toggle must recolor them in place. `theme_controller._apply_overlay_artists`
    iterates `box._ind_rows`: a visible row recolors its `label_textarea`
    to `theme["text"]`; a hidden row recolors every child `TextArea` to the
    muted colour. Without this the indicator names stay their old colour
    (e.g. black after switching to dark) until the next full `_render`
    (the reported bug, where opening "Manage Indicators" was the re-render
    trigger). The recolor lands before `ThemeController.apply`'s trailing
    `draw_idle` → `_on_draw_event` → `_blit_overlays` re-composite.
- **`_last_hovered_slot`**: tracks last axes' slot
  ("primary"/"compare"); persists across Notebook tab switches
  so watchlist double-click + click-to-type route to the last
  panel.

## Invariants

- `_blit_bg` invalidated by `_render` and `_draw_slice` (set
  `None`); next `draw_event` re-captures.
- `_overlay_bg` (the cached base+readout layer) is nulled at every
  `_blit_bg` recapture site (`_on_draw_event`, `_paint_tick_frame`) and
  otherwise rebuilt lazily when its fingerprint
  `(_last_readout_key, readout visibilities)` changes. It is never stale:
  a content change either flips the fingerprint or nulls the cache.
- Pan: before drag, all data artists `animated=True`; after
  `_pan_end`, all `animated=False`.
- Hover and crosshair never render on top of a stale background;
  `_blit_overlays` always `restore_region` first (of `_overlay_bg` on the
  fast path, or `_blit_bg` when (re)building the cache).
- Crosshair vline on every axes when any cursor position is
  known; hline only on `_crosshair_current_ax`.
- Clicking on chart gives canvas widget keyboard focus.

## Data Flow / Algorithm

Pan-drag frame:

```
_pan_drag(event):
    update xlim by pixel delta
    prev_ranges = snapshot each slot's (render_start, render_end)
    for slot: _ensure_rendered_for_view(slot)
    cur_bar_range = (ceil(lo_f-eps), floor(hi_f+eps)) of panned ax
    if slice_changed or cur_bar_range != _pan_last_bar_range:  # qw-pan-autoscale
        autoscale_y; _pan_last_bar_range = cur_bar_range
    if any range changed: _pan_rebind_animated_after_slice()  # NO canvas.draw
    restore_region(_pan_bg); draw_artist(each animated); blit(figure.bbox)
```

Hover dispatch:

```
_dispatch_hover(event):
    look up ax → (candles, kind, offset)
    find slot; get (rs, re_)
    if xdata/ydata None: hide hover; keep crosshair
    idx = round(xdata - offset)
    if idx out of [rs, re_) or is_gap or |xdata - (idx+off)| > 0.3:
        hide hover, keep crosshair
    Y hit test: price → low <= y <= high; volume → 0 <= y <= volume
    if hit: _update_crosshair + _show_hover
```

## Known limitations

- Zoom doesn't use blit (only pan does). Rubber-band + scroll-wheel
  invoke full redraws; fine because brief.
- No pinch / touch support.
