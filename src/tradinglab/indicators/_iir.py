"""Shared vectorised first-order IIR (linear-recurrence) kernel.

Many indicators are dominated by a per-bar Python loop that evaluates a
first-order linear recurrence of the form::

    out[k] = q * out[k-1] + b[k]

(EMA, Wilder RMA, the SMI double-EMA, and each stage of the Laguerre RSI
cascade are all instances of this with different ``q`` and input term
``b``). Evaluated as a Python loop this is O(N) interpreter overhead —
80-330 ms on a 100k-bar 1-minute history.

:func:`iir_tail` solves the recurrence in closed form using the same
chunked-``cumsum`` substitution that :mod:`indicators.wilder` already uses
for Wilder smoothing::

    out[k] = q^(k+1) * seed + q^k * cumsum_{j<=k} (b[j] * q^(-j))

To keep ``q^(-j)`` inside float64 range the tail is processed in chunks
sized so ``q^(-chunk) < 1e15``; the running ``seed`` is chained between
chunks. Output matches the per-bar recurrence to within float64 round-off.

This module is the single source of truth for that substitution; EMA
(``ma_kernels``), the SMI NaN-aware EMA, and the Laguerre RSI cascade all
route through it.
"""

from __future__ import annotations

import numpy as np

_MAX_CHUNK = 4096
_LOG10_BUDGET = 15.0  # keep q^(-chunk) below 1e15


def _chunk_size(q: float) -> int:
    """Largest chunk whose ``q^(-chunk)`` stays inside float64 headroom."""
    log10_q = float(np.log10(q))  # negative for q in (0, 1)
    chunk = int(np.floor(_LOG10_BUDGET / -log10_q))
    return max(1, min(chunk, _MAX_CHUNK))


def iir_tail(tail_b: np.ndarray, q: float, seed: float) -> np.ndarray:
    """Vectorised first-order IIR over a contiguous tail.

    Returns ``out`` with ``out[k] = q * out[k-1] + tail_b[k]`` and the
    implicit predecessor ``out[-1] = seed``. ``tail_b`` must be a finite
    float array (callers clean NaNs first). Bit-equivalent to the scalar
    loop within float64 round-off for ``q`` in ``(0, 1)``.
    """
    b = np.asarray(tail_b, dtype=np.float64)
    m = int(b.size)
    out = np.empty(m, dtype=np.float64)
    if m == 0:
        return out
    if q == 0.0:
        # Recurrence collapses to out[k] = tail_b[k].
        np.copyto(out, b)
        return out
    if not (0.0 < q < 1.0):
        # Outside the stable range the closed form would overflow; fall
        # back to the exact scalar loop (rare — EMA/RMA/Laguerre all keep
        # q in (0, 1)).
        prev = float(seed)
        for k in range(m):
            prev = q * prev + float(b[k])
            out[k] = prev
        return out

    chunk = _chunk_size(q)
    prev = float(seed)
    pos = 0
    while pos < m:
        end = min(pos + chunk, m)
        sub = b[pos:end]
        sz = sub.size
        j = np.arange(sz, dtype=np.float64)
        inv_q_pow = np.power(1.0 / q, j)   # q^(-j), j = 0..sz-1
        q_pow = np.power(q, j)             # q^k
        cum = np.cumsum(sub * inv_q_pow)
        out_slice = q_pow * (q * prev) + q_pow * cum
        out[pos:end] = out_slice
        prev = float(out_slice[-1])
        pos = end
    return out


def ema_sma_seeded(arr: np.ndarray, length: int) -> np.ndarray:
    """EMA seeded with the SMA of the first ``length`` valid samples.

    ``alpha = 2/(length+1)``. Output is NaN before
    ``first_valid + length - 1``; mid-stream NaNs are treated as 0 in the
    recurrence (continuous line). Matches the canonical TradingView/TA-Lib
    seeding used by :func:`ma_kernels.ema` and the :class:`EMA` indicator.
    """
    out = np.full_like(arr, np.nan, dtype=np.float64)
    n = int(arr.size)
    if n == 0 or length < 1:
        return out
    mask = np.isfinite(arr)
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return out
    first = int(idx[0])
    seed_end = first + length  # exclusive
    if seed_end > n:
        return out
    alpha = 2.0 / (length + 1.0)
    window = arr[first:seed_end]
    seed = float(np.where(np.isfinite(window), window, 0.0).mean())
    out[seed_end - 1] = seed
    if seed_end >= n:
        return out
    tail = arr[seed_end:].astype(np.float64, copy=True)
    tail[~np.isfinite(tail)] = 0.0
    out[seed_end:] = iir_tail(alpha * tail, 1.0 - alpha, seed)
    return out


def ema_first_seeded_nan(arr: np.ndarray, length: int) -> np.ndarray:
    """NaN-skipping EMA seeded at the first finite sample's value.

    ``alpha = 2/(length+1)``. Leading NaNs stay NaN; the recurrence seeds
    at the first finite sample with that sample's value, then advances only
    over finite samples (mid-stream NaNs are skipped, their output stays
    NaN). This is the SMI double-EMA convention.
    """
    out = np.full_like(arr, np.nan, dtype=np.float64)
    n = int(arr.size)
    if n == 0:
        return out
    idx = np.flatnonzero(np.isfinite(arr))
    if idx.size == 0:
        return out
    alpha = 2.0 / (length + 1.0)
    f = arr[idx].astype(np.float64, copy=False)
    out[idx[0]] = f[0]
    if f.size > 1:
        out[idx[1:]] = iir_tail(alpha * f[1:], 1.0 - alpha, float(f[0]))
    return out
