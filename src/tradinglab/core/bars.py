"""Canonical OHLCV view: ``Bars``.

The single source of truth for "candle list as NumPy columns". Replaces
the parallel evolutions of:

* ``tradinglab.scanner.fields.BarsNp`` (scanner side; OHLCV + timestamps + session)
* ``tradinglab.data.normalize.CandleArrays`` (fetch side; OHLCV only)
* ``tradinglab.core.series.SeriesArrays`` (chart render side; OHLCV + tooltip cache)

…all of which had their own ``np.fromiter`` extraction paths and could
never share a snapshot. ``Bars`` lifts the scanner shape (it's the
fullest of the three) and adds an optional back-reference to the
originating ``List[Candle]`` so legacy ``compute(candles)`` paths can
keep working during the migration.

Design notes
------------

* ``volume`` is float64 (not int64). The chart's ``SeriesArrays`` uses
  ``np.nanmax`` over volumes for gap-tolerant axis-scaling; making
  volume float64 from the start avoids a per-render astype.
* ``timestamps`` is ``datetime64[ns]``, naive UTC. Matches the existing
  ``BarsNp`` convention. ``_to_naive_utc`` is the single conversion
  point; do not bypass it.
* The dataclass is frozen so a ``Bars`` value is safe to share across
  threads without locking. The arrays are not copy-on-write — callers
  must treat them as read-only.
* ``candles`` back-reference is intentionally optional. Callers that
  build a ``Bars`` from raw arrays (e.g. the fetch-side stash) cannot
  always produce a candle list cheaply; indicators that haven't been
  migrated to ``compute_arr`` will need to fall back through
  ``bars.candles`` and will refuse to run if it is ``None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Sequence

import numpy as np

from ..models import Candle


def _to_naive_utc(dt: datetime) -> datetime:
    """Strip timezone (after converting to UTC) so ``np.datetime64`` is happy."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@dataclass(frozen=True)
class Bars:
    """Frozen columnar NumPy view of an OHLCV time series.

    Use :meth:`from_candles` for the candle-list path or
    :meth:`from_arrays` for the prebuilt-arrays path. Both produce
    structurally identical ``Bars`` values; downstream code does not
    care which constructor was used.

    Public attributes
    -----------------
    open, high, low, close : np.ndarray  (float64, shape (n,))
    volume                 : np.ndarray  (float64, shape (n,))
    timestamps             : np.ndarray  (datetime64[ns], shape (n,))
    session                : np.ndarray  (object, shape (n,)) — tags
                                          ``"regular" | "pre" | "post" | "gap"``
    candles                : Optional[List[Candle]]
                                          back-reference for fallback paths
    """

    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    timestamps: np.ndarray
    session: np.ndarray
    candles: Optional[List[Candle]] = field(default=None, repr=False, compare=False)

    def __len__(self) -> int:
        return int(self.close.size)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_candles(cls, candles: Sequence[Candle]) -> "Bars":
        """Single-source ``np.fromiter`` extraction. **The** OHLCV builder."""
        n = len(candles)
        if n == 0:
            empty_f = np.empty(0, dtype=np.float64)
            return cls(
                open=empty_f, high=empty_f, low=empty_f, close=empty_f,
                volume=np.empty(0, dtype=np.float64),
                timestamps=np.empty(0, dtype="datetime64[ns]"),
                session=np.empty(0, dtype=object),
                candles=list(candles) if not isinstance(candles, list) else candles,
            )
        return cls(
            open=np.fromiter((c.open  for c in candles), dtype=np.float64, count=n),
            high=np.fromiter((c.high  for c in candles), dtype=np.float64, count=n),
            low =np.fromiter((c.low   for c in candles), dtype=np.float64, count=n),
            close=np.fromiter((c.close for c in candles), dtype=np.float64, count=n),
            volume=np.fromiter((c.volume for c in candles), dtype=np.float64, count=n),
            timestamps=np.array([_to_naive_utc(c.date) for c in candles],
                                dtype="datetime64[ns]"),
            session=np.array([c.session for c in candles], dtype=object),
            candles=list(candles) if not isinstance(candles, list) else candles,
        )

    @classmethod
    def from_arrays(
        cls,
        *,
        open: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        volume: np.ndarray,
        timestamps: Optional[np.ndarray] = None,
        session: Optional[np.ndarray] = None,
        candles: Optional[List[Candle]] = None,
    ) -> "Bars":
        """Construct from pre-extracted arrays.

        ``timestamps`` and ``session`` may be omitted by callers that
        only have OHLCV (e.g. the fetcher's prebuilt-arrays stash); they
        will be derived from ``candles`` if provided, else filled with
        sentinel values that still satisfy the dtype contract.
        """
        n = int(close.size)
        if timestamps is None:
            if candles is not None and len(candles) == n:
                timestamps = np.array(
                    [_to_naive_utc(c.date) for c in candles],
                    dtype="datetime64[ns]",
                )
            else:
                timestamps = np.empty(n, dtype="datetime64[ns]")
        if session is None:
            if candles is not None and len(candles) == n:
                session = np.array([c.session for c in candles], dtype=object)
            else:
                session = np.full(n, "regular", dtype=object)
        # Volume is always float64 in Bars; tolerate int64 input.
        if volume.dtype != np.float64:
            volume = volume.astype(np.float64)
        return cls(
            open=open, high=high, low=low, close=close, volume=volume,
            timestamps=timestamps, session=session, candles=candles,
        )

    # ------------------------------------------------------------------
    # Convenience views
    # ------------------------------------------------------------------

    def typical_price(self) -> np.ndarray:
        """``(high + low + close) / 3`` — VWAP / classic-pivot input."""
        return (self.high + self.low + self.close) / 3.0
