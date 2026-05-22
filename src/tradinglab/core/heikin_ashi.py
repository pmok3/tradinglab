"""Heikin-Ashi candle computation.

Heikin-Ashi ("average bar") is a derived candle representation used to
visually smooth raw OHLC and surface trend continuation. The transform
is purely visual / advisory:

* Real OHLC remains the source of truth for indicators, scanner
  conditions backed by indicators, hover readouts, autoscale ranges,
  and screenshots of the prices axis.
* HA arrays are substituted only at the candle-glyph draw site (and
  exposed as dedicated ``ha_*`` builtin scanner fields), so toggling
  HA never silently changes the math elsewhere.

Formulas (canonical, satisfies most platforms incl. TradingView /
ThinkOrSwim):

    HA_Close[i]  = (O[i] + H[i] + L[i] + C[i]) / 4
    HA_Open[0]   = (O[0] + C[0]) / 2                         # seed
    HA_Open[i]   = (HA_Open[i-1] + HA_Close[i-1]) / 2        # i >= 1
    HA_High[i]   = max(H[i], HA_Open[i], HA_Close[i])
    HA_Low[i]    = min(L[i], HA_Open[i], HA_Close[i])

NaN handling
------------
NaN inputs (gap placeholders, missing data) propagate to NaN HA values
at the same index. The recurrence skips NaN seeds: if ``HA_Open[i-1]``
or ``HA_Close[i-1]`` is NaN, ``HA_Open[i]`` is re-seeded from
``(O[i] + C[i]) / 2`` so a single gap doesn't poison the entire suffix.

Public API
----------

* :func:`ha_arrays(open_, high, low, close) -> (ha_o, ha_h, ha_l, ha_c)`
  — pure NumPy, four float64 arrays of equal length matching inputs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ..models import Candle  # noqa: F401


def ha_arrays(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return Heikin-Ashi (open, high, low, close) arrays.

    All four inputs must be 1-D arrays of equal length. Values are
    treated as float64 internally; NaN is preserved per the policy
    described in the module docstring.
    """
    o = np.asarray(open_, dtype=np.float64)
    h = np.asarray(high,  dtype=np.float64)
    lo = np.asarray(low,   dtype=np.float64)
    c = np.asarray(close, dtype=np.float64)
    n = c.size
    if not (o.size == h.size == lo.size == n):
        raise ValueError("ha_arrays: input arrays must be equal length")
    if n == 0:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty.copy(), empty.copy(), empty.copy()

    ha_c = (o + h + lo + c) / 4.0
    ha_o = np.empty(n, dtype=np.float64)
    # Sequential recurrence — must be a Python loop because
    # ha_o[i] depends on ha_o[i-1] and ha_c[i-1]. n is small in
    # practice (single-symbol prefix; usually <2000 bars).
    seed0 = (o[0] + c[0]) / 2.0
    ha_o[0] = seed0
    for i in range(1, n):
        prev_o = ha_o[i - 1]
        prev_c = ha_c[i - 1]
        if np.isnan(prev_o) or np.isnan(prev_c):
            # Re-seed across NaN runs so a single gap doesn't
            # poison the rest of the series.
            ha_o[i] = (o[i] + c[i]) / 2.0
        else:
            ha_o[i] = (prev_o + prev_c) / 2.0

    ha_h = np.maximum.reduce([h,  ha_o, ha_c])
    ha_l = np.minimum.reduce([lo, ha_o, ha_c])
    return ha_o, ha_h, ha_l, ha_c


def heikin_ashi_candles(candles: list[Candle]) -> list[Candle]:
    """Return a parallel candle list with HA OHLC but original metadata.

    Used by the renderer when the View → Heikin-Ashi Candles toggle is
    on. The returned candles share the input's ``date``, ``volume``,
    and ``session`` fields verbatim — only ``open``/``high``/``low``/
    ``close`` are substituted — so downstream code that branches on
    ``is_extended`` / ``is_gap`` / volume continues to work identically.

    Gap candles in the input remain gap candles in the output (NaN
    OHLC is preserved by :func:`ha_arrays`).

    The returned list is a fresh shallow list of fresh ``Candle``
    instances; the caller may mutate it without disturbing the source.
    """
    from ..models import Candle  # local import to avoid cycles
    from .bars import Bars
    n = len(candles)
    if n == 0:
        return []
    bars = Bars.from_candles(candles)
    ha_o, ha_h, ha_l, ha_c = ha_arrays(bars.open, bars.high, bars.low, bars.close)
    out: list[Candle] = []
    for i, src in enumerate(candles):
        if src.is_gap:
            # Preserve gap placeholders verbatim.
            out.append(src)
            continue
        out.append(Candle(
            date=src.date,
            open=float(ha_o[i]),
            high=float(ha_h[i]),
            low=float(ha_l[i]),
            close=float(ha_c[i]),
            volume=src.volume,
            session=src.session,
        ))
    return out
