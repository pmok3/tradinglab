"""Schwab snapshot decoding and provisional, trade-time-driven minute bars.

LEVELONE is a change-only quote snapshot, NOT a time-and-sales feed. Its
observed trades can update a forming bar but cannot recover missed highs,
lows or trades. CHART_EQUITY remains authoritative by timestamp. No receive
clock, prior-close seed, midpoint or synthetic gap bar enters OHLC.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..constants import classify_session, floor_to_interval
from ..core.session_calendar import POST_CLOSE_MIN, PRE_OPEN_MIN
from ..core.timezones import ET
from ..models import Candle

LOG = logging.getLogger(__name__)

# Current Schwab IDs, not legacy TDA QUOTE IDs (whose previous close was 15).
LEVELONE_FIELDS = {
    "0": "symbol", "1": "bid_price", "2": "ask_price", "3": "last_price",
    "4": "bid_size", "5": "ask_size", "8": "total_volume",
    "10": "high_price", "11": "low_price", "12": "close_price",
    "35": "trade_time_ms",
}

# Verified against schwab-py's ChartEquityFields and realistic key/seq
# envelopes: https://schwab-py.readthedocs.io/en/latest/streaming.html
CHART_EQUITY_FIELDS = {
    "0": "symbol", "1": "sequence", "2": "open", "3": "high",
    "4": "low", "5": "close", "6": "volume", "7": "chart_time_ms",
    "8": "chart_day",
}


def _decode(content: Mapping[str, Any], fields: dict[str, str]) -> dict[str, Any]:
    out = {fields[str(k)]: v for k, v in content.items() if str(k) in fields}
    if "key" in content:
        out["symbol"] = content["key"]
    if "symbol" in out:
        out["symbol"] = str(out["symbol"]).strip().upper()
    return out


def decode_levelone_content(content: Mapping[str, Any]) -> dict[str, Any]:
    """Decode a partial snapshot; absent fields stay absent."""
    return _decode(content, LEVELONE_FIELDS)


def decode_chart_equity_content(content: Mapping[str, Any]) -> dict[str, Any]:
    return _decode(content, CHART_EQUITY_FIELDS)


def _number(value: Any, *, positive: bool = False) -> float:
    n = float(value)
    if not math.isfinite(n) or n < 0 or (positive and n == 0):
        raise ValueError("invalid market number")
    return n


def _trade_time(value: Any) -> datetime:
    if ET is None:
        raise ValueError("Eastern timezone unavailable")
    return datetime.fromtimestamp(_number(value, positive=True) / 1000, ET)


def chart_equity_to_candle(decoded: Mapping[str, Any]) -> Candle | None:
    """Validate an authoritative bar, preserving its exchange-local timestamp."""
    required = ("open", "high", "low", "close", "volume", "chart_time_ms")
    if not all(k in decoded for k in required):
        LOG.warning("schwab-stream: incomplete chart bar")
        return None
    try:
        ts = _trade_time(decoded["chart_time_ms"])
        o, h, low, c = (_number(decoded[k], positive=True) for k in required[:4])
        v = int(_number(decoded["volume"]))
        if low > min(o, c) or h < max(o, c) or low > h:
            raise ValueError("invalid OHLC envelope")
    except (ValueError, TypeError, OverflowError, OSError):
        LOG.warning("schwab-stream: invalid chart bar")
        return None
    return Candle(date=floor_to_interval(ts, 1), open=o, high=h, low=low, close=c,
                  volume=v, session=classify_session(ts.hour, ts.minute))


@dataclass
class _Bar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    def to_candle(self) -> Candle:
        return Candle(
            date=self.start, open=self.open, high=self.high, low=self.low,
            close=self.close, volume=self.volume,
            session=classify_session(self.start.hour, self.start.minute),
        )


@dataclass
class MinuteBarBuilder:
    """Provisional bars from observed trades only; owned by the socket worker.

    First cumulative volume is a baseline, not volume traded this minute.
    Adjacent-minute deltas are attributed to the newer observed snapshot;
    gaps/day changes rebaseline instead of assigning unknown volume to it.
    """

    _bar: _Bar | None = field(default=None, init=False)
    _snapshot_at: datetime | None = field(default=None, init=False)
    _trade_at: datetime | None = field(default=None, init=False)
    _price: float | None = field(default=None, init=False)
    _cumulative: int | None = field(default=None, init=False)
    _initial_cumulative: int | None = field(default=None, init=False)

    def apply_levelone(
        self, decoded: Mapping[str, Any], *, now: datetime | None = None,
    ) -> list[tuple[str, Candle]]:
        """Merge trade fields; ``now`` is compatibility-only, never a bucket clock."""
        if not {"last_price", "trade_time_ms", "total_volume"}.intersection(decoded):
            return []
        try:
            at = _trade_time(decoded["trade_time_ms"]) if "trade_time_ms" in decoded else self._snapshot_at
            price = _number(decoded["last_price"], positive=True) if "last_price" in decoded else self._price
            cumulative = (int(_number(decoded["total_volume"])) if "total_volume" in decoded
                          else self._initial_cumulative)
        except (ValueError, TypeError, OverflowError, OSError):
            LOG.warning("schwab-stream: invalid trade snapshot")
            return []
        # Retain valid wire deltas even when their timestamp cannot update
        # a bar. Omitted fields in later snapshots will not be retransmitted.
        self._snapshot_at, self._price = at, price
        if at is not None and self._trade_at is not None and at < self._trade_at:
            return []
        if at is None:
            # A separate timestamp delta can complete this initial image.
            self._initial_cumulative = cumulative
            return []
        if at.weekday() >= 5 or not PRE_OPEN_MIN <= at.hour * 60 + at.minute < POST_CLOSE_MIN:
            return []
        if price is None:
            self._trade_at = at
            self._initial_cumulative = cumulative
            return []

        start = floor_to_interval(at, 1)
        old = self._bar
        rollover = old is None or start > old.start
        reset = old is None or start.date() != old.start.date() or start - old.start > timedelta(minutes=1)
        delta = 0
        if reset:
            self._cumulative = None
        if cumulative is not None:
            if self._cumulative is not None:
                delta = max(0, cumulative - self._cumulative)
            # A backwards correction must not lower the baseline and count
            # the same volume twice when the cumulative value catches up.
            self._cumulative = max(cumulative, self._cumulative or 0)
        self._trade_at = at
        self._initial_cumulative = None
        if rollover:
            self._bar = _Bar(start, price, price, price, price, delta)
        else:
            assert old is not None
            old.close = price
            old.high, old.low = max(old.high, price), min(old.low, price)
            old.volume += delta
        assert self._bar is not None
        return [("rollover" if rollover else "tick", self._bar.to_candle())]

    def maybe_rollover(self, now: datetime) -> list[tuple[str, Candle]]:
        """A quiet clock is not evidence of a trade, on any session or holiday."""
        return []
