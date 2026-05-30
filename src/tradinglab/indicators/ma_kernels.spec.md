# indicators/ma_kernels.py — Spec

## Purpose
Catalogue of four moving-average kernels — `SMA`, `EMA`, `WMA`, `RMA`
— under a single `apply_ma(kind, arr, length)` dispatcher. Used by
indicators that expose a user-selectable `ma_type` ParamDef (Bollinger,
Keltner, ATR, MACD, Chandelier Stops).

## Public API
- `MA_TYPES: Tuple[str, ...] = ("SMA", "EMA", "WMA", "RMA")` — ordered
  catalogue. Re-exported by consumers as the `choices=` value.
- `sma(arr, length) -> np.ndarray` — simple rolling mean. NaN until
  `first_valid + length - 1`.
- `ema(arr, length) -> np.ndarray` — exponential MA with
  `alpha = 2/(length+1)`, seeded with the SMA of the first `length`
  valid samples and published at index `first_valid + length - 1`.
  Matches TradingView / TA-Lib (differs from `pandas.ewm(adjust=False)`
  which seeds at the first sample).
- `wma(arr, length) -> np.ndarray` — linearly-weighted MA with weights
  `1, 2, ..., length`. Sum of weights is `length*(length+1)/2`. NaN
  until `first_valid + length - 1`.
- `rma(arr, length) -> np.ndarray` — Wilder's RMA. Thin re-export of
  `wilder.wilder_smooth_avg` so the catalogue is self-contained while
  the single source of truth lives in `wilder.py`.
- `apply_ma(kind, arr, length) -> np.ndarray` — case-insensitive
  dispatch. Raises `ValueError` for unknown `kind`.

## Dependencies
- Internal: `.wilder.wilder_smooth_avg` (re-exported as `rma`);
  `._iir.ema_sma_seeded` (vectorised EMA recurrence kernel).
- External: `numpy`.

## Design Decisions
- **`ema` is loop-free.** The EMA recurrence is evaluated by the
  shared closed-form kernel `_iir.ema_sma_seeded` (chunked-cumsum), not
  a per-bar Python loop. Output is bit-equivalent to the former loop
  (pinned by `tests/unit/indicators/test_iir_vectorization.py`).
- **Common contract across all four kernels.** Input is a 1-D
  `np.ndarray` of finite floats with optional leading NaNs. Mid-stream
  NaNs are treated as 0 in the recurrence so the line stays
  continuous. Output is same-shape with NaN until the first defined
  index.
- **Uniform warmup mask.** Every kernel publishes its first finite
  value at `first_valid + length - 1`. EMA could publish from index 0
  but uses the same mask for visual parity — a chart with `BB(EMA)`
  vs `BB(SMA)` shouldn't show one starting earlier than the other.
- **RMA delegates to `wilder.wilder_smooth_avg`** — ATR / ADX consume
  the Wilder primitive directly; this module re-exports it under the
  `rma` name so one bug fix covers everyone.
- **String dispatch (not enum / class)** — persisted `ma_type` is a
  short string in `params`; routing by string keeps indicator
  implementations one-liners.
- **`_first_valid` and `_DISPATCH` are private** — `apply_ma` is the
  only contract.

## Invariants
- For every kernel, `out.shape == arr.shape`.
- Indices `[0, first_valid + length - 1)` are NaN.
- `length < 1` or `arr.size == 0` → all-NaN output.
- `apply_ma("sma" | "SMA" | "Sma", ...)` resolves identically.
- `apply_ma("RMA", arr, n) is wilder.wilder_smooth_avg(arr, n)` in
  observable behavior.
