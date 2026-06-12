# core/viewport.py — Spec

## Purpose
Pure math for y-axis autoscale and the virtualized render-range calculation. Called from `ChartApp._autoscale_slot_y`, `gui/interaction._autoscale_y_to_visible`, and `ChartApp._ensure_rendered_for_view` / `_render`.

## Public API
- `RENDER_BUFFER_MULTIPLIER = 3` — render window is 3× visible.
- `y_limits_for_slice(series, kind, start, end, *, log=False) -> Optional[(ymin, ymax)]` — nan-aware y-range. For `kind="price"`, **asymmetric** padding: bottom and top pads resolved at call time from `defaults.get("price_bot_pad_frac")` (default 0.05) / `defaults.get("price_top_pad_frac")` (default 0.12) — user-overridable via `settings.json`. Extra top headroom reserves space for the always-on top-left OHLCV / %change readout strip (`gui/interaction.spec.md` §11.6). For `kind="volume"` returns `(0, 1.1 * max)` (no readout on volume axes). On `log=True` (price only), asymmetry applied multiplicatively in log space. Returns `None` when the slice has no finite values.
- `remap_window_by_time(prev_dates, prev_xlim, new_dates) -> Optional[(lo, hi)]` — remap an index-space xlim from a previous series onto a new series by timestamp coverage. Returns a half-open slice or `None` for invalid / degenerate remaps. Also returns `None` when the source window spans the **entire** source series (`lo_i == 0` AND `hi_i == n_prev-1`) — viewing all of a symbol (e.g. a 2-bar IPO showing its full history) is not a deliberate zoom, so there is no calendar selection to preserve; the caller falls back to its default right-edge window. A proper sub-window (touching at most one edge), however narrow, is still remapped.
- `compute_render_range(visible_lo, visible_hi, n, min_size, max_size) -> (start, end)` — `[start, end)` centered on visible, width = `max(min_size, min(max_size, visible_count * RENDER_BUFFER_MULTIPLIER))`, clipped to `[0, n]`.

## Dependencies
Internal: `.series.SeriesArrays`, `..defaults`. External: `numpy`.

## Design Decisions
- **NaN-aware reductions** (`np.nanmin`/`np.nanmax`) so gap candles are transparently skipped. Returns `None` on all-NaN/empty (matplotlib refuses NaN/Inf ylims).
- **Linear fallback `max(hi * 0.01, 1.0)`** prevents a zero-range slice collapsing to `lo == hi`.
- **Log-space padding is multiplicative**: additive padding on a log axis either produces a negative `ymin` or pushes past a decade boundary, causing `LogLocator` to round the view out and shrink candles to half-window. `top_mult = (hi/lo) ** TOP_PAD_FRAC`, `bot_mult = (hi/lo) ** BOT_PAD_FRAC` fattens by the same fraction of log-span as linear, decade-stable.
- **Asymmetric price-axis padding (top headroom)** — `kind="price"` pads `BOT_PAD_FRAC` below, `TOP_PAD_FRAC` above. Volume stays symmetric (no readout). Tuned to roughly match TradingView. Locked by `check_d29_price_axes_top_headroom`.
- **Volume: 0 to 1.1 × max**, not padded both ends. Padding below would look like negative volume.
- **Render buffer 3×**: lets `_ensure_rendered_for_view` wait until visible crosses halfway before refilling. Smooth panning without excessive redraw.
- **`compute_render_range` re-centers on refill** rather than sliding per frame: ~1 refill per `span` bars panned.
- **Full-source-coverage is not a preservable zoom** (`remap_window_by_time`): the ticker-switch time-window preservation exists to carry a deliberate *sub-window* selection across symbols. If the source xlim spans the whole source series, the user wasn't zoomed in — preserving it would map a tiny full-history span (e.g. a 2-bar IPO's ~1 day) onto a long-history destination, crushing it to ~2 bars. Detected via the rounded/clamped source indices (`lo_i == 0` AND `hi_i == n_prev-1`) and resolved by returning `None` (caller defaults to the right-edge window). Chosen over a destination-width floor because a floor would also clobber a *deliberate* narrow zoom (e.g. a 3-bar window the user wants carried across names). Locked by `tests/unit/test_viewport_remap.py` (`test_two_bar_ipo_source_does_not_crush_long_destination`, full-coverage + edge-only-pan cases).

## Invariants
- `y_limits_for_slice` never returns NaN/Inf.
- On `log=True`, `ymin > 0` always (`lo > 0` guard).
- `compute_render_range`: `0 <= start <= end <= n` and `end - start >= min(target, n)` (subject to clipping).
- All-gap slice → `None` (callers keep existing ylim).
- `remap_window_by_time` full-source-coverage (`lo_i == 0` AND `hi_i == n_prev-1`) → `None`; a window touching at most ONE edge is a deliberate pan and is still remapped.

## Algorithm
```
y_limits_for_slice(series, "price", s, e, log):
    lows = series.lows[s:e]; highs = series.highs[s:e]
    if lows.size == 0 or no finite: return None
    lo, hi = nanmin(lows), nanmax(highs)
    TOP_PAD_FRAC, BOT_PAD_FRAC = 0.12, 0.05  # defaults; overridable via settings
    if log and lo > 0 and hi > 0:
        return lo / (hi/lo)**BOT_PAD_FRAC, hi * (hi/lo)**TOP_PAD_FRAC
    span = hi - lo
    if span <= 0:
        pad = max(hi*0.01, 1.0); return lo-pad, hi+pad
    return lo - span*BOT_PAD_FRAC, hi + span*TOP_PAD_FRAC

compute_render_range(vlo, vhi, n, minsz, maxsz):
    clamp vlo, vhi to [0, n]
    span = max(1, vhi - vlo)
    target = clamp(span * RENDER_BUFFER_MULTIPLIER, minsz, maxsz)
    if target >= n: return (0, n)
    center = (vlo + vhi) // 2
    start = max(0, center - target//2)
    end = min(n, start + target)
    if end - start < target: start = max(0, end - target)
    return (start, end)
```

## Known limitations
- Log padding assumes strictly positive prices. A candle with `low <= 0` falls through to linear padding.
