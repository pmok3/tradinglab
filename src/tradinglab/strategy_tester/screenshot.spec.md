# `strategy_tester/screenshot.py`

Headless per-trade screenshot rendering for the Strategy Tester
Report. One PNG per closed trade, composed via the same
`tradinglab.rendering` primitives as the live chart so visual parity
holds.

## Public surface
- `ScreenshotSpec` — knob bag with defaults: `pre_bars=30`,
  `post_bars=10`, `max_bars=200`, `width_in=14.5`, `height_in=8.2`,
  `dpi=110`, `dark_mode=False`, `draw_volume_pane=True`.
- `IndicatorOverlayCache` — immutable bundle of precomputed overlay
  lines for one candle series / strategy pair.
- `CandleTimestampIndex` — immutable sorted lookup of non-gap candle
  timestamps to original candle indices for one candle series.
- `build_candle_timestamp_index(candles) -> CandleTimestampIndex` —
  precomputes timestamp lookup data once for a batch of screenshots.
- `build_indicator_overlay_cache(candles, entry_strategy,
  exit_strategy) -> IndicatorOverlayCache` — walks the strategy
  conditions and computes every drawable price-overlay indicator once.
- `render_trade_screenshot(*, candles, trade_row, output_path,
  spec=None, entry_strategy=None, exit_strategy=None,
  indicator_overlay_cache=None, timestamp_index=None) -> Path`
  — renders one PNG to disk; returns the actual `Path`. When
  `entry_strategy` / `exit_strategy` are supplied, every distinct
  price-overlay indicator referenced by their condition tree(s) is
  drawn on the price pane (see "Indicator overlays" below). When a
  precomputed `indicator_overlay_cache` is supplied, render uses it
  directly and does not re-walk or re-compute strategy indicators.
  When a precomputed `timestamp_index` is supplied, render uses it for
  entry/exit candle lookup instead of rebuilding the lookup.
- `select_window(candles, entry_index, exit_index, *, pre_bars,
  post_bars, max_bars) -> (start, end)` — pure-function window
  selection; exported for unit-testability.
- `trade_filename(symbol, order_id) -> str` — canonical filename
  `<SYM>_<order_id>_post.png`; falls back to `unknown` for missing
  `order_id`. Suffix matches `write_trade_rows_csv` so screenshots
  travel with exported CSVs.

## Window contract
- Start = `max(0, entry_index - pre_bars)`.
- End (exclusive) = `min(len(candles), exit_index + 1 + post_bars)`.
- If the resulting window exceeds `max_bars`, clip **from the left**
  so the exit + post-bar context is preserved. Long-running trades
  may therefore see no pre-entry runway.
- Always contains both `entry_index` and `exit_index` clamped into
  `[0, len(candles))`.

## Visual parity rules
- Composes the same `rendering.draw_candlesticks` /
  `rendering.draw_volume` / `rendering.setup_price_axes` /
  `rendering.setup_volume_axes` / `rendering.style_axes` primitives
  the live chart calls in `app.py`. Body half-widths come from
  `rendering.dynamic_body_half` against the visible-bar count.
- Light-mode + dark-mode palettes mirror the live chart's default
  themes; selecting `dark_mode=True` swaps a single theme dict.
- No pyplot, no Tk: constructed via `matplotlib.figure.Figure(...)`
  + `FigureCanvasAgg(fig).print_png(...)` so this module is safe to
  call from worker threads inside `runner.py`.
- Y-axis is framed with an 8% headroom pad above the highest high /
  below the lowest low so annotations don't overlap the spine.
- **Price + volume panes touch** (`gridspec.add_gridspec(..., hspace=0)`)
  matching the live chart's contiguous layout. `setup_price_axes` /
  `setup_volume_axes` already prune the bottom-most price tick AND
  top-most volume tick so the shared boundary doesn't show colliding
  tick labels (see `rendering.spec.md` audit `volume-axis-prune-both`).
  Previously a `hspace=0.04` gap was visible — fixed in audit
  `screenshot-pane-gap` after user-reported regression vs the live UI.
  Regression test in `test_screenshot_ux.py::test_screenshot_gridspec_hspace_is_zero`.

