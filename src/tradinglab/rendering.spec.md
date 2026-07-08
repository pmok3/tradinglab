# rendering.py — Spec

## Purpose

Pure matplotlib drawing primitives for candlestick + volume charts. Designed
for **virtualized** rendering: each `draw_*` accepts a `[start, end)` slice
and builds artists only for bars in that range. Caller (`ChartApp`) owns the
axes lifecycle (one-time styling, xlim, grid) so pan re-renders swap
Collections without rebuilding formatters/locators.

## Public API

- Directional colours are resolved **live** at paint time via
  `constants.BULL_COLOR` / `constants.BEAR_COLOR` attribute lookup (the module
  imports `from . import constants as _constants`, NOT a value-binding
  `from .constants import BULL_COLOR` — that froze the colour at import and
  broke the runtime Okabe-Ito palette toggle). `_bar_rgba` and `vol_geometry`
  read `_constants.BULL_COLOR` / `_constants.BEAR_COLOR` on every call, so
  `ChartApp.set_use_colorblind_palette` + `_render()` repaints with the new
  palette without a relaunch. Audit `color-blind-palette`.
- `safe_remove(artist)` — `artist.remove()` wrapped in try/except.
- `draw_candlesticks(ax, candles, x_offset=0, start=0, end=None, hollow_indices=None, flat_overlay=None, body_half=None) -> (wicks, bodies)`
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
  - **Vectorized geometry (perf sprint #2)**: the non-hollow path builds
    all wick/body vertices + RGBA colours with numpy via
    `_vectorized_candle_geometry` — no per-bar Python loop. Bit-for-bit
    identical to looping `bar_geometry` (pinned by
    `tests/unit/test_rendering_vectorized.py`); ~3.3× faster on large
    slices (zoom-out 60k bars: ~200ms→~60ms). The hollow path keeps the
    legacy per-bar loop (wick-splitting + per-bar face/linewidth don't
    vectorize cleanly, and Highlight-Key-Bars is never the hot path).
    `flat_overlay` works on top of either (it iterates the built body
    vertices, numpy rows or list tuples).
  - **`_sc_*` cache dtypes**: `_sc_verts` (bodies) and `_sc_segments`
    (wicks) are the vectorized numpy arrays `(M,4,2)` / `(M,2,2)` (only
    the H1 fastpath touches them, via `[-1] = <tuple>` + `set_*`, both
    ndarray-safe). `_sc_colors` and `_sc_src_indices` are Python **lists**
    (consumers — the fastpath + `volume_tod_overlay.suppress_default_volume_fill`
    — use `or []` / `not` / item-assignment / `zip` semantics). wicks and
    bodies keep SEPARATE `_sc_colors` lists (the tick fastpath rewrites
    `wicks._sc_colors[-1]` only — prior behaviour preserved exactly).

- `brighter_shade(rgba, *, dark_mode) -> rgba` — RGB→HLS, saturation=1.0,
  lightness clamped: dark `min(0.92, max(0.55, l + 0.18))`; light
  `min(0.55, max(0.40, l))`. The dark-mode `l + 0.18` term guarantees the
  accent stays visibly lighter than the source body it is hatched on top of
  — a plain `max(0.55, l)` was a no-op for bodies already brighter than 0.55
  (the coral `BEAR_COLOR`, l≈0.625), collapsing the hatch into the body so
  bear flat bars were invisible in dark mode. Alpha passthrough. Used by
  `ChartApp._ha_flat_overlay_for` in dark mode.
- `darker_shade(rgba, *, dark_mode) -> rgba` — lightness drops 0.18 light
  (floor 0.18) / 0.10 dark (floor 0.10); saturation `+0.15` (clamped to 1.0).
  Alpha passthrough. Used by `gui/volume_tod_overlay` and by
  `ChartApp._ha_flat_overlay_for` in light mode.
- `draw_volume(ax, candles, x_offset=0, start=0, end=None, body_half=None) -> bars`
  `PolyCollection`. RTH bars at 0.7 alpha, extended-hours at
  `0.7 * _EXTENDED_ALPHA` (≈0.315). Geometry + colours built vectorized
  via `_vectorized_vol_geometry` (bit-for-bit identical to looping
  `vol_geometry`); `_sc_verts` is the numpy `(M,4,2)` array, `_sc_colors`
  a Python list (per the fastpath / suppression consumers).
- `draw_session_shading(ax, candles, x_offset=0, start=0, end=None, pre_color, post_color, intraday=False) -> List`
  Soft vertical bands behind contiguous extended-hours runs. Uses a
  blended transform (data X, axes Y) so bands always span full axes height;
  contiguous same-session runs collapse into one Rectangle.
- `setup_price_axes(ax)`, `setup_indicator_pane_axes(ax, *, min_label_px=28)`,
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
  (`hspace=0` collision with the price/volume boundary). `style_axes`
  recolors ticks via `ax.tick_params(which="both", ...)` so MINOR tick
  marks + labels are themed alongside majors — required for a log y-scale
  pane (e.g. RVOL `log_scale=True`), where a typical sub-decade ratio range
  renders its readable labels (`2,3,4,6×10ⁿ`) as MINOR ticks that would
  otherwise stay default-black and vanish in dark mode. The kwarg persists
  onto minor ticks created by a later `set_yscale("log")` and recolors
  existing minors on a live theme swap; it is a no-op on linear panes with
  no minor ticks. `style_axes`
  also recolors any in-pane indicator-name label artists on theme swap:
  it iterates `ax._sc_pane_label_artists` (the per-config name + spacer
  `Text` artists created by `indicators.render._render_pane_labels`),
  falling back to the legacy singular `ax._sc_pane_label_artist`.
- Geometry helpers `bar_geometry`, `vol_geometry` and an optional
  `body_half: Optional[float]` on `draw_candlesticks`/`draw_volume` support
  the H1 stream-tick fastpath. The single-bar helpers remain the source of
  truth for the fastpath; the slice-build hot path uses their vectorized
  twins `_vectorized_candle_geometry` / `_vectorized_vol_geometry` (plus
  the shared `_extract_slice_arrays` single-pass OHLCV/session extractor
  and `_bar_colors_vec` RGBA builder), which produce byte-identical output.
  Module constants `_DENSE_PX_PER_BAR_THRESHOLD
  = 4.0`, `_BODY_HALF = 0.6`, `_BODY_HALF_FLOOR = 0.05`; helper
  `dynamic_body_half(ax, n_visible)` clamps `_BODY_HALF * ratio` between
  floor and `_BODY_HALF` when px/bar drops below the threshold (no-op at or
  above it). `ChartApp._draw_slice` stashes the result on `ps["body_half"]`
  so the fastpath reuses the same width.

## Dependencies

- Internal: `constants` (`BULL_COLOR`, `BEAR_COLOR` via live `_constants.*`
  attribute lookup — see Public API note; `classify_session` for
  gap-shading), `formatting.fmt_volume`, `models.Candle`.
- External: `numpy`; matplotlib `to_rgba` at module import and
  `LineCollection`, `PolyCollection`, `Rectangle`, `blended_transform_factory`,
  `FuncFormatter`, `MaxNLocator` inside the draw/setup functions.

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
