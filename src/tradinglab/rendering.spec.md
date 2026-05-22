# rendering.py — Spec

## Purpose

Pure matplotlib drawing primitives for candlestick + volume charts. Designed
for **virtualized** rendering: each `draw_*` accepts a `[start, end)` slice
and builds artists only for bars in that range. Caller (`ChartApp`) owns the
axes lifecycle (one-time styling, xlim, grid) so pan re-renders swap
Collections without rebuilding formatters/locators.

## Public API

- `BULL_COLOR`, `BEAR_COLOR` — re-imported from `.constants`.
- `safe_remove(artist)` — `artist.remove()` wrapped in try/except.
- `draw_candlesticks(ax, candles, x_offset=0, start=0, end=None, hollow_indices=None, flat_overlay=None) -> (wicks, bodies)`
  Builds `LineCollection` (wicks) + `PolyCollection` (bodies) for the slice.
  X coordinate is `global_index + x_offset` (stable across slice refills).
  Returns `(None, None)` on empty slice. Gap bars (`is_gap`) leave their
  X-slot blank. Both artists have `set_snap(False)` (path-snap rounds
  asymmetrically at low px/bar densities; AA keeps body+wick mathematically
  centered).
  - `hollow_indices: set[int]` — those global indices render with transparent
    face + thicker outline (`linewidth=1.4` vs `0.8`); wick is split into
    two segments (low→body-low, body-high→high) so nothing draws inside the
    body. Flag stashed as `bodies._sc_hollow_mode`.
  - `flat_overlay: Mapping` keys `bull_indices`/`bear_indices`/`bull_color`/
    `bear_color`/`bull_hatch`/`bear_hatch` — emits an additional hatched
    `PolyCollection` per side on top of bodies (`facecolors="none"`,
    `edgecolors=*_color`, `linewidths=0`, `hatch=*_hatch`, `zorder=3.5`).
    Bull/bear are separate collections because matplotlib hatch is
    per-collection. Stashed on `bodies._sc_flat_hatch_collections`
    (matplotlib does NOT cascade `remove()` from `bodies` to add-ons).
  - Hollow takes priority over flat: bars in both render hollow, hatch
    polygon omitted for that bar.
  - When any hatch is emitted, `bodies._sc_accent_mode = True` so the H1
    fastpath bails (rightmost-only mutation can't safely re-derive masks).
- `brighter_shade(rgba, *, dark_mode) -> rgba` — RGB→HLS, saturation=1.0,
  lightness clamped: dark `max(0.55, l)`; light `min(0.55, max(0.40, l))`.
  Alpha passthrough. Used by `ChartApp._ha_flat_overlay_for` in dark mode.
- `darker_shade(rgba, *, dark_mode) -> rgba` — lightness drops 0.18 light
  (floor 0.18) / 0.10 dark (floor 0.10); saturation `+0.15` (clamped to 1.0).
  Alpha passthrough. Used by `gui/volume_tod_overlay` and by
  `ChartApp._ha_flat_overlay_for` in light mode.
- `draw_volume(ax, candles, x_offset=0, start=0, end=None) -> bars`
  `PolyCollection`. RTH bars at 0.7 alpha, extended-hours at
  `0.7 * _EXTENDED_ALPHA` (≈0.315).
- `draw_session_shading(ax, candles, x_offset=0, start=0, end=None, pre_color, post_color, intraday=False) -> List`
  Soft vertical bands behind contiguous extended-hours runs. Uses a
  blended transform (data X, axes Y) so bands always span full axes height;
  contiguous same-session runs collapse into one Rectangle.
- `setup_price_axes(ax)`, `setup_indicator_pane_axes(ax)`,
  `setup_volume_axes(ax)`, `style_axes(ax, theme)` — one-time axes setup
  (grid, `margins=0`, y-tick locator, theme colors). Y-tick labels on the
  **right** edge (TradingView/Sierra convention). `setup_indicator_pane_axes`
  uses a pixel-aware `MaxNLocator` subclass capping `nbins` to
  `pane_height_px // 28`. `setup_volume_axes` caps at `nbins=3` and prunes
  **both** the upper tick (collides with the bottom-most price tick on the
  pane above; `hspace=0`) AND the lower tick (which is always `0` because
  volume ylims are pinned to `(0.0, vmax * 1.1)` — `0` is visually obvious
  from a bar reaching the pane's bottom edge, and the label collided with
  whatever indicator pane the user placed below volume). Audit
  ``volume-axis-prune-both``. `setup_price_axes` prunes lower tick only
  (`hspace=0` collision with the price/volume boundary).
- Geometry helpers `bar_geometry`, `vol_geometry` and an optional
  `body_half: Optional[float]` on `draw_candlesticks`/`draw_volume` support
  the H1 stream-tick fastpath. Module constants `_DENSE_PX_PER_BAR_THRESHOLD
  = 4.0`, `_BODY_HALF = 0.6`, `_BODY_HALF_FLOOR = 0.05`; helper
  `dynamic_body_half(ax, n_visible)` clamps `_BODY_HALF * ratio` between
  floor and `_BODY_HALF` when px/bar drops below the threshold (no-op at or
  above it). `ChartApp._draw_slice` stashes the result on `ps["body_half"]`
  so the fastpath reuses the same width.

## Dependencies

- Internal: `constants` (`BULL_COLOR`, `BEAR_COLOR`, `classify_session` for
  gap-shading), `formatting.fmt_volume`, `models.Candle`.
- External: matplotlib (`LineCollection`, `PolyCollection`, `Rectangle`,
  `blended_transform_factory`, `FuncFormatter`, `MaxNLocator`, `to_rgba`).
  Imported lazily inside each draw fn; module import stays cheap.

## Design notes

- **X coord = global candle index + offset, NEVER slice-local.** This is the
  central invariant — slice refills don't teleport bars.
- Collections > per-bar Line2D/Rectangle (per-artist overhead ~100× per-vertex).
- Zero-body candles get a synthetic body height (`span * 0.01` or `0.01`).
- Extended-hours bars are alpha-dimmed (not recolored).
- HA display substitution: when HA mode on, `ChartApp._draw_slice` swaps in
  HA-OHLC candles for `draw_candlesticks` only; `draw_volume`,
  `draw_session_shading`, and indicators receive real candles. H1 fastpath
  bails in HA mode (rightmost-only mutation can't satisfy the HA recurrence).
- Highlight Key Bars: `_key_bar_hollow_indices_for(candles)` → `hollow_indices`
  set. Fastpath bails when toggle is on.
- Highlight Flat Bars (View → Heikin-Ashi): only renders when HA mode and
  the flat-highlight toggle are both on; hatch pattern is `"xxx"` on both
  sides (bull/bear identity is already in the body fill). Piggybacks on the
  HA fastpath bail.
- `draw_*` never calls `ax.clear()` or sets xlim — caller's job. This is
  what makes pan re-renders cheap.

## Invariants

- For any `i in [start, end)`, artists draw at X = `i + x_offset`.
- `draw_candlesticks` and `draw_volume` skip `is_gap` bars.
- Contiguous same-session runs in `draw_session_shading` collapse to one
  Rectangle.
- `draw_session_shading(intraday=False)` never classifies `"gap"` as
  `"pre"`/`"post"` (midnight-stamped gaps would falsely classify).
- Setup helpers are idempotent.

## Algorithm (`draw_session_shading`)

```
i = start
while i < end:
    sess = _shade_session(candles[i])      # respects intraday flag for gaps
    if sess in {"pre","post"}:
        j = i
        while j < end and _shade_session(candles[j]) == sess: j += 1
        add Rectangle X=[i+off-0.5, (j-1)+off+0.5], Y=[0,1] axes-coords,
            face=pre/post_color, alpha=0.14, zorder=0
        i = j
    else:
        i += 1
```
