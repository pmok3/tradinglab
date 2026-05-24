# `strategy_tester/screenshot.py`

Headless per-trade screenshot rendering for the Strategy Tester
Report. One PNG per closed trade, composed via the same
`tradinglab.rendering` primitives as the live chart so visual parity
holds.

## Public surface
- `ScreenshotSpec` — knob bag with defaults: `pre_bars=30`,
  `post_bars=10`, `max_bars=200`, `width_in=14.5`, `height_in=8.2`,
  `dpi=110`, `dark_mode=False`, `draw_volume_pane=True`.
- `render_trade_screenshot(*, candles, trade_row, output_path, spec=None) -> Path`
  — renders one PNG to disk; returns the actual `Path`.
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

## Annotation contract
- **Entry**: triangle marker, green up-triangle for long, red
  down-triangle for short. `s=180` (was 120 — bumped so the entry
  remains obvious on dense charts), `edgecolors="black"`,
  `linewidths=0.8`, `zorder=10`. A small inline label
  ``" Entry $123.45"`` is placed at the marker so the user can read
  the fill price without zooming in.
- **Exit**: grey `x` marker at exit_price. `s=170`,
  `linewidths=2.4`, `zorder=10`. Inline ``" Exit $123.45"`` label.
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
  ``<SYM>  •  LONG/SHORT qty  •  @ YYYY-MM-DD HH:MM ET  •  setup: <tag>``.
  The entry datetime is critical for identifying which trade among a
  busy run the screenshot represents. Right-aligned P&L in green/red.
- **X-axis**: bottom pane (volume when present, otherwise price)
  carries datetime labels via a ``FuncFormatter`` that maps bar
  indices to ``HH:MM`` (single-day windows) or ``M/D HH:MM``
  (multi-day). Labels are rotated 15° in multi-day mode for legibility.

## Threading contract
- Pure function — no shared mutable state, no globals (apart from the
  read-only colour constants).
- Can be called concurrently from any number of threads (workers in
  `runner.py` produce one trade per row). `FigureCanvasAgg` and
  `Figure` objects are local to the call.

## Error handling
- **Empty candles list** → `ValueError` only when the trade refers
  to a missing timestamp; the helper `_index_of_ts` falls back to a
  nearest-match scan so off-by-one epoch precision doesn't crash the
  pipeline.
- **Single-bar trades** (`entry_index == exit_index`) draw a single
  candle window with the entry triangle and exit `x` stacked on the
  same bar.
- **MAE/MFE dot placement on gap-only spans** silently skips the
  marker (returns `-1` from `_find_extreme_bar`).

## Design notes
- Per the plan, *indicator overlays* (drawing only the indicators
  referenced by the entry/exit strategy) are out of scope for PR 2.
  They land in a follow-up because they require introspecting the
  strategy's `trigger.condition` shape and computing the matching
  indicator series — orthogonal to the screenshot wiring itself.
- The PNG is rasterised at 110 dpi by default (≈750 KB / file for
  1600×900). Disk usage caps and "Delete run" controls live in the
  Recent runs sidebar (PR 5).
- Reusing `rendering.py` rather than reimplementing the candle
  primitive is the deliberate single-source-of-truth invariant:
  live-chart palette tweaks propagate automatically (R2 mitigation
  in plan.md).
