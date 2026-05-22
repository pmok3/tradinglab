"""Moving-average kernels — SMA / EMA / WMA / RMA.

Used by indicators (Bollinger Bands, ATR, ...) that expose a
user-selectable ``ma_type`` parameter. All four functions share a
common contract:

* Input: 1-D ``np.ndarray`` of finite floats (with optional leading
  NaNs — typical of a True-Range series whose index 0 is NaN). Mid-stream
  NaNs are treated as 0 in the recurrence to keep the line continuous.
* Output: same-shape array with ``NaN`` until the first index where
  the MA is fully defined. The first finite output index is
  ``first_valid + length - 1`` for SMA / WMA / RMA; for EMA we use
  the same warmup mask for visual parity even though the recurrence
  could publish from index 0 onward.
* ``length >= 1``; callers should validate larger minimums.

The four kernels are kept in one module so a single ``apply_ma(kind,
arr, length)`` dispatcher can route by string without each indicator
re-importing each kernel individually.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .wilder import wilder_smooth_avg as _wilder_smooth_avg

MA_TYPES: Tuple[str, ...] = ("SMA", "EMA", "WMA", "RMA")


def _first_valid(arr: np.ndarray) -> int:
    """Return the first index of a finite value, or -1 if none."""
    mask = np.isfinite(arr)
    indices = np.flatnonzero(mask)
    return int(indices[0]) if indices.size > 0 else -1


def sma(arr: np.ndarray, length: int) -> np.ndarray:
    """Simple Moving Average. NaN until first_valid + length - 1."""
    out = np.full_like(arr, np.nan, dtype=np.float64)
    n = arr.size
    if n == 0 or length < 1:
        return out
    first = _first_valid(arr)
    if first < 0 or first + length > n:
        return out
    cleaned = np.where(np.isfinite(arr), arr, 0.0)
    cs = np.concatenate(([0.0], np.cumsum(cleaned)))
    seed_end = first + length  # exclusive
    idx = np.arange(seed_end - 1, n)
    out[idx] = (cs[idx + 1] - cs[idx + 1 - length]) / length
    return out


def ema(arr: np.ndarray, length: int) -> np.ndarray:
    """Exponential Moving Average, alpha = 2/(length+1).

    Seeded with the **SMA of the first ``length`` valid samples**,
    published at index ``first_valid + length - 1``. Recurrence runs
    from that seed onward. This matches TradingView and TA-Lib's
    EMA (and the standalone :class:`EMA` indicator). It differs from
    ``pandas.ewm(adjust=False)``, which seeds at the first sample.
    """
    out = np.full_like(arr, np.nan, dtype=np.float64)
    n = arr.size
    if n == 0 or length < 1:
        return out
    first = _first_valid(arr)
    if first < 0 or first + length > n:
        return out
    alpha = 2.0 / (length + 1.0)
    seed_end = first + length  # exclusive of the last seed input
    window = arr[first:seed_end]
    cleaned_seed = np.where(np.isfinite(window), window, 0.0)
    seed = float(cleaned_seed.mean())
    out[seed_end - 1] = seed
    prev = seed
    for i in range(seed_end, n):
        v = arr[i]
        if not np.isfinite(v):
            v = 0.0
        prev = alpha * float(v) + (1.0 - alpha) * prev
        out[i] = prev
    return out


def wma(arr: np.ndarray, length: int) -> np.ndarray:
    """Linearly-weighted Moving Average — weights 1, 2, ..., length.

    The most recent bar carries weight ``length`` and the oldest
    weight ``1``; sum of weights is ``length*(length+1)/2``. NaN
    until ``first_valid + length - 1``.
    """
    out = np.full_like(arr, np.nan, dtype=np.float64)
    n = arr.size
    if n == 0 or length < 1:
        return out
    first = _first_valid(arr)
    if first < 0 or first + length > n:
        return out
    cleaned = np.where(np.isfinite(arr), arr, 0.0)
    weights = np.arange(1, length + 1, dtype=np.float64)
    wsum = float(weights.sum())
    seed_end = first + length
    windows = sliding_window_view(cleaned[first:], length)
    weighted = (windows * weights).sum(axis=1)
    out[seed_end - 1: seed_end - 1 + weighted.size] = weighted / wsum
    return out


def rma(arr: np.ndarray, length: int) -> np.ndarray:
    """Wilder's RMA — EMA with ``alpha = 1/length``, seeded at the
    *mean* of the first ``length`` valid samples.

    Thin re-export of :func:`indicators.wilder.wilder_smooth_avg`.
    The MA dispatcher (and any future ``ma_type``-driven indicator)
    routes through this name so the module stands alone as the
    MA-kernel catalogue, while the single source of truth lives in
    :mod:`indicators.wilder` (used by ADX / ATR directly).
    """
    return _wilder_smooth_avg(arr, length)


_DISPATCH = {
    "SMA": sma,
    "EMA": ema,
    "WMA": wma,
    "RMA": rma,
}


def apply_ma(kind: str, arr: np.ndarray, length: int) -> np.ndarray:
    """Dispatch to one of :func:`sma` / :func:`ema` / :func:`wma` /
    :func:`rma` by case-insensitive ``kind`` string.

    Raises :class:`ValueError` for an unknown ``kind``.
    """
    key = str(kind).upper()
    fn = _DISPATCH.get(key)
    if fn is None:
        raise ValueError(
            f"unknown ma_type {kind!r}; expected one of {MA_TYPES}"
        )
    return fn(arr, length)
