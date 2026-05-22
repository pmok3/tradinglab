"""Chandelier stops — shared math (Chuck LeBeau, 1995).

Pure math layer. No matplotlib, no Tk, no main-thread coupling, no
exit-strategy state. Used by:

* :class:`tradinglab.indicators.chandelier.ChandelierStops` (always-on
  overlay indicator — draws the chandelier line at every bar without
  any position context).
* :mod:`tradinglab.exits.spec` (in-trade exit rule — anchored at
  the entry bar, ratcheted forward, fires on touch).

A chandelier stop is a volatility-scaled trailing stop:

* Long stop  = ``highest_high(window) − multiplier × ATR``
* Short stop = ``lowest_low(window)  + multiplier × ATR``

Where ``window`` is a rolling-high (or rolling-low) lookback.

**Camp-B anchor mode** (the only mode this app uses, per user spec):

When ``anchor_idx`` is given, the rolling-high/low *only* looks at bars
from ``anchor_idx`` onward, capped at ``lookback`` bars wide. This
matches LeBeau's "hang the stop from the high *since you entered the
trade*" semantics and avoids the ugly surprise of "stop is already 3
ATR away on bar 1 because of a pre-entry spike". Before ``anchor_idx``
the output is NaN.

When ``anchor_idx`` is ``None`` (always-on indicator mode), the window
is the standard backward-looking ``lookback`` slice of the full series.
Output is NaN until the rolling window has ``lookback`` bars.

ATR warm-up
-----------

Whichever moving-average kernel is selected (RMA / SMA / EMA / WMA) is
applied to Wilder's True Range. The ATR series is NaN until
``atr_period`` valid TR bars are available; chandelier output is
correspondingly NaN at those indices ("NaN-gap" warm-up display per the
user's spec — no SMA-of-TR placeholder).

Ratcheting
----------

Always on (the defining trait of a chandelier). Long stops never
descend; short stops never rise. ``ratchet_prev`` carries the running
high-water-mark forward when this function is invoked in chunks (live
update path); leave it ``None`` for a full recompute.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from ..indicators.ma_kernels import MA_TYPES, apply_ma
from ..indicators.wilder import true_range as _true_range

__all__ = [
    "compute_atr",
    "compute_chandelier_long",
    "compute_chandelier_short",
    "rolling_highest_high_since",
    "rolling_lowest_low_since",
]


def compute_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    atr_period: int,
    ma_type: str,
) -> np.ndarray:
    """Compute ATR from raw OHLC arrays with a selectable kernel.

    Thin wrapper that materialises True-Range then routes through
    :func:`indicators.ma_kernels.apply_ma`. NaN at index 0 (no prior
    close) and NaN until index ``atr_period`` (kernel warm-up).
    """
    if str(ma_type).upper() not in MA_TYPES:
        raise ValueError(
            f"ma_type must be one of {MA_TYPES}; got {ma_type!r}"
        )
    if int(atr_period) < 2:
        raise ValueError(f"atr_period must be >= 2; got {atr_period}")
    tr = _true_range(highs, lows, closes)
    return apply_ma(str(ma_type).upper(), tr, int(atr_period))


def rolling_highest_high_since(
    highs: np.ndarray,
    lookback: int,
    anchor_idx: int | None = None,
) -> np.ndarray:
    """Per-bar rolling highest-high, optionally anchored.

    * ``anchor_idx is None`` (indicator mode): classic backward-looking
      window of ``lookback`` bars. NaN until index ``lookback - 1``.
    * ``anchor_idx is not None`` (exit-rule mode, Camp B): NaN before
      ``anchor_idx``. At ``anchor_idx`` the value is ``highs[anchor_idx]``.
      At ``anchor_idx + k`` it is ``max(highs[max(anchor_idx, i-lookback+1) : i+1])``
      — the window expands forward and is capped at ``lookback``.
    """
    n = int(highs.size)
    out = np.full(n, np.nan, dtype=np.float64)
    L = int(lookback)
    if L < 1 or n == 0:
        return out
    if anchor_idx is None:
        if n < L:
            return out
        windows = sliding_window_view(highs, L)
        out[L - 1:] = np.nanmax(windows, axis=1)
        return out

    a = int(anchor_idx)
    if a < 0 or a >= n:
        return out
    for i in range(a, n):
        start = max(a, i - L + 1)
        window = highs[start: i + 1]
        out[i] = float(np.nanmax(window))
    return out


def rolling_lowest_low_since(
    lows: np.ndarray,
    lookback: int,
    anchor_idx: int | None = None,
) -> np.ndarray:
    """Per-bar rolling lowest-low, optionally anchored. Mirror of
    :func:`rolling_highest_high_since`."""
    n = int(lows.size)
    out = np.full(n, np.nan, dtype=np.float64)
    L = int(lookback)
    if L < 1 or n == 0:
        return out
    if anchor_idx is None:
        if n < L:
            return out
        windows = sliding_window_view(lows, L)
        out[L - 1:] = np.nanmin(windows, axis=1)
        return out

    a = int(anchor_idx)
    if a < 0 or a >= n:
        return out
    for i in range(a, n):
        start = max(a, i - L + 1)
        window = lows[start: i + 1]
        out[i] = float(np.nanmin(window))
    return out


def _ratchet_long(arr: np.ndarray, prev: float | None) -> np.ndarray:
    """In-place ratchet (max-of-running) of a long chandelier series.

    NaN entries pass through untouched. ``prev`` seeds the running max
    so the function can be called incrementally on chunks.
    """
    out = arr.copy()
    running: float | None = prev
    for i in range(out.size):
        v = out[i]
        if not np.isfinite(v):
            continue
        if running is None or v > running:
            running = float(v)
        out[i] = running
    return out


def _ratchet_short(arr: np.ndarray, prev: float | None) -> np.ndarray:
    """In-place ratchet (min-of-running) of a short chandelier series."""
    out = arr.copy()
    running: float | None = prev
    for i in range(out.size):
        v = out[i]
        if not np.isfinite(v):
            continue
        if running is None or v < running:
            running = float(v)
        out[i] = running
    return out


def compute_chandelier_long(
    highs: np.ndarray,
    atr_values: np.ndarray,
    lookback: int,
    multiplier: float,
    *,
    anchor_idx: int | None = None,
    ratchet_prev: float | None = None,
) -> tuple[np.ndarray, float | None]:
    """Long chandelier stop = highest_high(window) − multiplier × ATR.

    Returns ``(stops, final_ratchet)`` where ``final_ratchet`` is the
    last finite ratcheted stop (useful as ``ratchet_prev`` for an
    incremental next-call). Both NaN where the window is undefined OR
    ATR has not warmed up.
    """
    if int(lookback) < 1:
        raise ValueError(f"lookback must be >= 1; got {lookback}")
    if not np.isfinite(float(multiplier)) or float(multiplier) <= 0:
        raise ValueError(f"multiplier must be > 0; got {multiplier!r}")
    if highs.shape != atr_values.shape:
        raise ValueError(
            f"highs and atr_values must have same shape; "
            f"got {highs.shape} vs {atr_values.shape}"
        )
    hh = rolling_highest_high_since(highs, lookback, anchor_idx)
    raw = hh - float(multiplier) * atr_values
    ratcheted = _ratchet_long(raw, ratchet_prev)
    finite = ratcheted[np.isfinite(ratcheted)]
    final = float(finite[-1]) if finite.size else None
    return ratcheted, final


def compute_chandelier_short(
    lows: np.ndarray,
    atr_values: np.ndarray,
    lookback: int,
    multiplier: float,
    *,
    anchor_idx: int | None = None,
    ratchet_prev: float | None = None,
) -> tuple[np.ndarray, float | None]:
    """Short chandelier stop = lowest_low(window) + multiplier × ATR.

    Mirror of :func:`compute_chandelier_long`. Returns
    ``(stops, final_ratchet)``.
    """
    if int(lookback) < 1:
        raise ValueError(f"lookback must be >= 1; got {lookback}")
    if not np.isfinite(float(multiplier)) or float(multiplier) <= 0:
        raise ValueError(f"multiplier must be > 0; got {multiplier!r}")
    if lows.shape != atr_values.shape:
        raise ValueError(
            f"lows and atr_values must have same shape; "
            f"got {lows.shape} vs {atr_values.shape}"
        )
    ll = rolling_lowest_low_since(lows, lookback, anchor_idx)
    raw = ll + float(multiplier) * atr_values
    ratcheted = _ratchet_short(raw, ratchet_prev)
    finite = ratcheted[np.isfinite(ratcheted)]
    final = float(finite[-1]) if finite.size else None
    return ratcheted, final
