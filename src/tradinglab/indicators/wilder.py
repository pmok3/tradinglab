"""Wilder smoothing (RMA) helpers — shared by ADX, ATR, and any other
indicator that needs J. Welles Wilder's recursive moving average.

Wilder's RMA is equivalent to an EMA with ``alpha = 1/length`` and a
specific seeding rule: the first ``length`` valid samples are averaged
(or summed) to seed the recurrence. Two forms are exposed because
ADX wants the **sum** form (so DI ratios divide cleanly: sum/sum =
average/average) while ATR and ADX's outer pass want the **average**
form (so the result has the same units as the input).
"""

from __future__ import annotations

import numpy as np


def wilder_smooth_sum(arr: np.ndarray, length: int) -> np.ndarray:
    """Wilder smoothing in *sum* form.

    Seeds at index ``first_valid + length - 1`` with the sum of the
    first ``length`` valid samples, then applies Wilder's recurrence
    ``S_i = S_{i-1} - S_{i-1}/length + arr[i]``. Output is NaN before
    the seed index. Leading NaNs in ``arr`` (e.g. TR[0]) are skipped
    when locating ``first_valid``; mid-stream NaNs are treated as 0
    in the recurrence so the line stays continuous.
    """
    out = np.full_like(arr, np.nan)
    n = arr.size
    if n == 0 or length < 1:
        return out
    first = -1
    for i in range(n):
        if np.isfinite(arr[i]):
            first = i
            break
    if first < 0:
        return out
    seed_end = first + length  # exclusive
    if seed_end > n:
        return out
    seed = float(arr[first:seed_end].sum())
    out[seed_end - 1] = seed
    prev = seed
    for i in range(seed_end, n):
        v = arr[i]
        if not np.isfinite(v):
            v = 0.0
        prev = prev - prev / length + float(v)
        out[i] = prev
    return out


def wilder_smooth_avg(arr: np.ndarray, length: int) -> np.ndarray:
    """Wilder smoothing in *average* form (RMA).

    Same shape as :func:`wilder_smooth_sum` but seeds at the *mean*
    of the first ``length`` valid samples and uses
    ``S_i = S_{i-1} * (1 - 1/length) + arr[i] * (1/length)`` —
    equivalent to an EMA with ``alpha = 1/length``. The output has
    the same units as ``arr``.
    """
    out = np.full_like(arr, np.nan)
    n = arr.size
    if n == 0 or length < 1:
        return out
    first = -1
    for i in range(n):
        if np.isfinite(arr[i]):
            first = i
            break
    if first < 0:
        return out
    seed_end = first + length
    if seed_end > n:
        return out
    seed = float(arr[first:seed_end].mean())
    out[seed_end - 1] = seed
    prev = seed
    inv_L = 1.0 / length
    for i in range(seed_end, n):
        v = arr[i]
        if not np.isfinite(v):
            v = 0.0
        prev = prev * (1.0 - inv_L) + float(v) * inv_L
        out[i] = prev
    return out


def true_range(highs: np.ndarray, lows: np.ndarray,
               closes: np.ndarray) -> np.ndarray:
    """Compute Wilder's True Range per bar.

    ``TR[i] = max(high[i] - low[i], |high[i] - close[i-1]|,
                  |low[i]  - close[i-1]|)``

    Index 0 is NaN (no prior close). All input arrays must be the
    same length.
    """
    n = highs.size
    tr = np.full(n, np.nan, dtype=np.float64)
    if n < 2:
        return tr
    prev_close = np.empty_like(closes)
    prev_close[0] = np.nan
    prev_close[1:] = closes[:-1]
    hl = highs - lows
    hpc = np.abs(highs - prev_close)
    lpc = np.abs(lows - prev_close)
    tr = np.where(hpc > hl, hpc, hl)
    tr = np.where(lpc > tr, lpc, tr)
    tr[0] = np.nan
    return tr
