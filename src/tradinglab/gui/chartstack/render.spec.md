# `chartstack/render.py` — Per-card matplotlib drawing

## Purpose
Single source of truth for what gets painted on a card's `Axes`. Per the
2026-05-16 candles-only simplification, cards render miniature daily OHLC
candlesticks only. The earlier M4 visual stack (volume-stroke sparkline,
VWAP, PMH/PML horizontals, pre/post wash, last-3-bars overlay, halted-symbol
treatment) has been retired.

## Public API
- `draw_card_placeholder(ax, binding, *, theme=None)` — clear + draw the
  symbol centred in 14-pt text; strip ticks and spines. Used for empty
  slots, single-bar slots, and cards whose fetch is in flight or returned
  no bars. `binding=None` renders `"(empty)"` so a regressed binding
  pipeline is visually obvious. `theme` (optional palette with `text` /
  `ax_bg` keys) is applied so dark-mode colors survive `ax.clear()`;
  omitted by headless render tests.
- `draw_card_candles(ax, bars, *, binding=None, tint=None, theme=None,
  **_ignored_legacy_kwargs)` — main entry. Clears the axes, draws OHLC
  candles plus a header row (symbol top-left; last + %chg-vs-prior-close
  top-right). `tint` paints a colored border via `apply_card_tint`. Falls
  through to `draw_card_placeholder` on `len(bars) < 2` (still honours
  `tint` and `theme`). `theme` repaints the axes face and the LEFT-aligned
  symbol; the RIGHT-aligned last+%chg keeps its direction-encoded
  bull/bear/flat color so sentiment encoding survives.
- `draw_card_sparkline` — back-compat alias for `draw_card_candles`. Legacy
  kwargs (`show_vwap`, `show_pmh_pml`, `show_last_candles`,
  `volume_stroke_encoding`, `halted_at`) are accepted and silently ignored
  via `**_ignored_legacy_kwargs`.
- `apply_card_tint(ax, color)` — toggles axes spines on with `color`
  (linewidth 1.6); `color=None` hides them. Idempotent. Driven by the M6
  alert engine via the `tint` kwarg.

## Visual composition (zorder, back to front)
1. Candles — wicks (`LineCollection`, zorder=3), bodies
   (`PatchCollection` of `Rectangle`, zorder=4).
2. Header row text — symbol left, last + %chg right (zorder=10).
3. Optional spine tint (orthogonal to artists).

A doji bar (open == close) draws a floor-height sliver so it
remains visible. Body width spans 70 % of the per-bar x-step;
falls back to a minimum of `0.3` to stay visible on a 2-bar card.

## Color tokens
- Bull / bear candle colors are read **live** from
  `constants.BULL_COLOR` / `BEAR_COLOR` inside `_direction_color`
  (module imports `from ... import constants as _constants`), so the
  cards mirror the main chart — including the Okabe-Ito color-blind
  palette toggle (`ChartApp.set_use_colorblind_palette` →
  `ChartStackPanel.refresh_palette()`). Audit `color-blind-palette`.
- `_FLAT_COLOR = "#6b7280"` — neutral grey for doji (no palette
  variant; bull/bear carry the directional meaning).

## Drawing helpers
- `_direction_color(open, close)` — bull / bear / flat hex string;
  bull/bear resolve `constants.BULL_COLOR` / `BEAR_COLOR` live.
- `_draw_candles(ax, bars, xs)` — single `LineCollection` for wicks +
  single `PatchCollection` for bodies (~3 ms / 60-bar card vs ~60 ms for a
  per-bar `ax.plot` + `add_patch` loop). Lazy `matplotlib.collections` /
  `.patches` imports keep the module test-importable with no display
  backend.

## Settings keys (deprecated)
`chartstack.show_vwap`, `chartstack.show_pmh_pml`,
`chartstack.show_last_candles`, `chartstack.volume_stroke_encoding` remain
in `settings_adapter.DEFAULTS` for back-compat but the renderer ignores
them. Cleanup is deferred to the next settings-dialog touch.

## Design decisions
- Candles-only: simpler, faster, and trader feedback called the M4 stack
  visual noise at 220-px thumbnail width.
- `ax.clear()` every draw — refresh cycles mustn't leave stale artists.
- `(empty)` for `binding=None` so a regressed binding pipeline is visible.
- Y-range = candle hi/lo + 8 % padding — keeps wicks inside the panel and
  avoids the 0-clipping that a `.plot()` autoscale would produce.
- Tint via axes spines (not a Tk frame) — cards share one Figure with no
  per-card Tk container; spine toggling composes correctly with blit.
- `draw_card_sparkline` alias + `**_ignored_legacy_kwargs` keeps the
  simplification surgical (no panel-side rewrite).
- Lazy matplotlib imports in `_draw_candles` keep the module
  test-importable.

## Testing
Smoke coverage via the ChartStack panel tests: candle bodies/wicks visible
after fetch lands, placeholder renders on empty/single-bar input, tint
clears cleanly on rebind.

