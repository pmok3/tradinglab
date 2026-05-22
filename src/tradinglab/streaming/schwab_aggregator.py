"""Pure aggregator for Schwab streaming feeds.

Two Schwab streaming services, both rolled up to 1-minute Candles
that match the rest of the chart pipeline:

* **LEVELONE_EQUITIES** — sub-minute quote/trade ticks. We drive the
  in-progress 1-minute bar from these. Each incoming tick advances
  ``close``, expands ``high``/``low``, and accumulates ``volume``.
  When the wall clock crosses a minute boundary, we seal the bar and
  open a new one seeded at the previous close.

* **CHART_EQUITY** — 1-minute OHLCV bars from Schwab's tape, arriving
  ~5–30 seconds after the minute closes. These are the *source of
  truth* for closed minutes. The chart writes them straight into the
  BarsBuffer, silently overwriting whatever LEVELONE synthesized
  (per design: corrections aren't surfaced as a distinct event; the
  next paint picks them up).

This module is **pure**: no threads, no sockets, no time.time(). The
state machine is :class:`MinuteBarBuilder` and consumers feed it
parsed tick dicts + the current wall clock. Tests drive it directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple

from ..constants import classify_session, floor_to_interval
from ..models import Candle

# ---------------------------------------------------------------------------
# LEVELONE field decoding
# ---------------------------------------------------------------------------

# Schwab's streaming API uses numeric field IDs in the wire payload to
# save bytes. The spec at https://developer.schwab.com/streamer-api
# documents these explicitly. We only consume the few fields we need
# for bar synthesis. Unknown/extra fields are ignored.
#
# Field map for LEVELONE_EQUITIES content dicts:
LEVELONE_FIELDS = {
    "0":  "symbol",
    "1":  "bid_price",
    "2":  "ask_price",
    "3":  "last_price",
    "4":  "bid_size",
    "5":  "ask_size",
    "8":  "total_volume",       # cumulative day volume (not per-tick)
    "35": "trade_time_ms",      # epoch ms of last trade
}


def decode_levelone_content(content: Mapping[str, Any]) -> Dict[str, Any]:
    """Translate one LEVELONE_EQUITIES content dict to logical names.

    Schwab sends partial updates (only changed fields), so the result
    may be missing any key. Numeric fields are coerced to ``float`` /
    ``int`` only when present; absent keys stay absent.
    """
    out: Dict[str, Any] = {}
    for raw_key, val in content.items():
        name = LEVELONE_FIELDS.get(str(raw_key), None)
        if name is None:
            continue
        out[name] = val
    return out


# ---------------------------------------------------------------------------
# CHART_EQUITY field decoding
# ---------------------------------------------------------------------------

# CHART_EQUITY content dicts represent one finished 1-minute bar.
CHART_EQUITY_FIELDS = {
    "0": "symbol",
    "1": "sequence",
    "2": "open",
    "3": "high",
    "4": "low",
    "5": "close",
    "6": "volume",
    "7": "chart_time_ms",   # epoch ms of bar start
}


def decode_chart_equity_content(content: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for raw_key, val in content.items():
        name = CHART_EQUITY_FIELDS.get(str(raw_key), None)
        if name is None:
            continue
        out[name] = val
    return out


def chart_equity_to_candle(
    decoded: Mapping[str, Any], *, tz=timezone.utc,
) -> Optional[Candle]:
    """Map a decoded CHART_EQUITY bar into our :class:`Candle`.

    Returns ``None`` if any required OHLCV/timestamp field is missing
    — partial chart-equity messages are rare but defensible to skip.
    """
    required = ("open", "high", "low", "close", "volume", "chart_time_ms")
    if not all(k in decoded for k in required):
        return None
    ts = datetime.fromtimestamp(int(decoded["chart_time_ms"]) / 1000.0, tz=tz)
    return Candle(
        date=ts,
        open=float(decoded["open"]),
        high=float(decoded["high"]),
        low=float(decoded["low"]),
        close=float(decoded["close"]),
        volume=int(decoded["volume"]),
        session=classify_session(ts.hour, ts.minute),
    )


# ---------------------------------------------------------------------------
# MinuteBarBuilder — drives LEVELONE → in-progress 1-min bar
# ---------------------------------------------------------------------------


@dataclass
class _Bar:
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    # Volume baseline lets us turn LEVELONE's *cumulative day* volume
    # into per-bar volume by subtracting the cumulative value at bar
    # open. Set to None until the first cumulative we observe in the
    # bar, then frozen for the bar's lifetime.
    _cum_volume_at_open: Optional[int] = None

    def to_candle(self) -> Candle:
        return Candle(
            date=self.start,
            open=self.open, high=self.high, low=self.low, close=self.close,
            volume=int(self.volume),
            session=classify_session(self.start.hour, self.start.minute),
        )


@dataclass
class MinuteBarBuilder:
    """Stateful 1-minute aggregator for one symbol.

    Feeds: per-tick :meth:`apply_levelone` calls. Outputs: events
    matching the existing :class:`StreamSource` protocol —
    ``("tick", Candle)`` or ``("rollover", Candle)`` (or both, if a
    single LEVELONE message advances time across a boundary).

    Seeding: pass the last close from REST history into
    :meth:`open_initial_bar`. The first emitted event is a
    ``rollover`` carrying the in-progress bar at the current minute.
    """

    seed_close: float
    _bar: Optional[_Bar] = field(default=None, init=False)

    def open_initial_bar(self, now: datetime) -> Tuple[str, Candle]:
        """Open the very first in-progress bar at ``now`` floored to 1-min.

        Returns the ``("rollover", Candle)`` event the source should
        emit so the consumer adds the in-progress bar to its buffer.
        """
        start = floor_to_interval(now, 1)
        self._bar = _Bar(
            start=start,
            open=self.seed_close, high=self.seed_close,
            low=self.seed_close, close=self.seed_close,
        )
        return ("rollover", self._bar.to_candle())

    def apply_levelone(
        self, decoded: Mapping[str, Any], *, now: datetime,
    ) -> List[Tuple[str, Candle]]:
        """Apply one decoded LEVELONE update. Returns the events to emit.

        Multiple events can come back in one call if the wall clock
        already crossed a minute boundary since the last update.
        """
        events: List[Tuple[str, Candle]] = []
        if self._bar is None:
            events.append(self.open_initial_bar(now))

        # Roll past any boundaries we've crossed since the last call.
        # Each boundary seals the current bar and seeds a fresh one.
        events.extend(self._roll_to(now))

        bar = self._bar
        assert bar is not None  # narrowed by _roll_to

        # Pick the price that drives the close. Prefer last trade
        # price; fall back to mid of bid/ask if no trade has printed.
        last = decoded.get("last_price")
        if last is None:
            bid = decoded.get("bid_price")
            ask = decoded.get("ask_price")
            if bid is not None and ask is not None:
                last = (float(bid) + float(ask)) / 2.0
        if last is not None:
            px = float(last)
            bar.close = px
            if px > bar.high:
                bar.high = px
            if px < bar.low:
                bar.low = px

        # Volume: LEVELONE's "total_volume" is cumulative for the day.
        # Per-bar volume = current cumulative − snapshot taken at bar open.
        cum = decoded.get("total_volume")
        if cum is not None:
            cum_int = int(cum)
            if bar._cum_volume_at_open is None:
                bar._cum_volume_at_open = cum_int
                bar.volume = 0
            else:
                bar.volume = max(0, cum_int - bar._cum_volume_at_open)

        # Only emit a tick if anything actually changed. With no
        # last/bid/ask and no volume, a heartbeat-y update is silent.
        if last is not None or cum is not None:
            events.append(("tick", bar.to_candle()))
        return events

    def maybe_rollover(self, now: datetime) -> List[Tuple[str, Candle]]:
        """Force boundary check (no LEVELONE update). Used by the
        per-source clock thread so quiet symbols still roll over."""
        if self._bar is None:
            return [self.open_initial_bar(now)]
        return self._roll_to(now)

    def _roll_to(self, now: datetime) -> List[Tuple[str, Candle]]:
        events: List[Tuple[str, Candle]] = []
        bar = self._bar
        assert bar is not None
        target = floor_to_interval(now, 1)
        while target > bar.start:
            # Open the next minute, seeded by the previous close.
            new_start = bar.start + timedelta(minutes=1)
            self._bar = _Bar(
                start=new_start,
                open=bar.close, high=bar.close,
                low=bar.close, close=bar.close,
            )
            bar = self._bar
            events.append(("rollover", bar.to_candle()))
        return events
