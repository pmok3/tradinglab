# indicators/macd.py — Spec

## Purpose
MACD — Moving Average Convergence Divergence. Three concurrent series
on its own pane (`pane_group = "macd"`): `macd = fast_ma - slow_ma`,
`signal = ma(macd)`, `histogram = macd - signal`. Values are in
price units (unbounded), so MACD does not share a y-scale with the
bounded oscillators.

## Public API
- `class MACD` — `kind_id="macd"`, `kind_version=1`,
  `overlay=False`, `pane_group="macd"`, `reference_levels=(0.0,)`.
  Constructor
  `(fast_length=12, slow_length=26, signal_length=9, ma_type="EMA",
   source="close")`.
- `params_schema`:
  - `fast_length: int` (default 12, min 2, max 2000).
  - `slow_length: int` (default 26, min 2, max 2000). Must be
    strictly greater than `fast_length`; equality/inversion raises
    `ValueError` (would flip the momentum sign).
  - `signal_length: int` (default 9, min 2, max 2000); `>= 2`
    enforced (1 would make `histogram == 0`).
  - `ma_type: choice` (default `"EMA"`, `SMA | EMA | WMA | RMA`) —
    applied uniformly to fast, slow, and signal.
  - `source: choice` (default `"close"`, `close | hl2 | hlc3 | ohlc4`).
- `default_style`: `macd` `#2ca02c` w=1.4; `signal` `#ff7f0e` w=1.2;
  `histogram` `#26a69a` w=1.0 (bar colors come from `histogram_palette`
  via the classifier — the style color is only used for legend hint).
- `output_kinds: ClassVar[Mapping[str, str]]` —
  `{"macd": "line", "signal": "line", "histogram": "histogram"}`.
  The render layer dispatches non-`"line"` keys to specialised artists
  (see `render.spec.md`).
- `histogram_palette: ClassVar[Tuple[str, str, str, str]]` — 4-color
  scheme in classifier index order
  `(rising_above, falling_above, rising_below, falling_below)` =
  `(strong_bull, weak_bull, weak_bear, strong_bear)`. Sourced from
  `constants.macd_histogram_palette()` (single source of truth, Okabe-Ito
  aware) — NO hardcoded hex. The histogram **renderer re-resolves this
  LIVE** on every paint via the same function (see `render.spec.md`), so a
  runtime color-blind toggle reaches the bars; the ClassVar snapshot drives
  only the default-style legend swatch + introspection. Audit
  `color-blind-palette-audit`.
- `compute(candles) -> {"macd", "signal", "histogram"}`. All three
  outputs are length `len(candles)`; warmup is NaN.
- `compute_arr(bars)` — `Bars`-native entry point used by the cache.
- `classify_histogram(hist) -> np.ndarray[int8]` — pure helper
  returning `0..3` per the palette order above, `-1` for NaN. The
  first defined bar has no predecessor; it is classified as "rising"
  by convention. Fully vectorised (no per-bar loop): a NaN gap resets
  the "rising" comparison to True for the next finite bar, matching the
  former scalar loop. Pinned by
  `tests/unit/indicators/test_iir_vectorization.py`.
- `name`: compact label; tags appear only when a parameter differs
  from its default (e.g. `MACD(12,26,9)`, `MACD(12,26,9,SMA,hl2)`,
  `MACD(8,21,5)`).

## Dependencies
- Internal: `..models.Candle`, `..core.bars.Bars`,
  `.base.LineStyle`, `.base.ParamDef`,
  `.ma_kernels.MA_TYPES`, `.ma_kernels.apply_ma`.
- External: `numpy`.

## Algorithm
```
src       = source_values(bars, source)        # close / hl2 / hlc3 / ohlc4
fast_ma   = apply_ma(ma_type, src, fast_length)
slow_ma   = apply_ma(ma_type, src, slow_length)
macd      = fast_ma - slow_ma
signal    = apply_ma(ma_type, macd, signal_length)
histogram = macd - signal
```

`apply_ma` skips leading NaNs cleanly. Source selector:
`close → bars.close`, `hl2 → (h+l)/2`, `hlc3 → (h+l+c)/3`,
`ohlc4 → (o+h+l+c)/4`.

## Invariants
- `histogram[i] == macd[i] - signal[i]` (float tolerance) at every
  defined index.
- `macd` is NaN until `first_valid_input + slow_length - 1`.
- `signal` / `histogram` are NaN until
  `first_valid_input + slow_length + signal_length - 2`.
- Output arrays have the same length as the input candle sequence.
- Compute is deterministic for a given `(candles, params)` pair.

## Design Decisions
- **Own pane (`pane_group = "macd"`).** MACD is unbounded in price
  units; sharing a y-axis with RSI/SMI/ADX/RVOL would crush them.
- **Uniform kernel for fast/slow/signal.** Mixing per-MA kernels
  multiplies the param surface without practical benefit.
- **`slow > fast` validated at construction.** Inversion would flip
  the momentum sign convention silently.
- **Histogram as a single `LineCollection`** of vertical segments
  `[(x_i, 0) → (x_i, hist[i])]` with per-segment colors. Avoids the
  "tens of thousands of Line2D bars" cost without coupling to a
  bar-chart Axes type. See `render.spec.md` for the dispatch.
- **`output_kinds` ClassVar** is optional and backwards-compatible —
  indicators without it stay on the all-line render path.

## Incremental protocol (compute #3)
- `inc_init(bars)` / `inc_step(state, bars, *, prev_len)` extend MACD O(k) on a closed-bar append. State = `{fast, slow, signal, seeded}` + cached `output`/`len`: the three chained EMAs (fast, slow, and the signal EMA of the macd line) are continued with `alpha=2/(L+1)` per leg. **Gated to `ma_type=='EMA'`** (the default) via `_inc_supported()`; SMA/WMA/RMA leave `seeded=False` so `inc_step` raises and the cache full-recomputes. Causal-prefix-exact; appended bars differ from the kernel by float64 round-off. Pinned by `tests/unit/indicators/test_indicator_meta.py` (generic parity).
