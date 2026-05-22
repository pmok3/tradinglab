"""Vectorized numpy view of a candle series + lazy tooltip cache.

Pure-compute; no Tk/mpl. The tooltip ``format_date`` callable is opaque to
this module — it's invoked later by the caller (currently the GUI, but
could be a headless report formatter).
"""
from __future__ import annotations

import numpy as np

from ..data import pop_prebuilt_arrays
from ..formatting import fmt_volume
from ..models import Candle


class SeriesArrays:
    """Vectorized numpy view of a ``List[Candle]`` plus a lazy tooltip-text cache.

    Built lazily on first access; cached keyed on ``id(candles)``. The OHLCV
    numpy arrays are built up front because autoscale slices them on every
    pan. Tooltip strings, however, are built **on demand** — most candles
    are never hovered, so pre-formatting every string at startup wastes
    time on large histories.

    Two construction paths:

    * ``__init__(candles, format_date)`` — legacy path. Extracts arrays
      via ``np.fromiter`` over the candle list (5 passes total).
    * :meth:`from_arrays` — fast path used when the fetcher already
      produced numpy arrays during normalization. Skips the 5 extraction
      passes by reusing the fetcher's arrays directly. See
      ``data/normalize.py`` for the side-channel mechanism.
    """

    __slots__ = ("opens", "highs", "lows", "closes", "volumes",
                 "_candles", "_format_date", "_tooltip_cache", "n", "_bars")

    def __init__(self, candles: list[Candle], format_date) -> None:
        n = len(candles)
        self.n = n
        self.opens = np.fromiter((c.open for c in candles), dtype=float, count=n)
        self.highs = np.fromiter((c.high for c in candles), dtype=float, count=n)
        self.lows = np.fromiter((c.low for c in candles), dtype=float, count=n)
        self.closes = np.fromiter((c.close for c in candles), dtype=float, count=n)
        self.volumes = np.fromiter((c.volume for c in candles), dtype=float, count=n)
        self._candles = candles
        # Lazy: built only if a consumer asks for the timestamps/session
        # arrays via :attr:`bars`. ``app._series`` may also seed this
        # field via :meth:`from_bars` when the indicator cache already
        # built one (avoiding a second extraction).
        self._bars = None
        self._format_date = format_date
        self._tooltip_cache: dict[int, str] = {}

    @classmethod
    def from_arrays(cls, candles: list[Candle], format_date,
                    arrays) -> SeriesArrays:
        """Construct from pre-extracted numpy arrays (see ``data/normalize``)."""
        self = cls.__new__(cls)
        self.n = len(candles)
        self.opens = arrays.opens
        self.highs = arrays.highs
        self.lows = arrays.lows
        self.closes = arrays.closes
        self.volumes = arrays.volumes
        self._candles = candles
        # No Bars view has been built yet; lazy-build on first access.
        self._bars = None
        self._format_date = format_date
        self._tooltip_cache = {}
        return self

    @classmethod
    def from_bars(cls, bars, format_date) -> SeriesArrays:
        """Construct directly from an existing :class:`Bars` view.

        Zero-copy: the underlying numpy arrays are shared. Used by the
        chart app to keep the SeriesArrays and IndicatorCache views in
        sync (one extraction, two consumers).
        """
        if bars.candles is None:
            raise ValueError("Bars must carry its candles back-reference")
        self = cls.__new__(cls)
        self.n = bars.open.shape[0]
        self.opens = bars.open
        self.highs = bars.high
        self.lows = bars.low
        self.closes = bars.close
        self.volumes = bars.volume
        self._candles = bars.candles
        self._bars = bars
        self._format_date = format_date
        self._tooltip_cache = {}
        return self

    @property
    def bars(self):
        """Return the :class:`Bars` view of these candles (lazy build)."""
        if self._bars is None:
            from .bars import Bars
            self._bars = Bars.from_candles(self._candles)
        return self._bars

    def tooltip_text(self, idx: int) -> str:
        """Return the formatted OHLCV tooltip for candle ``idx``.

        Strings are built on first access and cached so subsequent hovers
        over the same candle are free dict lookups.
        """
        t = self._tooltip_cache.get(idx)
        if t is None:
            c = self._candles[idx]
            tag = ""
            if c.session == "pre":
                tag = "[PRE] "
            elif c.session == "post":
                tag = "[POST] "
            t = (f"{tag}{self._format_date(c)}\n"
                 f"O: {c.open:,.2f}\n"
                 f"H: {c.high:,.2f}\n"
                 f"L: {c.low:,.2f}\n"
                 f"C: {c.close:,.2f}\n"
                 f"Vol: {fmt_volume(c.volume)}")
            self._tooltip_cache[idx] = t
        return t


def build_series_safe(candles: list[Candle], format_date) -> SeriesArrays | None:
    """Build a ``SeriesArrays`` off the main thread, swallowing errors.

    Called from fetch / disk-load worker threads so the main thread's
    first ``_series()`` lookup hits the cache. Safe to run off-thread:
    ``SeriesArrays.__init__`` only populates numpy arrays and stashes
    the ``format_date`` callable (the callable itself may read GUI state
    so it is only invoked later on the main thread from ``tooltip_text``).

    Fast path: if the vectorized normalizer stashed pre-extracted numpy
    arrays for this candle list (see ``data/normalize.py``), pop them
    and construct via ``SeriesArrays.from_arrays`` — skips five
    ``np.fromiter`` passes over the candle list.
    """
    if not candles:
        return None
    try:
        prebuilt = pop_prebuilt_arrays(candles)
        if prebuilt is not None:
            return SeriesArrays.from_arrays(candles, format_date, prebuilt)
        return SeriesArrays(candles, format_date)
    except Exception:  # noqa: BLE001 - best effort; main thread falls back to lazy build.
        return None
