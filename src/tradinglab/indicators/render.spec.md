# indicators/render.py — Spec

## Purpose
Render-side bridge between the pure-compute indicator stack ([`base`](base.spec.md), [`moving_averages`](moving_averages.spec.md), [`rsi`](rsi.spec.md), bollinger) and the matplotlib figure. Computes (cached) values, materialises `Line2D` artists onto the slot's price axis (overlays) and per-config lower panes (non-overlays), and exposes a state object the app walks during fast paths (pan/zoom blit, streaming tick, theme swap).

## Public API
- `factory_by_kind_id(kind_id)` — convenience wrapper around `base.factory_by_kind_id` returning just the factory class (or `None`).
- `compute_layout(...)` — figure out which configs produce overlays vs non-overlay lower panes for a given slot.
- `class PanelIndicatorState` — dataclass: per-slot artist registry the app walks during blit / theme swap.
- `applicable_overlay_configs(manager, scope) -> list[IndicatorConfig]` and `applicable_non_overlay_configs(...)` — filter the manager's configs for a slot's scope.
- `render_for_slot(...)` — top-level call from `_render`: runs compute → builds artists on price + lower axes → returns a `PanelIndicatorState`.
- `autoscale_pane_y(ax_lower, lines, lo, hi)` — Y-autoscale a non-overlay pane to its visible window. Reads `_sc_y_data` off non-Line2D artists (e.g. histogram `LineCollection`) so they participate in autoscale the same way `Line2D.get_ydata()` does for line outputs.
- `_draw_histogram(...)` — private helper that materialises a 4-color histogram `LineCollection` for an indicator output declared with `output_kinds[key] == "histogram"`. Diff path supports `set_segments` + `set_colors` for in-place updates so streaming ticks don't reallocate the collection.

## Dependencies
- Internal: `..models.Candle`, `.base.factory_by_kind_id`, `IndicatorConfig` / `IndicatorManager` / `IndicatorCache` (consumed; not constructed here).
- External: `numpy`, `matplotlib` (Line2D + Axes — duck-typed).

## Design Decisions
- **Tk-thread / matplotlib-coupled by design**. Pure compute (NaN-correct, no Tk imports) lives in the `base` / kind-specific modules. Render is the only place where artists are created.
- **Gap-aware via `gap_mask`**: when a slot's candles list has been gap-padded for compare-mode alignment, the helper computes on the **non-gap subset** and NaN-pads the result back to the full length so x positions line up with the rendered candles. Without this, indicators would visibly drift across compare gaps.
- **Style resolution**: `_resolve_style(cfg, output_key)` reads a config's per-output style (e.g. RSI's `rsi`/`upper`/`lower` lines, Bollinger's `mid`/`upper`/`lower`). Falls back to factory defaults when the config doesn't override.
- **Wraps `base.factory_by_kind_id`'s `(display_name, factory)` tuple shape**: the `(name, factory)` tuple is right for menu/dialog code; render only needs the class. `_safe_remove_line` swallows `ValueError` because matplotlib raises when an artist has already been detached (theme swap + clear race).
- **`PanelIndicatorState` is the contract surface for fast paths**: blit code walks the state's `Line2D` lists with `set_animated(True)` so pan / zoom / streaming-tick redraws don't trigger a full figure rebuild.
- **Overlay zorder follows manager-list position (b43)**: each overlay's `Line2D` is created (or restamped via `set_zorder`) with `zorder = 4 + 0.01 * i`, where `i` is the config's index in `applicable_overlay_configs(...)`. This makes `IndicatorManager.reorder` actually reflow the visual stacking of overlapping overlay lines on the price axis. A pure constant `zorder=4` left late-added lines stranded on top because matplotlib's `axes.lines` insertion order — not the config order — was deciding the draw order, and we deliberately reuse `Line2D` artists across renders (keyed by `cfg.id`) to keep blit fast. Lower-pane order naturally tracks manager order via the figure-level gridspec rebuild on `_render`, so no equivalent zorder trick is needed for panes.
- **Reference-level rendering for pane indicators** — `_resolve_reference_levels(cfg, factory)` consults the instance's `reference_levels` first (built from `cfg.params`), then the class attribute, then an empty tuple. Levels are drawn via `ax_lower.axhline` and tracked through axis attributes `_sc_ref_levels_drawn` (the most-recent levels tuple) and `_sc_ref_level_lines` (the resulting `Line2D` artists), so a config-edit on the same axis tears down stale lines and draws the new ones without waiting for a full figure rebuild. SMI uses class-level ±40 / 0 by default; LRSI exposes per-instance levels driven by `oversold` / `overbought` / `show_reference_lines`; ADX uses class-level 25; MACD uses class-level 0.
- **Per-output render-kind dispatch (b71)** — indicators may declare an optional `output_kinds: Mapping[str, str]` ClassVar mapping each output key to a render kind. Recognized kinds:
  - `"line"` — the default, drawn as a `Line2D` on the pane axis. Used for every pre-MACD indicator with no `output_kinds` declared.
  - `"histogram"` — drawn as a `matplotlib.collections.LineCollection` of vertical segments `[(x_i, 0) → (x_i, value_i)]` with per-segment colors chosen from the indicator's `histogram_palette` ClassVar via a classifier (see MACD spec). The raw y array is pinned on the collection as `_sc_y_data` so `autoscale_pane_y` and the hover readout can access it like `Line2D.get_ydata()`. The same `_safe_remove_line` path (just calls `.remove()`) handles `LineCollection` teardown.
  - `"stair_line"` *(b72)* — same `Line2D` material as `"line"`, but with `drawstyle="steps-post"` so the line holds its value flat across each bar and visibly jumps to the next level at the bar boundary. Used by Chandelier Stops to make ratchet events visually unmistakable. Diff path tracks `get_drawstyle()` and live-flips between `"default"` and `"steps-post"` if the user swaps render kinds. **Both overlay AND non-overlay loops honour stair_line** (chandelier is a price overlay; histograms are pane-only by convention).
  Indicators without `output_kinds` stay on the original Line2D path with no per-indicator change. The dispatch happens inside `render_for_slot`'s overlay loop AND non-overlay loop — overlays may now be stair-step (b72) in addition to the default line; histograms remain pane-only.

## Invariants
- Compute output is the same length as the input candles list (NaN-padded across gaps).
- `render_for_slot` never raises on an unknown `kind_id` — unknown configs are skipped silently (the indicator dialog already presents them as "Unknown indicator (…)" read-only rows).
- Artists created here are owned by the returned `PanelIndicatorState`; the caller is responsible for `set_animated` and removal on full rebuild.
