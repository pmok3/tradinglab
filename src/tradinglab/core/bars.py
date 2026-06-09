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

import calendar
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from ..models import Candle


def _to_naive_utc(dt: datetime) -> datetime:
    """Strip timezone (after converting to UTC) so ``np.datetime64`` is happy."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _epoch_ns(dt: datetime) -> int:
    """Naive-UTC epoch nanoseconds for ``dt`` — bit-identical to
    ``np.datetime64(_to_naive_utc(dt), "ns")`` but without the per-bar
    ``astimezone`` object allocation.

    * tz-aware: ``round(dt.timestamp() * 1e6) * 1000``. ``timestamp()`` is
      the fast C path (no intermediate datetime). Whole-second bars (every
      real OHLCV candle) are exactly representable as float through year
      ~2096, so the round-trip is exact for any market data this app sees.
    * naive: the wall-clock fields are taken verbatim as UTC (matching
      ``_to_naive_utc``, which returns a naive datetime unchanged), via
      integer ``calendar.timegm`` — ``timestamp()`` must NOT be used here
      because it would reinterpret the fields in the local zone.
    """
    if dt.tzinfo is not None:
        return round(dt.timestamp() * 1_000_000) * 1000
    return (calendar.timegm(dt.timetuple()) * 1_000_000 + dt.microsecond) * 1000


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
    candles: list[Candle] | None = field(default=None, repr=False, compare=False)

    def __len__(self) -> int:
        return int(self.close.size)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_candles(cls, candles: Sequence[Candle]) -> Bars:
        """Single-pass OHLCV + timestamp + session extraction. **The** OHLCV builder.

        One Python loop fills six pre-allocated numpy arrays plus the
        object session array, replacing the former five ``np.fromiter``
        passes + two ``np.array`` list-comprehensions (seven walks of the
        candle list). Timestamps are accumulated as ``int64`` epoch-ns via
        :func:`_epoch_ns` (no per-bar ``astimezone``) and reinterpreted as
        ``datetime64[ns]`` with a zero-copy ``.view`` — bit-identical to the
        old ``np.array([_to_naive_utc(c.date) …], "datetime64[ns]")`` path.
        """
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
        open_ = np.empty(n, dtype=np.float64)
        high = np.empty(n, dtype=np.float64)
        low = np.empty(n, dtype=np.float64)
        close = np.empty(n, dtype=np.float64)
        volume = np.empty(n, dtype=np.float64)
        ts = np.empty(n, dtype=np.int64)
        session = np.empty(n, dtype=object)
        for i, c in enumerate(candles):
            open_[i] = c.open
            high[i] = c.high
            low[i] = c.low
            close[i] = c.close
            volume[i] = c.volume
            session[i] = c.session
            ts[i] = _epoch_ns(c.date)
        return cls(
            open=open_, high=high, low=low, close=close, volume=volume,
            timestamps=ts.view("datetime64[ns]"),
            session=session,
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
        timestamps: np.ndarray | None = None,
        session: np.ndarray | None = None,
        candles: list[Candle] | None = None,
    ) -> Bars:
        """Construct from pre-extracted arrays.

        ``timestamps`` and ``session`` may be omitted by callers that
        only have OHLCV (e.g. the fetcher's prebuilt-arrays stash); they
        will be derived from ``candles`` if provided, else filled with
        sentinel values that still satisfy the dtype contract.
        """
        n = int(close.size)
        if timestamps is None:
            if candles is not None and len(candles) == n:
                ts = np.empty(n, dtype=np.int64)
                for i, c in enumerate(candles):
                    ts[i] = _epoch_ns(c.date)
                timestamps = ts.view("datetime64[ns]")
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
