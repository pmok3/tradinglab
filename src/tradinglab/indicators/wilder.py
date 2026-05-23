"""Wilder smoothing (RMA) helpers — shared by ADX, ATR, RSI, and any
other indicator that needs J. Welles Wilder's recursive moving average.

Wilder's RMA is equivalent to an EMA with ``alpha = 1/length`` and a
specific seeding rule: the first ``length`` valid samples are averaged
(or summed) to seed the recurrence. Two forms are exposed because
ADX wants the **sum** form (so DI ratios divide cleanly: sum/sum =
average/average) while ATR and ADX's outer pass want the **average**
form (so the result has the same units as the input).

Both forms are implemented via a chunked closed-form substitution
that turns the per-bar Python recurrence into a single vectorised
numpy ``cumsum``. For a 60-day intraday history the kernel typically
takes ≈1 ms vs. 50–80 ms for the per-bar loop.
"""

from __future__ import annotations

import numpy as np


def _wilder_iir_vec(
    arr: np.ndarray, length: int, *, avg_form: bool,
) -> np.ndarray:
    """Vectorised Wilder smoothing.

    Solves the first-order IIR ``S_i = q*S_{i-1} + a*v_i`` (with
    ``q = (L-1)/L`` and ``a = 1/L`` for the average form, ``a = 1``
    for the sum form) using the closed-form

        S_n = q^n * (S_0 + sum_{k=1..n} a * v_k * q^(-k))

    expressed as ``q_pow * (prev + cumsum(a * v / q^k))``. To keep
    ``q^(-k)`` inside float64 range, the tail is processed in chunks
    sized so ``q^(-chunk_size) < 1e15``; the running ``prev`` is
    chained between chunks. Output matches the per-bar recurrence to
    within float64 round-off.
    """
    out = np.full_like(arr, np.nan, dtype=np.float64)
    n = int(arr.size)
    if n == 0 or length < 1:
        return out

    # Locate first finite sample.
    valid_mask = np.isfinite(arr)
    if not valid_mask.any():
        return out
    first = int(np.argmax(valid_mask))
    seed_end = first + length  # exclusive
    if seed_end > n:
        return out

    # Seed at index seed_end - 1.
    if avg_form:
        seed = float(arr[first:seed_end].mean())
        a = 1.0 / length
    else:
        seed = float(arr[first:seed_end].sum())
        a = 1.0
    out[seed_end - 1] = seed

    if seed_end >= n:
        return out

    # length == 1 ⇒ q == 0; the recurrence collapses to ``S_i = a*v_i``.
    # avg form: a=1/1=1 ⇒ S_i = v_i. sum form: a=1 ⇒ S_i = v_i. Either
    # way the output past the seed equals the cleaned tail.
    if length == 1:
        tail = arr[seed_end:].astype(np.float64, copy=True)
        tail[~np.isfinite(tail)] = 0.0
        out[seed_end:] = tail
        return out

    q = (length - 1.0) / length  # in (0, 1)

    # Mid-stream NaNs are treated as 0 in the recurrence to match the
    # original per-bar loop's behaviour.
    tail = arr[seed_end:].astype(np.float64, copy=True)
    tail[~np.isfinite(tail)] = 0.0

    # Pick chunk so ``q^(-chunk)`` stays inside float64 (cap 1e15 with
    # a safety margin from the 1e308 max). chunk = floor(15 / -log10 q).
    log10_q = float(np.log10(q))  # negative
    chunk = max(1, int(np.floor(15.0 / -log10_q)))
    chunk = min(chunk, 4096)

    prev = seed
    pos = 0
    tail_n = tail.size
    while pos < tail_n:
        end = min(pos + chunk, tail_n)
        sub = tail[pos:end]
        m = sub.size
        k = np.arange(1, m + 1, dtype=np.float64)
        inv_q_pow = np.power(1.0 / q, k)
        q_pow = np.power(q, k)
        cum_f = np.cumsum(a * sub * inv_q_pow)
        out_slice = q_pow * (prev + cum_f)
        out[seed_end + pos:seed_end + end] = out_slice
        prev = float(out_slice[-1])
        pos = end
    return out


def wilder_smooth_sum(arr: np.ndarray, length: int) -> np.ndarray:
    """Wilder smoothing in *sum* form.

    Seeds at index ``first_valid + length - 1`` with the sum of the
    first ``length`` valid samples, then applies Wilder's recurrence
    ``S_i = S_{i-1} - S_{i-1}/length + arr[i]``. Output is NaN before
    the seed index. Leading NaNs in ``arr`` (e.g. TR[0]) are skipped
    when locating ``first_valid``; mid-stream NaNs are treated as 0
    in the recurrence so the line stays continuous.
    """
    return _wilder_iir_vec(arr, length, avg_form=False)


def wilder_smooth_avg(arr: np.ndarray, length: int) -> np.ndarray:
    """Wilder smoothing in *average* form (RMA).

    Same shape as :func:`wilder_smooth_sum` but seeds at the *mean*
    of the first ``length`` valid samples and uses
    ``S_i = S_{i-1} * (1 - 1/length) + arr[i] * (1/length)`` —
    equivalent to an EMA with ``alpha = 1/length``. The output has
    the same units as ``arr``.
    """
    return _wilder_iir_vec(arr, length, avg_form=True)


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