## Annotation contract
- **Entry**: triangle marker, `ENTRY_LONG_COLOR` up-triangle for long,
  `ENTRY_SHORT_COLOR` down-triangle for short. These (plus `MAE_COLOR`,
  `MFE_COLOR`, `ENTRY_GUIDE_COLOR`) route through
  `constants.sentiment_recolor` so the directional markers follow the
  Okabe-Ito color-blind palette (orange = long/bull, blue = short/bear)
  instead of being locked to green/red; the neutral `EXIT_COLOR` /
  `TARGET_COLOR` / `EXIT_GUIDE_COLOR` stay fixed. Audit
  `color-blind-palette-audit`. `s=180` (was 120 — bumped so the entry
  remains obvious on dense charts), `edgecolors="black"`,
  `linewidths=0.8`, `zorder=10`. A bold ``"Entry $123.45"`` label is
  placed ~30 pt to the side + ~35 pt vertically from the marker
  (offset direction picked by `_annotation_offset` so it doesn't
  overlap the candle body), rendered with a white rounded bbox
  outlined in the entry colour and an arrow leader line pointing
  back to the marker. When the marker sits in the right-hand 20%
  of the visible window the horizontal offset flips so the label
  stays inside the chart bounds.
- **Exit**: grey `x` marker at exit_price. `s=170`,
  `linewidths=2.4`, `zorder=10`. Same bbox + arrow treatment as the
  entry label; the vertical offset direction is the opposite of the
  entry label's so the two never collide when entry/exit are close
  together on the x-axis.
- **Entry/exit guide lines**: faint vertical lines (`alpha=0.35`,
  `zorder=3`) at the entry and exit bar indices. The entry line is
  solid green; the exit line is dashed grey. These make the entry
  unmissable even on 200-bar dense charts and are the visual
  remediation for the "screenshots tell me nothing about where the
  entries actually were" complaint.
- **MAE**: red dot at the lowest-low bar (long) / highest-high bar
  (short) during the holding period. Y = `entry_price ∓
  |mae| / |qty|`; X = bar index returned by `_find_extreme_bar`.
- **MFE**: green dot at the opposite extreme. Same y-derivation.
- **Target**: dashed blue horizontal line at `pre.target` when
  PreTradeEntry recorded one; skipped otherwise.
- **Title**: left-aligned
  ``<SYM>  •  LONG/SHORT qty  •  @ YYYY-MM-DD HH:MM ET  •  <setup-segment>``.
  The entry datetime is critical for identifying which trade among a
  busy run the screenshot represents. The setup segment is selected
  by `_draw_title_and_labels`:
  * `setup_tag` set + `entry_strategy` supplied →
    ``setup: <tag>  •  via <strategy.name>``
  * `setup_tag` set, no strategy → ``setup: <tag>``
  * empty `setup_tag` + `entry_strategy` supplied →
    ``<strategy.name or strategy.id>`` (mechanical strategy_tester
    runs never write `setup_tag`; this is the common path)
  * neither set → segment omitted entirely (no `(no setup)` placeholder)

  Right-aligned P&L in green/red.
- **X-axis**: BOTH the price pane and the volume pane (when present)
  carry datetime labels via a ``FuncFormatter`` that maps bar
  indices to ``HH:MM`` (single-day windows) or ``M/D HH:MM``
  (multi-day). The price pane explicitly re-enables
  ``tick_params(axis="x", labelbottom=True)`` to override
  matplotlib's `sharex` auto-hide — without that the user sees no
  time labels at all when the volume pane is missing or carries
  the "Volume unavailable" annotation. Price-pane labels render at
  `fontsize=7`; volume-pane labels at `fontsize=8`. Labels are
  rotated 15° in multi-day mode for legibility.

## Threading contract
- Pure function — no shared mutable state, no globals (apart from the
  read-only colour constants).
