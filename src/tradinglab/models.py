"""Candle data model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Candle:
    """One OHLCV bar (timestamp + open/high/low/close + volume)."""

    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    # Session the bar belongs to: "pre", "regular", "post", or "gap".
    # "gap" is a placeholder used in compare mode when one ticker has no
    # data for a timestamp that the other ticker does — see
    # ``ChartApp._align_pair``. Rendering, hover, tables, and autoscale
    # all short-circuit on gap candles.
    session: str = "regular"

    @property
    def is_bull(self) -> bool:
        """True if the bar closed at or above its open (green)."""
        return self.close >= self.open

    @property
    def is_extended(self) -> bool:
        """True for bars outside regular trading hours (pre- or post-market).

        Explicitly excludes ``gap`` so the extended-hours coordination in
        ``_apply_pair_filter`` isn't fooled into treating placeholders as
        real pre/post bars.
        """
        return self.session in ("pre", "post")

    @property
    def is_gap(self) -> bool:
        """True for placeholder bars inserted during timestamp alignment."""
        return self.session == "gap"

    @classmethod
    def gap(cls, date: datetime) -> Candle:
        """Return a gap placeholder for ``date`` — NaN prices, zero volume."""
        nan = math.nan
        return cls(date=date, open=nan, high=nan, low=nan, close=nan,
                   volume=0, session="gap")
