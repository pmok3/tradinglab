"""Synthetic (offline, deterministic) streaming source.

One daemon thread per subscription advances a log-normal random walk on
the in-progress bar's close, updating the high/low envelope. When the
wall clock crosses the next interval boundary, a rollover event is
emitted seeded at the previous close.

The seed is derived from the ticker so a given symbol always draws the
same series — handy for exercising compare mode with predictable data.
"""

from __future__ import annotations

import math
import random
import threading
from datetime import datetime
from typing import Callable

from ..constants import (
    classify_session,
    floor_to_interval,
    interval_minutes,
    is_intraday,
)
from ..models import Candle
from .base import StreamCallback


class SyntheticStreamSource:

    def __init__(self, tick_period: float = 0.5) -> None:
        self._tick_period = tick_period

    def subscribe(
        self,
        ticker: str,
        interval: str,
        on_event: StreamCallback,
    ) -> Callable[[], None]:
        if not is_intraday(interval):
            # Daily+ doesn't meaningfully stream; return a no-op unsub.
            return lambda: None

        stop = threading.Event()
        step_min = interval_minutes(interval)
        rng = random.Random(hash((ticker, interval, "stream")) & 0xFFFFFFFF)
        # Derive a plausible starting price from the ticker (matches the
        # synthetic-history generator so users see continuity if they
        # seed from it).
        price = 50.0 + rng.random() * 450.0

        def _make_bar(start: datetime, open_px: float, close_px: float,
                      high_px: float, low_px: float, volume: int) -> Candle:
            session = classify_session(start.hour, start.minute)
            return Candle(
                date=start,
                open=open_px, high=high_px, low=low_px, close=close_px,
                volume=volume, session=session,
            )

        def _run() -> None:
            # Bootstrap the first in-progress bar. Its open is the initial
            # price; high/low start equal; the first tick will move things.
            bar_start = floor_to_interval(datetime.now(), step_min)
            nonlocal price
            open_px = price
            high_px = price
            low_px = price
            close_px = price
            volume = 0

            # Emit the very first bar as a "rollover" so the consumer knows
            # a new in-progress bar has been opened. After that, ticks
            # update it until the next interval boundary.
            on_event("rollover", _make_bar(bar_start, open_px, close_px,
                                           high_px, low_px, volume))

            while not stop.is_set():
                # Sleep up to tick_period but wake early on stop.
                stop.wait(self._tick_period)
                if stop.is_set():
                    break

                now = datetime.now()
                # Did we cross into a new interval? If so, seal the
                # current bar implicitly (the consumer keeps it as-is)
                # and emit a fresh in-progress bar at the new boundary.
                new_start = floor_to_interval(now, step_min)
                if new_start > bar_start:
                    bar_start = new_start
                    open_px = close_px  # continuity across the boundary
                    high_px = low_px = close_px
                    volume = 0
                    on_event("rollover", _make_bar(bar_start, open_px, close_px,
                                                   high_px, low_px, volume))
                    continue

                # Ordinary tick: advance the close, update envelope,
                # accumulate volume, emit.
                sigma = 0.0015  # per-tick log-normal sigma (~0.15%)
                drift = rng.gauss(0, sigma)
                close_px = max(0.01, close_px * math.exp(drift))
                if close_px > high_px:
                    high_px = close_px
                if close_px < low_px:
                    low_px = close_px
                # Tick adds a small slug of volume.
                volume += rng.randint(500, 5_000)
                price = close_px  # keep for any future subscriptions
                on_event("tick", _make_bar(bar_start, open_px, close_px,
                                           high_px, low_px, volume))

        thread = threading.Thread(
            target=_run, name=f"stream-{ticker}-{interval}", daemon=True,
        )
        thread.start()

        def _unsubscribe() -> None:
            stop.set()

        return _unsubscribe
