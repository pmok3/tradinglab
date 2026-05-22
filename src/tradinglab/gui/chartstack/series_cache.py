"""Per-card series cache — bounded deque of recent bars.

Each card holds at most ``maxlen`` bars (default 60, matches §2.1
of the synthesis: 60-bar sparkline window). Live ticks update the
trailing bar in-place; finalized bar rollovers append a new entry
and evict the oldest when over capacity.

The cache is intentionally dumb: no fetch logic, no streaming
hooks, no Tk. M2 wires :class:`CardController` to push ticks here;
M1 just needs the data structure with a clean API and the test
coverage that pins down the upsert / rollover / eviction
semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Bar:
    """OHLCV bar with a timestamp, in cache-internal shape.

    Mirrors the public ``Candle`` structure but keeps a separate
    type so the cache is decoupled from the larger ``models`` module
    (and trivially constructible in tests). ``session`` carries the
    pre-/regular-/post-market classification so card overlays
    (pre-market H/L, RTH-anchored VWAP) can tell which bars to
    consider without re-running session classification at draw time.
    """

    ts: Any
    open: float
    high: float
    low: float
    close: float
    volume: float
    session: str = "regular"


class CardSeriesCache:
    """Bounded ring buffer of :class:`Bar` instances for one card."""

    def __init__(self, maxlen: int = 60) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen must be positive")
        self._maxlen = int(maxlen)
        self._bars: list[Bar] = []

    @property
    def maxlen(self) -> int:
        """Configured cap on number of bars retained."""
        return self._maxlen

    def __len__(self) -> int:
        return len(self._bars)

    def upsert_tick(self, ts: Any, ohlcv: tuple, *, session: str = "regular") -> None:
        """Update the trailing bar in-place, or append a new one.

        ``ohlcv`` is ``(open, high, low, close, volume)``. If the
        last bar's ``ts`` matches, that bar is mutated; otherwise a
        new bar is appended (and the oldest evicted if we'd overflow
        ``maxlen``). ``session`` is preserved on in-place updates and
        used for newly appended bars.
        """
        if len(ohlcv) != 5:
            raise ValueError("ohlcv must be a 5-tuple of (o, h, l, c, v)")
        o, h, l, c, v = ohlcv
        if self._bars and self._bars[-1].ts == ts:
            last = self._bars[-1]
            last.open = float(o)
            last.high = float(h)
            last.low = float(l)
            last.close = float(c)
            last.volume = float(v)
            return
        self._bars.append(
            Bar(ts=ts, open=float(o), high=float(h), low=float(l),
                close=float(c), volume=float(v), session=str(session))
        )
        if len(self._bars) > self._maxlen:
            del self._bars[0 : len(self._bars) - self._maxlen]

    def append_rollover(self, bar: Bar) -> None:
        """Append a finalized bar, evicting the oldest if at capacity."""
        if not isinstance(bar, Bar):
            raise TypeError("append_rollover requires a Bar instance")
        self._bars.append(bar)
        if len(self._bars) > self._maxlen:
            del self._bars[0 : len(self._bars) - self._maxlen]

    def snapshot(self) -> list[Bar]:
        """Return a shallow copy of the bar list (safe to iterate)."""
        return list(self._bars)

    def invalidate(self) -> None:
        """Drop all cached bars (e.g. on binding change)."""
        self._bars.clear()

    def latest(self) -> Bar | None:
        """Return the trailing bar or ``None`` when empty."""
        return self._bars[-1] if self._bars else None


__all__ = ["Bar", "CardSeriesCache"]
