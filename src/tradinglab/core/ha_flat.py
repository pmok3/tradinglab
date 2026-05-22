"""Heikin-Ashi *flat* pattern detection (strong-trend visual signal).

A "flat" HA candle is one of the two classic strong-trend continuation
patterns:

* **Bull flat-bottom**: ``HA_close > HA_open`` AND ``HA_low == HA_open``
  (the candle has no lower wick — the bar opened at its low and went
  up). Read by traders as confirmation of an active uptrend.
* **Bear flat-top**: ``HA_close < HA_open`` AND ``HA_high == HA_open``
  (the candle has no upper wick — the bar opened at its high and went
  down). Read by traders as confirmation of an active downtrend.

Doji bars (``HA_open == HA_close``) are deliberately excluded — they
have no direction so neither variant fires. Bars whose HA values are
NaN (warm-up, gap re-seed) propagate to ``False`` / ``UNKNOWN``.

Equality uses the same price-scaled tolerance the scanner builtins use
(:func:`scanner.fields._ha_flat_eps`) so the chart highlight and the
``ha_flat_*`` scanner fields agree on every bar:

    eps = max(1e-9, abs(price) * 1e-9)

Pure NumPy. Safe to call from worker threads. Tiny LRU cache lives in
:mod:`scanner.fields` (the :class:`HAFlatArrays` shape is reused there).

Public API
----------
* :func:`compute_ha_flat_arrays(candles) -> HAFlatArrays` — full per-bar
  derivation set: ``bull_flat_bottom`` (bool), ``bear_flat_top`` (bool),
  ``signed`` (int8: +1 / -1 / 0 / UNKNOWN).
* :func:`compute_ha_flat_arrays_np(open_, high, low, close) -> HAFlatArrays`
  — array-input variant for the scanner cache (mirrors
  :func:`core.heikin_ashi.ha_arrays`'s signature).
* :data:`HA_FLAT_NONE`, :data:`HA_FLAT_BULL`, :data:`HA_FLAT_BEAR`,
  :data:`HA_FLAT_UNKNOWN` — sentinel values for ``signed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .bars import Bars
from .heikin_ashi import ha_arrays

if TYPE_CHECKING:  # pragma: no cover
    from ..models import Candle


# Sentinel values for ``signed``. Mirrors ``core.key_bar``'s convention:
# int8 with -128 reserved for "unknown" (NaN inputs / empty series tail).
HA_FLAT_NONE: int = 0
HA_FLAT_BULL: int = 1     # bull flat-bottom (no lower wick on green bar)
HA_FLAT_BEAR: int = -1    # bear flat-top (no upper wick on red bar)
HA_FLAT_UNKNOWN: int = -128


@dataclass(frozen=True)
class HAFlatArrays:
    """Per-bar HA flat-pattern masks.

    All three arrays share the same length as the input candle/array
    sequence. Indices align 1-to-1 with the source bars.
    """

    bull_flat_bottom: np.ndarray  # bool — True iff bull HA bar w/ no lower wick
    bear_flat_top: np.ndarray     # bool — True iff bear HA bar w/ no upper wick
    signed: np.ndarray            # int8 — +1 / -1 / 0 / -128 (UNKNOWN)

    def __len__(self) -> int:
        return int(self.signed.size)


def _ha_flat_eps(price: np.ndarray) -> np.ndarray:
    """Vectorised tolerance: ``max(1e-9, |price|*1e-9)``.

    Mirrors :func:`scanner.fields._ha_flat_eps` so chart and scanner
    classify every bar identically.
    """
    return np.maximum(1e-9, np.abs(price) * 1e-9)


def _empty_result(n: int) -> HAFlatArrays:
    return HAFlatArrays(
        bull_flat_bottom=np.zeros(n, dtype=bool),
        bear_flat_top=np.zeros(n, dtype=bool),
        signed=np.full(n, HA_FLAT_UNKNOWN, dtype=np.int8),
    )


def compute_ha_flat_arrays_np(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> HAFlatArrays:
    """Array-input variant; computes HA internally then derives flat masks.

    All four inputs must be 1-D float arrays of equal length. NaN
    propagates to ``False`` in the boolean masks and ``UNKNOWN`` in
    ``signed``. Used by the scanner field cluster — the renderer goes
    through :func:`compute_ha_flat_arrays` (candle-list input).
    """
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high,  dtype=np.float64)
    lo = np.asarray(low,   dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = c.size
    if not (o.size == h.size == lo.size == n):
        raise ValueError(
            "compute_ha_flat_arrays_np: input arrays must be equal length"
        )
    if n == 0:
        return _empty_result(0)

    ha_o, ha_h, ha_l, ha_c = ha_arrays(o, h, lo, c)
    return _classify(ha_o, ha_h, ha_l, ha_c)


def compute_ha_flat_arrays(candles: list[Candle]) -> HAFlatArrays:
    """Candle-list variant. Pure function; safe from worker threads.

    Matches the calling shape of :func:`core.key_bar.compute_key_bar_arrays`
    so the renderer's overlay path mirrors the existing key-bar pattern.
    """
    n = len(candles)
    if n == 0:
        return _empty_result(0)

    # ``Candle.is_gap`` placeholders carry NaN OHLC; ``Bars`` preserves
    # those raw values and ``ha_arrays`` already handles NaN runs
    # (re-seeds across them), while the eps comparisons below propagate
    # NaN → False.
    bars = Bars.from_candles(candles)
    o, h, lo, c = bars.open, bars.high, bars.low, bars.close

    ha_o, ha_h, ha_l, ha_c = ha_arrays(o, h, lo, c)
    return _classify(ha_o, ha_h, ha_l, ha_c)


def _classify(
    ha_o: np.ndarray,
    ha_h: np.ndarray,
    ha_l: np.ndarray,
    ha_c: np.ndarray,
) -> HAFlatArrays:
    """Pure derivation step shared by both public entry points."""
    finite = (
        np.isfinite(ha_o) & np.isfinite(ha_h)
        & np.isfinite(ha_l) & np.isfinite(ha_c)
    )

    eps = _ha_flat_eps(ha_o)

    # ``ha_low == ha_open`` and ``ha_high == ha_open`` — eps-tolerant.
    low_eq_open = np.abs(ha_l - ha_o) <= eps
    high_eq_open = np.abs(ha_h - ha_o) <= eps

    # Direction-aware: bull = strict ha_close > ha_open (excludes doji);
    # bear = strict ha_close < ha_open. Doji bars never fire either
    # mask — by design, they're not a strong-trend continuation signal.
    bull = ha_c > ha_o
    bear = ha_c < ha_o

    bull_fb = finite & bull & low_eq_open
    bear_ft = finite & bear & high_eq_open

    signed = np.where(
        ~finite, np.int8(HA_FLAT_UNKNOWN),
        np.where(
            bull_fb, np.int8(HA_FLAT_BULL),
            np.where(bear_ft, np.int8(HA_FLAT_BEAR), np.int8(HA_FLAT_NONE)),
        ),
    ).astype(np.int8)

    return HAFlatArrays(
        bull_flat_bottom=bull_fb,
        bear_flat_top=bear_ft,
        signed=signed,
    )
