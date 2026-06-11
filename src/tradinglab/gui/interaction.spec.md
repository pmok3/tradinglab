# gui/interaction.py — Spec

## Purpose

`InteractionMixin` — the entire interactive chart subsystem: pan,
zoom (rubber-band + scroll-wheel), hover tooltip, crosshair,
click-to-type ticker entry, and the drawings hit-test bridge.
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
  `_typing_buffer` (alnum + `._-` only; digits ignored to avoid
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
  `_sc_pane_label_config_ids`; `•` spacers carry `()` and are skipped),
  so clicking a name on a shared pane opens THAT indicator. Falls back
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
- `_blit_overlays` — composes hover + crosshair + value label on
  top of `_blit_bg`.
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
  `_time_label_artists`, `_readout_artists`, `_hover_ann`). Excluded from
  the tick data set so they are not baked into `_blit_bg` (which would
  make a crosshair "stick").
- The live-price overlay is slid to the fresh price by
  `ChartApp._refresh_view_after_tick` **before** the renderer repaints, so
  the blit paints it at the new price (it would otherwise lag one tick).

### Click-to-type

`_begin_click_to_type(ax)`, `_refresh_typing_preview`,
`_commit_click_to_type`, `_cancel_click_to_type`.

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
    `{"config_id", "label", "visible", "container": HPacker,
    "outputs": [{"output_key", "color", "line", "value_textarea",
    "key_label", "notset"}, ...]}` where `outputs` enumerates the visible bands
    in indicator-declared top-down order (via
    `Indicator.effective_output_keys(params)`). `_update_readout`
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
- **`_last_hovered_slot`**: tracks last axes' slot
  ("primary"/"compare"); persists across Notebook tab switches
  so watchlist double-click + click-to-type route to the last
  panel.

## Invariants

- `_blit_bg` invalidated by `_render` and `_draw_slice` (set
  `None`); next `draw_event` re-captures.
- Pan: before drag, all data artists `animated=True`; after
  `_pan_end`, all `animated=False`.
- Hover and crosshair never render on top of a stale background;
  `_blit_overlays` always `restore_region(_blit_bg)` first.
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
