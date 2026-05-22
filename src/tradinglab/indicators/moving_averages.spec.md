# indicators/moving_averages.py — Spec

## Purpose
Two overlay indicators: `SMA` (simple moving average) and `EMA`
(exponential moving average), both on close prices.

## Public API
- `class SMA(length=20)` — `kind_id="sma"`, `kind_version=1`,
  `name = f"SMA({length})"`, `overlay = True`. `compute(candles) ->
  {"sma": ndarray}` with the first `length-1` entries NaN.
  - `params_schema = (ParamDef("length", "int", 20, min=1,
    max=2000),)`.
  - `default_style = {"sma": LineStyle(color="#1f77b4", width=1.4)}`.
- `class EMA(length=20)` — `kind_id="ema"`, `kind_version=2`,
  `name = f"EMA({length})"`, `overlay = True`,
  `alpha = 2/(length+1)`. `compute(candles) -> {"ema": ndarray}`;
  first `length-1` entries are NaN, EMA is seeded at index `length-1`
  with the SMA of the first `length` closes, then recurses.
  - `params_schema = (ParamDef("length", "int", 20, min=1,
    max=2000),)`.
  - `default_style = {"ema": LineStyle(color="#ff7f0e", width=1.4)}`.
- Both raise `ValueError` on `length < 1`.

## Dependencies
- Internal: `..models.Candle`.
- External: `numpy`.

## Design Decisions
- **SMA via cumulative sum**: `out[n-1:] = (csum[n:] - csum[:-n]) / n`
  — O(N) instead of O(N×length). `np.concatenate(([0.0], cumsum))`
  puts a zero at the front so the windowed difference starts at
  index `n-1`.
- **EMA seed = SMA of first N closes (TradingView/TA-Lib convention).**
  Indices `0..length-2` are NaN. At index `length-1` EMA emits the
  simple mean; from `length` onward the recurrence
  `ema[i] = α·close[i] + (1−α)·ema[i−1]` runs. Differs from
  `pandas.ewm(adjust=False)` (which seeds at the first close and
  diverges for many bars before converging).

## Invariants
- `SMA(n).compute(cs)["sma"]`: length `len(cs)`, first `n-1` NaN, rest
  is trailing n-sample mean of closes.
- `EMA(n).compute(cs)["ema"]`: length `len(cs)`. Indices `0..n-2`
  NaN. `ema[n-1] == mean(cs[0..n-1].close)`. For `i >= n`:
  `ema[i] = α·cs[i].close + (1-α)·ema[i-1]`.
- Both raise `ValueError` pre-compute when instantiated with
  `length < 1`.
