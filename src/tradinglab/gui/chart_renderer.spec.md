# `gui/chart_renderer.py` — Spec

## Purpose
Own TradingLab's panel rendering state and the helper methods that mutate candle, volume, indicator, event, and blit-related artists without taking over full render orchestration from `ChartApp`.

## State
- `panel_state` — per-slot axes, artist, candle, and indicator registry.
- `ax_candle_map` — live axes → candle/kind/offset mapping used by hover/theme code.
- `blit_bg` — background cache invalidated whenever a slice rebuild changes topology.

## Public API
- `class ChartRenderer`
  - `ChartRenderer()` — initializes empty render state.
  - `reset_slot_artists(slot)` — tear down cached artists for one slot.
  - `display_candles_for(candles, *, ha_on)` — optional HA projection for glyph drawing.
  - `key_bar_hollow_indices_for(candles, *, highlight_key_bars_on)` — derive hollow-bar indices.
  - `ha_flat_overlay_for(candles, *, highlight_ha_flat_on, ha_on, dark_mode)` — derive HA-flat hatch metadata only when HA mode and the flat-highlight toggle are both on. Hatch colours derive from the **live** `constants.BULL_COLOR` / `BEAR_COLOR` (module imports `from .. import constants as _constants` and reads `_constants.BULL_COLOR` at call time, NOT a value-binding `from ..constants import BULL_COLOR`) so the Okabe-Ito palette toggle re-colours the hatch without a relaunch. Audit `color-blind-palette`.
  - `repaint_visible_slot_glyphs(...)` — redraw existing slot slices without rebuilding topology.
  - `autoscale_slot_y(...)`, `ensure_rendered_for_view(...)` — viewport maintenance helpers.
  - `apply_tick_to_artists(...)`, `refresh_view_after_tick(...)`, `refresh_view_after_append(...)` — streaming fast paths.
  - `render_indicators_for_slot(...)`, `autoscale_indicator_panes_for_slot(slot)` — indicator delegation + pane scaling.
  - `render_event_glyphs_for_slot(...)`, `render_volume_tod_for_slot(...)` — overlay delegation helpers.
  - `suppress_default_volume_fill(slot, suppress_indices)` — mutate volume-bar colors for the ToD overlay.

## Design Decisions
- `ChartApp._render()` and `ChartApp._draw_slice()` stay in `app.py`; they orchestrate topology and pass app-owned services/flags into `ChartRenderer`.
- Renderer methods accept explicit flags/callbacks instead of reaching into Tk vars or app services directly.
- `panel_state` and `ax_candle_map` are mutated in place so legacy aliases in `ChartApp` and mixins stay live.
- `blit_bg` remains renderer-owned, with `ChartApp` exposing a compatibility property that proxies reads/writes.

## Invariants
- Methods are fail-soft: rendering overlays and derived computations must never abort a paint.
- Artist teardown is idempotent.
- Streaming fast paths only mutate in place when the visible slice and cached artist metadata still line up.
- Indicator pane autoscale always operates on the visible x-window, not the full history.

## Testing
- Covered by the existing unit suite that exercises render, stream, indicator, and overlay code through `ChartApp`.
- Ruff checks this module directly for import/logic regressions.
