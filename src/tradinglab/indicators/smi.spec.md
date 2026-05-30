# indicators/smi.py — Spec

## Purpose
Stochastic Momentum Index (William Blau, 1993) — a double-smoothed
refinement of the classic stochastic oscillator. Measures how far the
close is from the midpoint of the recent range, then double-EMA-smooths
the result. Output bounded in roughly `[-100, +100]`; signal line
(third EMA) gives the primary crossover trigger. Drawn in its own pane.

## Public API
- `class SMI(length=14, smooth1=3, smooth2=3, signal_length=3)` —
  `kind_id="smi"`, `kind_version=1`, `overlay=False`. Display
  registry key: `"Stochastic Momentum Index"`.
- `params_schema`:
  - `length: int` (default 14, min 2, max 2000) — HH/LL lookback
    (Blau's `%K`).
  - `smooth1: int` (default 3, min 1, max 200) — first EMA pass.
  - `smooth2: int` (default 3, min 1, max 200) — second EMA pass.
  - `signal_length: int` (default 3, min 1, max 200) — EMA of SMI.
- `default_style`: `smi` teal `#17becf` width 1.4; `signal` orange
  `#ff7f0e` width 1.2.
- `scannable_outputs = (("smi","numeric"),("signal","numeric"))` — both lines exposed to the scanner.
- `reference_levels = (-40.0, 0.0, 40.0)` (class attribute).
- `compute(candles) -> {"smi": ndarray, "signal": ndarray}`.

## Dependencies
- Internal: `..models.Candle`, `.base.LineStyle`, `.base.ParamDef`,
  `._iir.ema_first_seeded_nan` (vectorised NaN-skipping EMA recurrence).
- External: `numpy`.

## Design Decisions
- **Double-EMA on numerator and denominator separately** — Blau's
  original construction. Smoothing the ratio instead would collapse
  range spikes into the SMI.
- **Recursive EMA seeded at first finite sample with that sample's
  value** (same convention as the `EMA` indicator). Preserves the
  `length-1` HH/LL warmup as NaN. `_ema_with_nan` delegates to the
  shared closed-form kernel `_iir.ema_first_seeded_nan` (no per-bar
  Python loop); output is bit-equivalent to the former loop.
- **Reference levels at −40, 0, +40** drawn by default. Blau's
  convention: > +40 ≈ overbought, < −40 ≈ oversold; SMI/signal
  crossovers are the primary entry trigger.
- **Flat-market continuity.** When rolling H−L collapses to 0, SMI
  emits 0 (not NaN) to keep the line continuous. Matches Blau's
  published convention.

## Invariants
- Both outputs are length `len(candles)`.
- First `length-1` indices are NaN.
- Defined `smi` values are in roughly `[-100, +100]` (bound is
  approximate — a perfectly directional move can pin numerator to
  denominator).
- Validation: `length < 2`, `smooth1 < 1`, `smooth2 < 1`, or
  `signal_length < 1` raises `ValueError`.

## Data Flow / Algorithm
```
HH    = max(high[i-N+1 .. i])
LL    = min(low [i-N+1 .. i])
mid   = (HH + LL) / 2
dist  = close[i] - mid
range = HH - LL

sd1   = EMA(dist,  smooth1)
sd2   = EMA(sd1,   smooth2)
sr1   = EMA(range, smooth1)
sr2   = EMA(sr1,   smooth2)

smi    = 100 * sd2 / (sr2 / 2)        # 0 when sr2 == 0
signal = EMA(smi, signal_length)
```

## Known limitations
- HH/LL computed via per-index slice (`O(N·L)`). Trivial at typical
  N≈500 / L≈14; a deque-based rolling-extreme would be needed for
  larger windows.