- Can be called concurrently from any number of threads (workers in
  `runner.py` produce one trade per row). `FigureCanvasAgg` and
  `Figure` objects are local to the call.

## Error handling
- **Empty candles list** → `ValueError` only when the trade refers
  to a missing timestamp; `CandleTimestampIndex.index_of` falls back to
  a nearest-match lookup so off-by-one epoch precision doesn't crash
  the pipeline.
- **Single-bar trades** (`entry_index == exit_index`) draw a single
  candle window with the entry triangle and exit `x` stacked on the
  same bar.
- **MAE/MFE dot placement on gap-only spans** silently skips the
  marker (returns `-1` from `_find_extreme_bar`).

## Design notes
- *Indicator overlays* — when `entry_strategy` / `exit_strategy` are
  supplied, the renderer walks the strategy condition tree(s)
  (`EntryStrategy.trigger.condition` and every
  `ExitStrategy.legs[*].triggers[*].condition`), collects every
  `FieldRef(kind="indicator")`, deduplicates by
  `(kind_id, sorted(params))`, instantiates each indicator via
  `indicators.base.factory_by_kind_id`, and plots every output of
  the instances whose `overlay == True` on the price pane.
  Oscillator-style indicators (RSI, MACD, SMI — `overlay == False`)
  are deliberately skipped because their 0–100 / centered-zero
  y-scale collapses the price pane. Lines are drawn with a
  distinct color from a small cycle
  (`["#ff7f0e", "#1f77b4", "#9467bd", "#8c564b", "#e377c2",
   "#17becf", "#bcbd22"]`), `linewidth=1.5`, `alpha=0.85`, and a
  legend in the upper-left names each line (e.g. `EMA(3)`,
  `EMA(8)`). When `entry_strategy` and `exit_strategy` are both
  `None`, the rendered PNG is byte-identical to the pre-overlay
  output — see `test_indicator_overlay_backcompat_when_strategy_none`.
  Strategy-tester batch rendering calls
  `build_indicator_overlay_cache(...)` once per symbol and passes the
  immutable cache to every per-trade `render_trade_screenshot` call, so
  a 60-trade symbol computes EMA/VWAP/Bollinger overlays once instead
  of once per PNG. Calling `render_trade_screenshot` directly without a
  cache preserves the old single-call behavior by building a temporary
  cache internally.
- *Timestamp lookup cache* — strategy-tester batch rendering calls
  `build_candle_timestamp_index(...)` once per symbol and passes the
  immutable cache to every per-trade `render_trade_screenshot` call, so
  entry/exit timestamp resolution is O(log N) and the candle timestamp
  list is not scanned twice per PNG. Direct calls without a cache build
  a temporary cache internally. `_index_of_ts` remains as the
  back-compat helper and delegates to the same cache implementation.
- *Volume y-axis on zero-volume windows* — `setup_volume_axes`
  doesn't set `ylim`; matplotlib autoscales from the
  `PolyCollection` vertices the `draw_volume` adds. When every
  visible candle has `volume == 0` (common for yfinance intraday
  bars in extended hours — AMD 5m at 04:00–09:30 ET returns
  `volume=0`), all polygons top out at `y=0` and autoscale
  collapses to the default `(0, 1)` range — the bug
  user-reported on `AMD_t1772226600_post.png`. The renderer now
  pins `ax_volume.set_ylim(0.0, vmax * 1.1)` (mirroring the live
  chart's `core.viewport.compute_volume_ylim` policy); when
  `vmax == 0`, ylim falls back to `(0, 1)` AND an explanatory
  annotation `"Volume unavailable for this window (extended hours
  or no data)"` is drawn in the pane so the empty pane reads as
  intentional rather than a render glitch.
- The PNG is rasterised at 110 dpi by default (≈750 KB / file for
  1600×900). Disk usage caps and "Delete run" controls live in the
  Recent runs sidebar (PR 5).
- Reusing `rendering.py` rather than reimplementing the candle
  primitive is the deliberate single-source-of-truth invariant:
  live-chart palette tweaks propagate automatically (R2 mitigation
  in plan.md).
