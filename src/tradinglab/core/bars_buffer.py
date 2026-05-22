"""Append-only mutable buffer that emits :class:`Bars` snapshots.

Motivation
----------

Today the scanner runner rebuilds :class:`~tradinglab.core.bars.Bars`
from scratch every tick via :meth:`Bars.from_candles`. That extracts
seven NumPy arrays (OHLCV + timestamps + session) using ``np.fromiter``
on each call. With a 200-symbol watchlist on every replay step (or
every live tick), this is a non-trivial cost that grows with both
universe size and bar history.

``BarsBuffer`` shifts the cost from rebuild-every-tick to
amortised-per-bar:

* :meth:`append` writes one Candle into a growable column store
  (capacity-doubling, like ``std::vector``); amortised O(1).
* :meth:`update_last` overwrites the last row in place — the seam for a
  future "forming bar" streaming path.
* :meth:`view` produces a frozen :class:`Bars` whose arrays are
  *views* over the populated prefix. No copy.

Lifetime contract
-----------------

The arrays exposed by :meth:`view` alias the buffer's own storage. A
subsequent :meth:`append` may trigger a capacity doubling that
*re-allocates* the underlying arrays — at which point any previously
obtained :class:`Bars` is left holding views into the old, no-longer-
shared storage (still valid, but no longer reflects the buffer).
:meth:`update_last` mutates a slot that is *visible* through any
outstanding view.

Therefore the rule for callers is:

    Use the returned ``Bars`` within the current tick; discard it
    before the next ``append`` / ``update_last``. Do not stash it in a
    long-lived cache.

Concurrency
-----------

The buffer is single-writer / multi-reader within one tick: the
scanner runner mutates it on the main thread before submitting work,
then worker threads only *read* through their captured ``Bars`` view.
The buffer itself takes no locks.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import numpy as np

from ..models import Candle
from .bars import Bars, _to_naive_utc

_INITIAL_CAPACITY = 16


class BarsBuffer:
    """Growable column store that emits :class:`Bars` snapshots.

    Use :meth:`from_candles` for an initial bulk-populated buffer or
    construct empty and call :meth:`append` per Candle. Either way,
    call :meth:`view` to get a frozen :class:`Bars` view into the
    populated prefix.
    """

    __slots__ = (
        "_n",
        "_capacity",
        "_open",
        "_high",
        "_low",
        "_close",
        "_volume",
        "_timestamps",
        "_session",
    )

    def __init__(self, initial_capacity: int = _INITIAL_CAPACITY) -> None:
        cap = max(1, int(initial_capacity))
        self._n: int = 0
        self._capacity: int = cap
        self._open       = np.empty(cap, dtype=np.float64)
        self._high       = np.empty(cap, dtype=np.float64)
        self._low        = np.empty(cap, dtype=np.float64)
        self._close      = np.empty(cap, dtype=np.float64)
        self._volume     = np.empty(cap, dtype=np.float64)
        self._timestamps = np.empty(cap, dtype="datetime64[ns]")
        self._session    = np.empty(cap, dtype=object)

    # ---------------------------------------------------------------- ctors

    @classmethod
    def from_candles(cls, candles: Sequence[Candle]) -> BarsBuffer:
        """Pre-populated buffer matching ``Bars.from_candles`` semantics."""
        n = len(candles)
        # Pick a capacity that comfortably holds n; round up to a power of 2
        # ≥ ``_INITIAL_CAPACITY`` so subsequent appends amortise normally.
        cap = max(_INITIAL_CAPACITY, _next_pow2(n))
        buf = cls(initial_capacity=cap)
        if n:
            buf.extend(candles)
        return buf

    # ------------------------------------------------------------- mutation

    def _ensure_capacity(self, want: int) -> None:
        if want <= self._capacity:
            return
        new_cap = self._capacity
        while new_cap < want:
            new_cap *= 2
        self._open       = _grow(self._open,       new_cap, np.float64)
        self._high       = _grow(self._high,       new_cap, np.float64)
        self._low        = _grow(self._low,        new_cap, np.float64)
        self._close      = _grow(self._close,      new_cap, np.float64)
        self._volume     = _grow(self._volume,     new_cap, np.float64)
        self._timestamps = _grow(self._timestamps, new_cap, "datetime64[ns]")
        self._session    = _grow(self._session,    new_cap, object)
        self._capacity = new_cap

    def append(self, candle: Candle) -> None:
        """Push one ``Candle`` onto the end of the buffer."""
        self._ensure_capacity(self._n + 1)
        i = self._n
        self._write_at(i, candle)
        self._n += 1

    def update_last(self, candle: Candle) -> None:
        """Overwrite the last row in place. Raises if the buffer is empty.

        Designed for the streaming "forming bar" path: the same bar
        index receives successive updates as new ticks arrive within
        the bar's wall-clock window. Mutates storage that may be
        visible through outstanding :meth:`view` results — callers
        must invalidate any cached views before calling.
        """
        if self._n == 0:
            raise IndexError("update_last on empty BarsBuffer")
        self._write_at(self._n - 1, candle)

    def extend(self, candles: Iterable[Candle]) -> None:
        """Bulk append. Pre-grows when the input has a known length."""
        try:
            extra = len(candles)  # type: ignore[arg-type]
        except TypeError:
            extra = 0
        if extra:
            self._ensure_capacity(self._n + extra)
        for c in candles:
            self.append(c)

    def clear(self) -> None:
        """Reset length to 0; preserves the current allocated capacity."""
        self._n = 0

    def _write_at(self, i: int, c: Candle) -> None:
        self._open[i]       = c.open
        self._high[i]       = c.high
        self._low[i]        = c.low
        self._close[i]      = c.close
        self._volume[i]     = c.volume
        self._timestamps[i] = np.datetime64(_to_naive_utc(c.date), "ns")
        self._session[i]    = c.session

    # --------------------------------------------------------- introspection

    def __len__(self) -> int:
        return self._n

    @property
    def capacity(self) -> int:
        return self._capacity

    # ----------------------------------------------------------------- view

    def view(self, candles: Sequence[Candle] | None = None) -> Bars:
        """Return a frozen ``Bars`` over the populated prefix.

        ``candles`` is the matching source list to attach as a back-
        reference (so :func:`compute_via_bars` can fall back to
        ``indicator.compute(bars.candles)`` for indicators without a
        ``compute_arr`` fast path). When provided, must satisfy
        ``len(candles) == len(self)`` — otherwise :class:`ValueError`.
        Pass ``None`` only when callers are sure every consumer
        supports the array-only fast path.
        """
        n = self._n
        if candles is not None and len(candles) != n:
            raise ValueError(
                f"BarsBuffer.view: candles length {len(candles)} does not match "
                f"buffer length {n}"
            )
        # Slice up-to-n. NumPy slicing returns views (no copy).
        return Bars.from_arrays(
            open=self._open[:n],
            high=self._high[:n],
            low=self._low[:n],
            close=self._close[:n],
            volume=self._volume[:n],
            timestamps=self._timestamps[:n],
            session=self._session[:n],
            candles=list(candles) if (candles is not None and not isinstance(candles, list)) else candles,
        )


# --------------------------------------------------------------------- helpers


def _next_pow2(n: int) -> int:
    """Smallest power of 2 ≥ ``n``; floor of 1."""
    if n <= 1:
        return 1
    p = 1
    while p < n:
        p <<= 1
    return p


def _grow(arr: np.ndarray, new_cap: int, dtype) -> np.ndarray:
    """Return a new array of ``new_cap`` elements with the old data copied in."""
    out = np.empty(new_cap, dtype=dtype)
    out[: arr.size] = arr
    return out


__all__ = ["BarsBuffer"]
