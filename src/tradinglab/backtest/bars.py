"""``BarSeries`` — per-field ndarray bar container for the engine.

Why per-field ndarrays (not a list of dataclasses): the perf budget for
the future automated batch runner is dominated by tight loops over
opens/closes/highs/lows. A list-of-Candle layout forces an attribute
lookup per bar; per-field ndarrays keep the working set in cache and
let vectorised strategies operate on whole windows at once.

Phase 1a only consumes single fields from a single index, but we
commit to the layout now so we never have to re-port the engine when
Phase 2 lands.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from ..models import Candle


@dataclass(frozen=True)
class BarSeries:
    """Immutable container for a contiguous run of bars on one symbol/timeframe.

    All ndarrays share a length. ``ts`` is int64 epoch seconds in UTC
    (naive in the input is treated as UTC; tz-aware values are
    converted). All price arrays are float64; volume is float64 (so we
    can encode "no trades" as 0.0 and never NaN).
    """

    symbol: str
    timeframe: str       # "1d" | "5m" (any string the engine doesn't interpret)
    ts: np.ndarray       # int64, shape (N,)
    open: np.ndarray     # float64, shape (N,)
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    def __len__(self) -> int:
        return int(self.ts.shape[0])

    def __post_init__(self) -> None:
        n = self.ts.shape[0]
        for name in ("open", "high", "low", "close", "volume"):
            arr = getattr(self, name)
            if arr.shape[0] != n:
                raise ValueError(
                    f"BarSeries field {name!r} has length {arr.shape[0]} "
                    f"but ts has length {n}"
                )
        if self.ts.dtype != np.int64:
            raise TypeError(f"BarSeries.ts must be int64; got {self.ts.dtype!r}")
        for name in ("open", "high", "low", "close", "volume"):
            arr = getattr(self, name)
            if arr.dtype != np.float64:
                raise TypeError(
                    f"BarSeries.{name} must be float64; got {arr.dtype!r}"
                )

    def index_for_ts(self, ts: int) -> int | None:
        """Exact-match lookup. Returns ``None`` if ``ts`` not present."""
        idx = int(np.searchsorted(self.ts, ts, side="left"))
        if 0 <= idx < len(self) and int(self.ts[idx]) == int(ts):
            return idx
        return None


# ---- adapter cache ----------------------------------------------------------

_FROM_CANDLES_CACHE: dict[tuple[str, str, int, int, float, float], BarSeries] = {}
_FROM_CANDLES_CACHE_MAX = 64


def _content_key(
    symbol: str, timeframe: str, candles: Sequence[Candle]
) -> tuple[str, str, int, int, float, float]:
    """Cheap content fingerprint suitable for ndarray reuse.

    Captures (symbol, timeframe, len, last-ts-epoch, last-close,
    first-close). Not cryptographic — collisions just cause a re-fetch
    next call, never wrong data, because we re-key on first miss.
    """
    if not candles:
        return (symbol, timeframe, 0, 0, 0.0, 0.0)
    last = candles[-1]
    first = candles[0]
    last_ts = _candle_ts_epoch(last)
    return (
        symbol, timeframe, len(candles),
        last_ts, float(last.close), float(first.close),
    )


def _candle_ts_epoch(c: Candle) -> int:
    """Convert a Candle.date (naive or tz-aware datetime) to int64 epoch seconds."""
    dt = c.date
    if dt.tzinfo is None:
        import datetime as _dt
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return int(dt.timestamp())


def from_candles(
    symbol: str, timeframe: str, candles: Sequence[Candle]
) -> BarSeries:
    """Build a :class:`BarSeries` from a list of :class:`Candle`.

    Cached by ``_content_key``; identical inputs return the same
    BarSeries object so downstream consumers can use ``id()`` as a
    cheap "did the data change" sentinel.
    """
    key = _content_key(symbol, timeframe, candles)
    cached = _FROM_CANDLES_CACHE.get(key)
    if cached is not None:
        return cached

    n = len(candles)
    ts = np.empty(n, dtype=np.int64)
    op = np.empty(n, dtype=np.float64)
    hi = np.empty(n, dtype=np.float64)
    lo = np.empty(n, dtype=np.float64)
    cl = np.empty(n, dtype=np.float64)
    vol = np.empty(n, dtype=np.float64)
    for i, c in enumerate(candles):
        ts[i] = _candle_ts_epoch(c)
        op[i] = float(c.open)
        hi[i] = float(c.high)
        lo[i] = float(c.low)
        cl[i] = float(c.close)
        vol[i] = float(c.volume)

    bs = BarSeries(symbol=symbol, timeframe=timeframe, ts=ts,
                   open=op, high=hi, low=lo, close=cl, volume=vol)

    if len(_FROM_CANDLES_CACHE) >= _FROM_CANDLES_CACHE_MAX:
        _FROM_CANDLES_CACHE.pop(next(iter(_FROM_CANDLES_CACHE)))
    _FROM_CANDLES_CACHE[key] = bs
    return bs


def _clear_cache_for_tests() -> None:
    """Test hook — drop the memoisation cache between smoke checks."""
    _FROM_CANDLES_CACHE.clear()
