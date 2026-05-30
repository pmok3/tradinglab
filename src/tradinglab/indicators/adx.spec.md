# indicators/adx.py — Spec

## Purpose
Wilder's Average Directional Index — a trend-strength oscillator in
`[0, 100]` with two companion direction lines (`+DI`, `-DI`). Drawn
in its own pane; a horizontal guide at 25 marks the canonical
"trending" threshold.

## Public API
- `class ADX(length=14)` — `kind_id="adx"`, `kind_version=1`,
  `overlay=False`, `reference_levels=(25.0,)`.
- `params_schema`: one `ParamDef` (`length`, int, default 14,
  min 2, max 2000).
- `default_style`: `plus_di` green `#2ca02c`, `minus_di` red
  `#d62728`, `adx` grey `#7f7f7f` (widths 1.2 / 1.2 / 1.6).
- `scannable_outputs = (("adx","numeric"),("+di","numeric"),("-di","numeric"))` — exposes ADX line and DI lines to the scanner. **Pre-existing key inconsistency:** the keys `+di`/`-di` declared here do NOT match `compute()`'s output keys (`plus_di`/`minus_di`); queries for `+di`/`-di` resolve to `None` through `out.get(key)`. Preserved verbatim for back-compat with existing scanner / entries / exits FieldRef persistence; `tests/unit/gui/test_scanner_tab_rank_presets.py` pins the names. Use `plus_di`/`minus_di` if you need numeric values — those are unrouted but available on the indicator's compute output dict.
- `compute(candles) -> {"plus_di": ndarray, "minus_di": ndarray,
  "adx": ndarray}`. Raises `ValueError` on `length < 2`.
- `warmup_bars` property returns `4 * length` for strategy-tester
  hydration; first finite ADX is earlier, but the chained Wilder IIR
  continues converging after first emit.

## Dependencies
- Internal: `..core.bars.Bars`, `.base.BaseIndicator`,
  `.base.LineStyle`, `.base.ParamDef`, `.wilder.true_range`,
  `.wilder.wilder_smooth_avg`, `.wilder.wilder_smooth_sum`.
- External: `numpy`.

## Design Decisions
- **Wilder DM tie-breaking.** `+DM = up` only when
  `up > down and up > 0`; `-DM = down` only when
  `down > up and down > 0`. Equal-and-positive counts as zero on both
  legs — matches Wilder 1978.
- **Sum-form Wilder smoothing for DM and TR, average form for ADX.**
  `+DI` / `-DI` are ratios of smoothed sums, so seed-by-sum cancels
  cleanly. ADX itself is averaged so it stays in `[0, 100]`. Both
  forms delegate to `wilder.py`'s vectorized Wilder kernels.
- **Flat-TR continuity** — when smoothed TR collapses to 0,
  `+DI` / `-DI` emit 0 (not NaN). DX → 0 when both DI legs are 0.
- **Reference level at 25** drawn via the class-level
  `reference_levels` tuple.

## Invariants
- `+DI`, `-DI`, `ADX` all in `[0, 100]` at every defined index.
- Output arrays are the same length as the input candles list.
- Equal-and-positive `up == down > 0` produces
  `+DM == -DM == 0`.
- First finite `+DI` / `-DI` at index `length`; first finite ADX at
  index `2·length − 1` (≈ 27 bars at default `length=14`).

## Data Flow / Algorithm
```
TR[i]  = max(high - low, |high - prev_close|, |low - prev_close|)
up     = high[i] - high[i-1]
down   = low[i-1] - low[i]
+DM    = up   if (up > down  and up > 0)   else 0
-DM    = down if (down > up  and down > 0) else 0

S_TR   = wilder_smooth_sum(TR, length)
S_+DM  = wilder_smooth_sum(+DM, length)
S_-DM  = wilder_smooth_sum(-DM, length)

+DI    = 100 * S_+DM / S_TR
-DI    = 100 * S_-DM / S_TR
DX     = 100 * |+DI - -DI| / (+DI + -DI)
ADX    = wilder_smooth_avg(DX, length)
```
