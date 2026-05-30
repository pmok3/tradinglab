# indicators/_iir.py — Spec

## Purpose
Shared, vectorised evaluation of the **first-order linear recurrence**
`out[k] = q*out[k-1] + b[k]` that dominates the runtime of several
indicators (EMA-family kernels, SMI, MACD, Laguerre RSI). Replaces the
per-bar Python loops in those consumers with a closed-form
chunked-cumsum kernel — zero new dependencies, identical float64 output.

## Public API
- `iir_tail(tail_b, q, seed) -> np.ndarray` — evaluates
  `out[k] = q*out[k-1] + tail_b[k]` for `k = 0..len-1` with
  `out[-1] == seed`. Returns an array the same length as `tail_b`.
- `ema_sma_seeded(arr, length) -> np.ndarray` — vectorised replacement
  for `ma_kernels.ema`. EMA with `alpha = 2/(length+1)`, seeded with the
  SMA of the first `length` valid samples and published at index
  `first_valid + length - 1`. Mid-stream NaN is treated as 0.
- `ema_first_seeded_nan(arr, length) -> np.ndarray` — vectorised
  replacement for `smi._ema_with_nan`. EMA seeded at the first finite
  sample's value; mid-stream NaN is **skipped** (recurrence continues at
  the next finite sample; skipped positions stay NaN).

## Design Decisions
- **Closed form, chunked for numerical stability.** The recurrence has
  the exact solution
  `out[k] = q^(k+1)*seed + q^k * cumsum_{j<=k}(b[j] * q^(-j))`.
  Because `q^(-j)` overflows float64 for large `j`, the input is
  processed in chunks sized so `q^(-chunk) < 1e15`; each chunk is seeded
  by the previous chunk's last value. This mirrors the proven pattern in
  `wilder._wilder_iir_vec`.
- **Two EMA seeding conventions are preserved exactly.** `ma_kernels.ema`
  seeds with an SMA and treats mid-stream NaN as 0; `smi._ema_with_nan`
  seeds at the first finite value and skips mid-stream NaN. The latter is
  handled by compress-finite (`np.flatnonzero`) → `iir_tail` → scatter,
  which is bit-exact because skipping a NaN equals continuing the
  recurrence at the next finite sample.
- **Pure numpy.** Numba/Cython were evaluated and rejected for this
  codebase (no win-arm64 llvmlite wheel; MSVC/LLVM build-toolchain and
  PyInstaller cross-arch-freeze complications). Pure-numpy vectorisation
  delivers the speedups with no build or dependency changes.

## Invariants
- `out.shape == arr.shape` for both EMA helpers.
- `iir_tail(empty, q, seed)` returns an empty array.
- Output is bit-equivalent (within float64 round-off) to the original
  scalar reference loops — pinned by
  `tests/unit/indicators/test_iir_vectorization.py`.

## Consumers
- `ma_kernels.ema` (→ MACD, Keltner, Chandelier-ATR, MA indicator).
- `smi._ema_with_nan` (×5 per SMI compute).
- `lrsi.compute_arr` (4-stage Laguerre cascade via sequential `iir_tail`).
