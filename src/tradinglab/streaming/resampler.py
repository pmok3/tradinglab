"""Cross-interval bar plumbing: aggregate 1m candles into Nm/Nh buckets.

Layer −1 of the exit-strategies design (see ``exits_v1_plan.md``). The
streaming source only delivers 1-minute bars (Schwab CHART_EQUITY plus
the LEVELONE-driven :class:`MinuteBarBuilder`). Higher intraday
intervals — 2m / 3m / 5m / 10m / 15m / 30m / 1h / 2h / 4h — are
materialised here on the fly so that scanner conditions, exit
triggers, and chart overlays referencing those intervals can read a
live :class:`BarsBuffer` instead of round-tripping to the historical
adapter on every tick.

Daily / weekly / monthly are deliberately out of scope: those bars
come from the historical adapter (yfinance / Schwab REST) and are
authoritative there. The resampler only fills the *intraday* gap.

Design notes
------------

* **Pure & stateful per (symbol, interval).** No threads, no I/O.
  Callers wire one :class:`BarResampler` per ``(symbol, interval)`` —
  see :class:`tradinglab.data.multi_interval_cache.MultiIntervalCache`.
* **Session-aware bucket alignment.** A 5-minute bar's boundary
  aligns to ``09:30``, ``09:35``, ``09:40`` ET — *not* to wall-clock
  ``09:00``, ``09:05``. Pre-market and post-market buckets simply
  walk the same anchor backwards / forwards (so a 5m bar at 09:25
  belongs to the bucket that opens at 09:25).
* **Mutable 1m candles.** The streaming pipeline reuses one
  :class:`Candle` instance for successive ``forming=True`` updates of
  the same minute. We therefore re-read the 1m candle's fields on
  every call rather than caching the reference's snapshotted values.
  Locked (i.e. closed-1m) contributions are eagerly copied out into
  scalar state.
* **Forming + closed events.** Every ``on_1m_tick`` returns at least
  one event. A bucket rollover yields ``[closed(prior),
  forming(new)]`` so that a downstream :class:`BarsBuffer` can
  ``append`` the sealed bar then either ``append`` or ``update_last``
  the new in-progress bar.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from ..models import Candle

# Supported target intervals → minutes. Matches the plan's Layer −1
# scope; daily+ deliberately excluded.
_SUPPORTED_INTERVALS: dict[str, int] = {
    "2m": 2,
    "3m": 3,
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
}


def supported_intervals() -> Tuple[str, ...]:
    """Return the tuple of target intervals this module can resample to."""
    return tuple(_SUPPORTED_INTERVALS.keys())


@dataclass(frozen=True)
class BarEvent:
    """One event emitted by :meth:`BarResampler.on_1m_tick`.

    Attributes
    ----------
    closed:
        ``True`` when the higher-interval bar just rolled over and is
        now finalised — the consumer should ``append`` it. ``False``
        for an in-progress (forming) update of the current bucket —
        the consumer should ``update_last`` (or ``append`` if no
        forming row exists yet for the current bucket).
    candle:
        Freshly constructed :class:`Candle` whose ``date`` is the
        bucket-start timestamp (not the source 1m's timestamp).
    source_minute_count:
        How many distinct 1-minute bars contributed to this event,
        including the in-progress 1m for forming events. Useful for
        downstream "is this bucket fully populated yet?" checks.
    """

    closed: bool
    candle: Candle
    source_minute_count: int


class BarResampler:
    """Aggregate 1-minute :class:`Candle` ticks into a target interval.

    Construct with the target interval (e.g. ``"5m"``) plus an
    optional ``session_open_time`` anchor (default US equities open at
    ``(9, 30)``). Feed every 1m tick — both forming and closed — into
    :meth:`on_1m_tick`; the returned :class:`BarEvent` list is the
    minimal set of bucket-level updates the caller needs to apply.

    The resampler holds state for exactly one in-progress higher-
    interval bucket. On bucket rollover it emits the prior bucket as
    a ``closed=True`` event and starts a new bucket seeded by the
    1m candle that triggered the rollover.

    Threading
    ---------
    Single-writer. The caller serialises ``on_1m_tick`` calls (in
    practice the streaming queue drain on the GUI / app thread).
    """

    __slots__ = (
        "_target_interval",
        "_target_min",
        "_open_h",
        "_open_m",
        # Locked aggregate (from already-closed 1m bars in this bucket)
        "_bucket_start",
        "_locked_count",
        "_locked_open",
        "_locked_high",
        "_locked_low",
        "_locked_close",
        "_locked_volume",
        "_locked_session_counts",
        "_locked_last_session",
        # In-progress 1m within this bucket (reference, may mutate)
        "_pending_1m",
    )

    def __init__(
        self,
        target_interval: str,
        *,
        session_open_time: Tuple[int, int] = (9, 30),
    ) -> None:
        if target_interval not in _SUPPORTED_INTERVALS:
            raise ValueError(
                f"Unsupported resampler target interval: {target_interval!r}. "
                f"Supported: {sorted(_SUPPORTED_INTERVALS)}"
            )
        self._target_interval: str = target_interval
        self._target_min: int = _SUPPORTED_INTERVALS[target_interval]
        h, m = session_open_time
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(
                f"session_open_time out of range: {session_open_time!r}"
            )
        self._open_h: int = int(h)
        self._open_m: int = int(m)
        self._reset_state()

    # ------------------------------------------------------------------ public

    @property
    def target_interval(self) -> str:
        return self._target_interval

    @property
    def target_minutes(self) -> int:
        return self._target_min

    def reset(self) -> None:
        """Drop all in-progress bucket state. Next tick seeds fresh."""
        self._reset_state()

    def current_forming(self) -> Optional[Candle]:
        """Return the current in-progress higher-interval bar, if any.

        Returns ``None`` before the first tick (or after :meth:`reset`).
        Otherwise returns a freshly built :class:`Candle` snapshotting
        the bucket's state right now (locked + any in-progress 1m).
        """
        if self._bucket_start is None:
            return None
        candle, _ = self._build_effective()
        return candle

    def on_1m_tick(
        self, candle: Candle, *, forming: bool
    ) -> List[BarEvent]:
        """Feed one 1m candle into the resampler. Returns events to emit.

        ``forming=True`` means the same 1m timestamp may receive
        further updates with mutated OHLCV; ``forming=False`` means
        this is the final, locked value for that minute.

        Typical return shapes:

        * ``[BarEvent(closed=False, ...)]`` — the normal case, an
          update to the current bucket's forming bar.
        * ``[BarEvent(closed=True, ...), BarEvent(closed=False, ...)]``
          — bucket rollover. The first event finalises the prior
          bucket; the second is the brand-new bucket seeded by
          ``candle``.
        """
        bucket_start = self._bucket_start_for(candle.date)
        events: List[BarEvent] = []

        # First tick ever — seed the bucket.
        if self._bucket_start is None:
            self._seed_bucket(bucket_start)
            self._absorb(candle, forming=forming)
            events.append(self._make_forming_event())
            return events

        # Out-of-order / older tick: ignore. Real streams shouldn't do
        # this and a misordered tick would corrupt aggregates.
        if bucket_start < self._bucket_start:
            return events

        # New bucket — finalise the prior one, then seed and absorb.
        if bucket_start > self._bucket_start:
            events.append(self._make_closed_event())
            self._seed_bucket(bucket_start)
            self._absorb(candle, forming=forming)
            events.append(self._make_forming_event())
            return events

        # Same bucket — just absorb and emit a forming update.
        self._absorb(candle, forming=forming)
        events.append(self._make_forming_event())
        return events

    # --------------------------------------------------------------- internals

    def _reset_state(self) -> None:
        self._bucket_start: Optional[datetime] = None
        self._locked_count: int = 0
        self._locked_open: float = 0.0
        self._locked_high: float = 0.0
        self._locked_low: float = 0.0
        self._locked_close: float = 0.0
        self._locked_volume: float = 0.0
        self._locked_session_counts: Counter = Counter()
        self._locked_last_session: str = "regular"
        self._pending_1m: Optional[Candle] = None

    def _seed_bucket(self, bucket_start: datetime) -> None:
        self._bucket_start = bucket_start
        self._locked_count = 0
        self._locked_open = 0.0
        self._locked_high = 0.0
        self._locked_low = 0.0
        self._locked_close = 0.0
        self._locked_volume = 0.0
        self._locked_session_counts = Counter()
        self._locked_last_session = "regular"
        self._pending_1m = None

    def _bucket_start_for(self, t: datetime) -> datetime:
        """Floor ``t`` to the most recent target-interval boundary anchored
        at ``session_open_time`` of the same wall-clock date.

        Pre-market timestamps yield buckets walking backwards from the
        anchor; post-market timestamps walk forwards. Bucket arithmetic
        uses Python's floor division so negative deltas land cleanly
        on a boundary (e.g. 5m bucket for 09:25 with anchor 09:30 →
        09:25; for 09:23 → 09:20).
        """
        anchor = t.replace(
            hour=self._open_h, minute=self._open_m,
            second=0, microsecond=0,
        )
        # 1m candles are always whole-minute aligned upstream. Round
        # to the nearest minute defensively in case a sub-minute
        # timestamp slips through (rounding rather than truncating
        # avoids a 59-second off-by-one near boundaries).
        delta_min = round((t - anchor).total_seconds() / 60.0)
        bucket_offset = (delta_min // self._target_min) * self._target_min
        return anchor + timedelta(minutes=int(bucket_offset))

    def _absorb(self, candle: Candle, *, forming: bool) -> None:
        """Fold ``candle`` into the current bucket's running aggregate."""
        if forming:
            # If a different 1m was pending, that earlier minute never
            # produced a forming=False event — treat it as locked at
            # its last seen state before swapping in the new pending.
            if (
                self._pending_1m is not None
                and self._pending_1m.date != candle.date
            ):
                self._lock(self._pending_1m)
            self._pending_1m = candle
            return

        # forming=False — this 1m is locked. If it matches the pending
        # candle's timestamp, the pending was the same minute; clear
        # it. Either way, fold the locked values into _locked_*.
        if (
            self._pending_1m is not None
            and self._pending_1m.date == candle.date
        ):
            self._pending_1m = None
        elif self._pending_1m is not None:
            # A different earlier minute is still pending — lock it
            # before the newly closed one.
            self._lock(self._pending_1m)
            self._pending_1m = None
        self._lock(candle)

    def _lock(self, c: Candle) -> None:
        """Copy ``c``'s scalar fields into the locked aggregate."""
        if self._locked_count == 0:
            self._locked_open = float(c.open)
            self._locked_high = float(c.high)
            self._locked_low = float(c.low)
        else:
            if c.high > self._locked_high:
                self._locked_high = float(c.high)
            if c.low < self._locked_low:
                self._locked_low = float(c.low)
        self._locked_close = float(c.close)
        self._locked_volume += float(c.volume)
        self._locked_session_counts[c.session] += 1
        self._locked_last_session = c.session
        self._locked_count += 1

    def _build_effective(self) -> Tuple[Candle, int]:
        """Produce the bucket's current Candle + minute count.

        Combines the locked aggregate with the in-progress 1m (if
        any). Re-reads ``_pending_1m``'s fields on every call so
        intra-minute mutation flows through.
        """
        assert self._bucket_start is not None
        count = self._locked_count
        if count == 0 and self._pending_1m is None:
            # Defensive: shouldn't happen since seed+absorb always
            # leaves at least one contribution.
            zero = self._bucket_start
            return (
                Candle(
                    date=zero, open=0.0, high=0.0, low=0.0, close=0.0,
                    volume=0, session=self._locked_last_session,
                ),
                0,
            )

        o = self._locked_open
        h = self._locked_high
        low = self._locked_low
        cl = self._locked_close
        v = self._locked_volume
        # Session: most-common across merged bars; tiebreak by last.
        session_counts = Counter(self._locked_session_counts)
        last_session = self._locked_last_session

        if self._pending_1m is not None:
            p = self._pending_1m
            if count == 0:
                o = float(p.open)
                h = float(p.high)
                low = float(p.low)
            else:
                if p.high > h:
                    h = float(p.high)
                if p.low < low:
                    low = float(p.low)
            cl = float(p.close)
            v += float(p.volume)
            session_counts[p.session] += 1
            last_session = p.session
            count += 1

        # Resolve session: most-common, with last-merged-bar as
        # tiebreaker when several sessions are tied.
        if session_counts:
            max_n = max(session_counts.values())
            tied = {s for s, n in session_counts.items() if n == max_n}
            if last_session in tied:
                session = last_session
            else:
                session = next(iter(tied))
        else:
            session = last_session or "regular"

        return (
            Candle(
                date=self._bucket_start,
                open=o,
                high=h,
                low=low,
                close=cl,
                volume=int(v),
                session=session,
            ),
            count,
        )

    def _make_forming_event(self) -> BarEvent:
        candle, count = self._build_effective()
        return BarEvent(closed=False, candle=candle, source_minute_count=count)

    def _make_closed_event(self) -> BarEvent:
        # On rollover, any still-pending 1m has effectively closed
        # (the next bucket has begun). Lock it before sealing.
        if self._pending_1m is not None:
            self._lock(self._pending_1m)
            self._pending_1m = None
        candle, count = self._build_effective()
        return BarEvent(closed=True, candle=candle, source_minute_count=count)


__all__ = ["BarEvent", "BarResampler", "supported_intervals"]
