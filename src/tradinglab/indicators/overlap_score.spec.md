# indicators/overlap_score.py — Spec

## Purpose
Overlap Score Inverted (OSI) measures how much of the current candle's
price range is in "new territory" relative to recent candles, with
exponential weighting (recent bars matter more). Complements ATR — ATR
measures range *size*; OSI measures range *location novelty*.

## Public API
- `class OverlapScoreInverted` — lower-pane indicator,
  `kind_id = "overlap_score_inv"`.
  - `compute_arr(bars: Bars) -> {"osi": ndarray}` in `[0, 100]`.
  - `kind_version = 1`; `overlay = False`.
  - No `is_available_for` — works on all intervals.
  - `params_schema`: `lookback` (int, default 10, range 2–200).
  - `default_style`: purple `osi` line, width 1.4.
  - `reference_levels`: 20, 80.

## How it works

For each bar, with lookback `N`:

```
current_range = max(high - low, 0.01)     # 0.01 floor avoids /0 on dojis

# overlap with prior bar k (k=1 is immediately previous):
overlap[k]    = max(0, min(high, high[k]) - max(low, low[k]))
overlap_pct[k] = overlap[k] / current_range

alpha         = max(0.01, 1 - 5 / (N + 1))
raw_weight[k] = alpha ** (k - 1)          # k=1 ≈ 39%, k=2 ≈ 22% (N=10)
norm_w[k]     = raw_weight[k] / Σ raw_weight

overlap_score = Σ norm_w[k] * overlap_pct[k]
OSI           = (1 - overlap_score) * 100
```

**Meaning:** OSI = 0 → entirely within recent ranges; OSI = 100 → zero
overlap with any recent bar (fully new territory).

## ATR + Overlap matrix

| | Low OSI (Consolidation) | High OSI (Expansion) |
|---|---|---|
| **Low ATR** | **Tight coil — spring loading** | Quiet drift/grind |
| **High ATR** | Volatile chop — stay out | **Breakout/momentum** |

## Dependencies
- Internal: `core.bars.Bars`, `indicators.base.BaseIndicator`,
  `indicators.base.LineStyle`, `indicators.base.ParamDef`.
- External: `numpy`, `numpy.lib.stride_tricks.sliding_window_view`.

## Design Decisions
- **Asymmetric normalization by current range** — "what fraction of
  MY range is in old territory?". Not Jaccard, which conflates
  range-size mismatch with positional displacement.
- **Aggressive exponential decay** (`alpha = 1 - 5/(N+1)`, steeper
  than standard EMA convention, clamped to `0.01` for very small
  lookbacks). Bars older than half the lookback are nearly zero-weighted,
  so a single breakout bar immediately moves the score.
- **Inverted scale.** High = new territory (traders expect high
  numbers to mean expansion).
- **Doji handling.** Range floored to 0.01.
- **Vectorized rolling overlap.** The implementation uses
  `sliding_window_view` over high/low arrays and reverses the weight
  vector so the most-recent prior bar receives the first weight.

## Invariants
1. Output in `[0, 100]`.
2. First `lookback` bars are NaN.
3. Output array length equals input length.
