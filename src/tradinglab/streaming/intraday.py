"""Bounded chart-only adaptation of native minute streams; no history I/O."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta

from ..core.timezones import ET
from ..models import Candle
from .resampler import BarResampler, CorrectionUnavailable

CHART_INTERVALS = frozenset({"5m", "15m", "30m", "1h"})


def _exchange_bar(bar: Candle) -> Candle:
    if ET is None:
        raise ValueError("Exchange timezone unavailable")
    stamp = bar.date
    stamp = stamp.replace(tzinfo=ET) if stamp.tzinfo is None else stamp.astimezone(ET)
    return replace(bar, date=stamp)


class IntradayAdapter:
    """Retain two buckets and emit only aggregates with safe minute coverage.

    ``closed`` is explicit authoritative 1m data, not inferred from wall time.
    A subscription's first forming minute may omit earlier trades, so it cannot
    replace history until its authoritative correction arrives.
    """

    def __init__(
        self, interval: str, *, history: Sequence[Candle] = (), seed: Sequence[Candle] = (),
    ) -> None:
        if interval not in CHART_INTERVALS:
            raise ValueError(f"Unsupported chart stream interval: {interval}")
        self.resampler = BarResampler(interval, correction_buckets=2, session_segments=True)
        self.ready = False
        self.needs_reconcile = False
        self.message = "Warming stream: polling until a complete bucket is available"
        self._latest: datetime | None = None
        self._startup_minute: datetime | None = None
        self._history_edge: datetime | None = None
        self._started = False
        self.reconcile(history=history, seed=seed)

    def reconcile(self, *, history: Sequence[Candle], seed: Sequence[Candle] = ()) -> None:
        self.resampler.reset()
        self.ready = False
        self.needs_reconcile = False
        self._latest = None
        self._startup_minute = None
        self._started = False
        self._history_edge = None
        self.message = "Warming stream: polling until a complete bucket is available"
        self.observe_history(history)
        if self.needs_reconcile:
            return
        # The newest cached minute can still be forming. Never label it final.
        for bar in seed[-(2 * self.resampler.target_minutes + 1):-1]:
            self.resampler.on_1m_tick(_exchange_bar(bar), forming=False)

    def observe_history(self, history: Sequence[Candle]) -> None:
        """Protect freshly polled aggregates without discarding minute warm-up."""
        self._history_edge = _exchange_bar(history[-1]).date if history else None
        for bar in history[-2:]:
            stamp = _exchange_bar(bar).date
            if self.resampler.bucket_start_for(stamp) != stamp:
                self.needs_reconcile = True
                self.message = "Stream/history bucket alignment differs; polling"
                return
        if self._latest is not None and self._history_edge is not None:
            start = self.resampler.bucket_start_for(self._latest)
            end = self.resampler.bucket_end_for(start) - timedelta(minutes=1)
            if start <= self._history_edge and not self.resampler.covers(start, end):
                self.ready = False
                self.message = "Warming stream: polling until a complete bucket is available"

    def _require_reconcile(self, message: str) -> list[tuple[str, Candle]]:
        self.ready = False
        self.needs_reconcile = True
        self.message = message
        return []

    def apply(self, kind: str, bar: Candle) -> list[tuple[str, Candle]]:
        if self.needs_reconcile:
            return []
        bar = _exchange_bar(bar)
        stamp = bar.date
        if not self._started:
            self._started = True
            if kind != "closed":
                self._startup_minute = stamp
        if self._latest is not None and stamp > self._latest + timedelta(minutes=1):
            return self._require_reconcile("Minute coverage gap; reconciling history while polling")
        if self._latest is None or stamp > self._latest:
            self._latest = stamp
        if kind == "closed" and stamp == self._startup_minute:
            self._startup_minute = None
        try:
            events = self.resampler.on_1m_tick(bar, forming=kind != "closed")
        except CorrectionUnavailable:
            return self._require_reconcile("Correction outside minute coverage; reconciling history while polling")

        out: list[tuple[str, Candle]] = []
        current = self.resampler.bucket_start_for(self._latest)
        for event in events:
            start = event.candle.date
            end = self.resampler.bucket_end_for(start) - timedelta(minutes=1)
            through = min(end, self._latest)
            if self._history_edge is not None and start <= self._history_edge:
                # A REST candle contains no "covered through minute" metadata.
                # Require the whole bucket before replacing that opaque aggregate.
                through = end
            if not self.resampler.covers(start, through):
                continue
            if self._startup_minute is not None and start <= self._startup_minute <= end:
                continue
            authoritative = self.resampler.covers(start, end, sealed=True)
            if event.closed or authoritative:
                out.append(("closed", event.candle))
            else:
                out.append(("tick", event.candle))
            if start == current:
                self.ready = True
                self.message = "Live stream"
        return out
