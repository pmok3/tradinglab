# gui/live_price_overlay_app.py — Spec

## Purpose

`LivePriceOverlayAppMixin` extracted from `ChartApp`. Holds the
ChartApp-side glue for the TradingView-style dotted live-price line:

1. **Decide *when*** to redraw the overlay (full repaint, called
   from end of `_render`) vs in-place update (called from
   `_refresh_view_after_tick` after stream-tick mutation).
2. **Decide *for which slot*** by walking `_panel_state`.
3. **Resolve the freshest price** for that slot's symbol via the
   `gui.live_price_overlay.resolve_price` helper (stream tick first,
   candle close fallback).

The overlay math itself stays in `gui/live_price_overlay.py`; this
mixin is purely the wiring.

## Public API

### `LivePriceOverlayAppMixin` methods (bound on `ChartApp`)

- `_redraw_live_price_overlay()` — full rebuild. For every price
  slot in `_panel_state`, asks the overlay helper to draw a
  horizontal dotted line + right-edge label at zorder 3.
  Always-on per design; slots with no resolvable price are
  silently skipped.
- `_update_live_price_overlay_for_slot(slot)` — in-place mutation
  of the line for a single slot. Pokes the overlay's per-slot
  artist. No-op if the overlay has never been redrawn (e.g.
  before the first `_render`). `ChartApp._refresh_view_after_tick`
  calls this **before** delegating to the renderer's
  `refresh_view_after_tick`, so the live-tick blit fast path
  (`gui/interaction.py:_paint_tick_frame`, which repaints inside that
  delegated call) paints the line at the fresh price instead of lagging
  one tick. The resolved price equals the close the tick is about to
  write, so updating first introduces no staleness.

## Dependencies

- Internal (lazy import inside both methods): `.gui.live_price_overlay
  .resolve_price` — kept lazy to avoid pulling matplotlib into the
  mixin's module-level import graph.
- External: `logging` (module-level `logger`).

## Design Decisions

- **No `__init__` on the mixin.** All state initialised by
  `ChartApp.__init__`: `_live_price_overlay`, `_panel_state`,
  `_last_stream_price`.
- **Lazy `resolve_price` import** keeps the mixin's top-level
  imports minimal — matplotlib is reached only when a price needs
  resolving.
- **Per-slot try/except** so a render error on the compare pane
  cannot blank out the primary's live-price line.
- **Update path uses its own logger** so a recurring overlay
  failure is debuggable without producing dialog noise.

## Invariants

- `_redraw_live_price_overlay` is safe to call when
  `_live_price_overlay` is `None` (returns silently).
- `_update_live_price_overlay_for_slot(slot)` is safe to call
  before the first `_render` (overlay no-ops).
- Neither method raises out of the mixin — all exceptions are
  swallowed (and the failed update logged for the in-place path).
