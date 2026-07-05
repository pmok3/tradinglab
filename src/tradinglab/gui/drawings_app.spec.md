# gui/drawings_app.py — Spec

## Purpose

`DrawingsAppMixin` extracted from `ChartApp`. Owns the chart-side
horizontal-line drawing concern:

1. **Store subscription** — coalesce `DrawingStore` events into a
   single drawings-only repaint per Tk idle slot, with session-sticky
   color tracking.
2. **Fast-path repaint** — swap drawing artists without rebuilding
   candles, indicators, volume, or overlays.
3. **Overlay reattach** — called from inside `_render` to add
   drawing artists to every freshly-built price slot.
4. **Alt+H placement** — snap-to-OHLC helpers and the
   `_open_drawing_dialog` singleton-per-`drawing.id` opener.
5. **Right-click menus** — chart canvas + per-drawing context menus.

## Public API

### `DrawingsAppMixin` methods (bound on `ChartApp`)

- `_on_drawing_event(event_kind, _ticker, _drawing)` —
  `DrawingStore` subscriber. Coalesces to a single
  `_repaint_drawings_only` per Tk idle. Falls back to
  `_render` on failure. Refreshes `_last_drawing_color`
  on `update` events.
- `_on_drawing_save_error(exc)` — surfaces drawings.json save
  failures into the status bar, throttled to one user-visible
  message per 10s.
- `_friendly_oserror(exc)` — static helper rendering an
  `OSError` as a one-line user message.
- `_redraw_drawings_overlay()` — attach horizontal-line artists
  to every price slot from the panel-state dict. Called from
  inside `_render` after the per-slot `_draw_slice` loop.
- `_repaint_drawings_only()` — fast-path repaint that touches
  drawing artists only. Raises `RuntimeError` if no canvas is
  mounted (signal to the caller to fall back to `_render`).
- `_open_drawing_dialog(drawing_id)` — singleton-per-id dialog
  opener; lifts an existing dialog instead of opening a second.
- `_on_alt_h_placement(event)` — Alt+H keyboard placement of a
  new drawing at the snapped cursor price.
- `_resolve_cursor_px_fallback()` — pixel-coordinate
  fallback when the matplotlib event has no `xdata` / `ydata`.
- `_compute_snapped_drawing_price(ax, slot_key, y_data, y_pixel)` — snap the
  cursor's data-coord to the nearest visible OHLC level if
  within `_DRAWINGS_SNAP_PIXEL_THRESHOLD` pixels.
- `_collect_visible_ohlc_for_slot(slot_key)` — gather the
  visible candles' OHLC levels for snap evaluation.
- `_show_chart_canvas_menu(slot_key, event, x_root, y_root)` — right-click menu on the
  chart canvas background (snapshot / drawings clear / etc.).
- `_show_drawing_context_menu(drawing_id, x_root, y_root)` — right-click
  menu on a drawing artist (edit / delete / change color).

### Module-level constants

- `_DRAWINGS_SNAP_PIXEL_THRESHOLD = 8.0` — pixel distance under
  which the snap-to-OHLC helper engages.

## Dependencies

- Internal: `..drawings.render.render_drawings` (re-exported as
  `_render_drawings`), `..formatting.format_dt`,
  `..status.StatusLog` (type-only).
- External: `math`, `time`, `tkinter`, `typing.Any`.
- `_repaint_drawings_only` imports
  `..drawings.render.clear_drawing_artists` lazily so the
  fast-path doesn't widen this module's top-level import graph.

## Design Decisions

- **No `__init__` on the mixin.** All state initialised by
  `ChartApp.__init__`: `_drawings` (DrawingStore),
  `_drawing_redraw_pending`, `_last_drawing_color`,
  `_drawing_save_error_last_ts`, `_panel_state`, `_canvas`,
  `_figure`, `_status`, `_drawing_dialogs`, `_dialog_mgr`.
- **Coalesced repaint.** The store can fire many events per
  drag (~5 Hz from the dialog slider); collapsing into one
  `after_idle` repaint keeps the canvas responsive.
- **Drawings-only fast-path** (`_repaint_drawings_only`) trades
  a tiny risk of artist-leak (`_clear_drawings` removes by gid)
  for ~100× fewer artist rebuilds vs the full `_render`.
- **Session-sticky color** lives on `_last_drawing_color` so a
  user drawing five red lines in a row never re-picks red.

## Invariants

- `_on_drawing_event` is idempotent w.r.t. `_drawing_redraw_pending`
  — re-entry while pending is a no-op.
- `_repaint_drawings_only` raises `RuntimeError("no canvas mounted")`
  iff there is no `_canvas`; this is the documented signal for
  callers to fall back to `_render`.
- `_on_drawing_save_error` never raises (status pipeline failure
  during teardown is swallowed).
- `_friendly_oserror` always returns a non-empty string of ≤160
  chars.
