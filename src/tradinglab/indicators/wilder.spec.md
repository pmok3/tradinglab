# indicators/wilder.py — Spec

## Purpose
J. Welles Wilder's recursive smoothing primitives plus the True-Range
definition — shared by ADX, ATR, Chandelier Stops, Keltner (atr
method), and the `rma` kernel in `ma_kernels`. Centralised so one bug
fix to Wilder semantics covers every consumer.

## Public API
- `wilder_smooth_sum(arr, length) -> np.ndarray` — Wilder smoothing in
  **sum** form. Seeds at `first_valid + length - 1` with the *sum* of
  the first `length` valid samples, then applies
  `S_i = S_{i-1} − S_{i-1}/length + arr[i]`. ADX consumes the sum form
  because its `+DI` / `-DI` ratios are `smoothed_sum(DM) /
  smoothed_sum(TR)` and sum/sum cancels cleanly.
- `wilder_smooth_avg(arr, length) -> np.ndarray` — same shape as the
  sum form but seeds at the *mean* of the first `length` valid samples
  and uses `S_i = S_{i-1} · (1 − 1/length) + arr[i] · (1/length)` —
  i.e. an EMA with `alpha = 1/length`. Output carries the same units
  as `arr`. Re-exported as `rma` by `ma_kernels`; used directly by ATR
  and ADX's outer DX-smoothing pass.
- `true_range(highs, lows, closes) -> np.ndarray`:
  `TR[i] = max(high[i] − low[i], |high[i] − close[i-1]|,
              |low[i]  − close[i-1]|)`.
  Index 0 is NaN. All three inputs must be the same length.

## Dependencies
- External: `numpy`.

## Design Decisions
- **Two seeding forms (sum and average).** Algebraically related
  (`avg = sum / length`) but with distinct seed scales so downstream
  callers don't need to multiply or divide by `length`.
- **Leading NaN skipped when locating `first_valid`.** TR's index 0
  is NaN by definition; the smoothers must start at the first finite
  index, not 0.
- **Mid-stream NaNs treated as 0 in the recurrence** — keeps the line
  continuous through gap rows.
- **Vectorized chunked closed form.** Both smoothers delegate to the
  private `_wilder_iir_vec`, which evaluates the same recurrence with
  chunked NumPy `cumsum` substitution instead of a per-bar Python loop.
- **`true_range` is the single TR primitive** for ADX, ATR,
  Chandelier Stops, and Keltner (`method="atr"`) — no parallel
  implementations.

## Invariants
- `wilder_smooth_sum(arr, n)` and `wilder_smooth_avg(arr, n)`:
  - Output shape `== arr.shape`.
  - Indices `[0, first_valid + n - 1)` are NaN.
  - `length < 1` or `arr.size == 0` → all-NaN output (no exception).
  - All-NaN input → all-NaN output.
- `true_range(highs, lows, closes)`:
  - Output shape `== highs.shape`.
  - `TR[0]` is NaN; `TR[i] >= 0` for every defined `i`.
  - `n < 2` → all-NaN output.
