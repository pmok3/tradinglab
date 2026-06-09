# indicators/bollinger.py — Spec

## Purpose
Bollinger Bands volatility envelopes. Three concurrent overlay lines
(`middle` / `upper` / `lower`) on the price axis. The centerline can
be any of four moving-average kernels (`SMA`, `EMA`, `WMA`, `RMA`)
selected via the `ma_type` ParamDef; the band envelope is the same
population-σ window regardless of kernel.

## Public API
- `class BollingerBands` — `kind_id="bbands"`, `kind_version=3`,
  `overlay=True`. Constructor
  `(length=20, num_std=2.0, std_length=None, ma_type="SMA")`.
- `params_schema`:
  - `length: int` (default 20, min 2, max 2000) — centerline MA
    window.
  - `num_std: float` (default 2.0, min 0.1, max 10.0) — band width
    in σ.
  - `std_length: int` (default 20, min 2, max 2000) — rolling-σ
    window. Constructor argument `None` resolves to `length` (so
    pre-b44 configs hydrate identically).
  - `ma_type: choice` (default `"SMA"`, choices `SMA | EMA | WMA |
    RMA`).
- `default_style`: SMA-default green (`#2ca02c`) for all three keys
  (middle width 1.2, upper/lower 1.0). Per-MA palette
  (`SMA→#2ca02c`, `EMA→#d62728`, `WMA→#9467bd`, `RMA→#1f77b4`)
  defined for future per-instance default swapping.
- `scannable_outputs = (("middle","numeric"),("upper","numeric"),("lower","numeric"))` — exposes the three lines to the scanner registry.
- `effective_output_keys(cls, params) -> ("upper", "middle", "lower")` — classmethod overriding `BaseIndicator.effective_output_keys` (`default_style` insertion order is `middle, upper, lower`) so the in-readout overlay legend (`gui/readout_legend.py`) renders the bands in canonical **top-down visual order on the chart** (`upper` above `middle` above `lower`). Without this override the row would read `BB(20) middle <v> upper <v> lower <v>`, which contradicts the chart geometry. Audit `legend-condensation`.
- `compute(candles) -> {"middle", "upper", "lower"}`. First
  `max(length, std_length) - 1` entries of the bands are NaN; the
  centerline alone is defined from `length-1` onward (so for
  `std_length > length` the middle shows during a warmup where bands
  are still NaN).
- `name`: `BB(20,2)` when `std_length == length` for SMA; suffixes
  `,EMA` etc. for non-default kernel; appends `σ=K` when σ window
  decoupled (e.g. `BB(20,2,EMA,σ=10)`).

## Dependencies
- Internal: `..models.Candle`, `.base.LineStyle`, `.base.ParamDef`,
  `.ma_kernels.MA_TYPES`, `.ma_kernels.apply_ma`.
- External: `numpy`.

## Design Decisions
- **Single class with `ma_type` discriminator.** Persisted
  `kind_id="bbands_ema"` migrates to `("bbands", {"ma_type": "EMA"})`
  via `base._KIND_ID_MIGRATIONS`.
- **σ uses population stddev (`numpy.std` ddof=0)** on `close` over
  `std_length`. Matches Bollinger's original formulation and
  TradingView (differs by √(N/(N−1)) from sample-stddev).
- **σ is computed around the simple mean of closes**, regardless of
  `ma_type`. Matches TradingView / ThinkOrSwim for EMA / WMA variants.
- **Rolling mean + variance via cumulative sums** — O(N) using
  `Var = E[X²] − E[X]²`. Negative values from float noise are clamped
  to 0 before `sqrt`.
- **Centerline delegated to `apply_ma`** — shared with ATR / Keltner /
  MACD; one bug fix to `RMA`/`WMA` covers all.
- **`std_length` independent of `length`** lets the user smooth band
  width without flattening the centerline.

## Invariants
- `upper >= middle >= lower` at every defined position when
  `num_std > 0`.
- For `ma_type="SMA"` and `std_length == length`,
  `middle == SMA(length).compute(candles)["sma"]` exactly.
- All three outputs have length `len(candles)`; first
  `max(length, std_length) - 1` indices of the bands are NaN.

## Data Flow / Algorithm
```
closes = fromiter(c.close for c in candles)
center = apply_ma(ma_type, closes, length)            # MA kernel
csum   = concat([0], cumsum(closes))
csum2  = concat([0], cumsum(closes**2))
mean_m = (csum[m:]  - csum[:-m])  / m                  # m = std_length
mean_sq = (csum2[m:] - csum2[:-m]) / m
sigma  = sqrt(clip(mean_sq - mean_m**2, 0, +inf))      # ddof=0 σ on closes
warmup = max(length, std_length)
middle[length-1:] = center[length-1:]
upper [warmup-1:] = middle[warmup-1:] + num_std * sigma_aligned
lower [warmup-1:] = middle[warmup-1:] - num_std * sigma_aligned
```

## Incremental protocol (compute #3)
- `inc_init(bars)` / `inc_step(state, bars, *, prev_len)` extend the bands O(k). State = `{sum_n, sum_m, sumsq_m, seeded}` + cached `output`/`len`: `sum_n` is the rolling SMA window sum (middle), `sum_m`/`sumsq_m` the rolling population-variance running sums (std = sqrt(max(sumsq_m/m - mean_m**2, 0))). **Gated to `ma_type=='SMA'`** (the default); EMA/WMA/RMA leave `seeded=False` → full recompute. Causal-prefix-exact; appended bars differ from compute_arr's cumsum form by float64 round-off. Pinned by the generic parity meta-test.
