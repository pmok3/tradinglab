"""Shared Schwab bar/quote subscriptions, with optional polling-fallback health.

The worker in schwab_connection owns all auth, network and reconnect work.
Subscription edits only change locked desired state; callbacks run outside
that lock. LEVELONE bars are provisional snapshots, CHART_EQUITY corrects by
timestamp. No clock-generated bars or synchronous REST seeds are emitted.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..models import Candle
from .base import StreamCallback, StreamState, StreamStatus
from .quotes import QuoteCallback
from .schwab_aggregator import MinuteBarBuilder, chart_equity_to_candle
from .schwab_connection import (  # noqa: F401 - preserve public helper imports
    _BACKOFF,
    _STALE_TIMEOUT,
    CHART_EQUITY_FIELD_IDS,
    LEVELONE_FIELD_IDS,
    USER_PREFERENCE_URL,
    _Connection,
    _is_login_ok,
    build_login_request,
    build_subs_request,
    fetch_streamer_info,
)

if TYPE_CHECKING:
    from .schwab_quotes import SchwabQuoteSubscription

LOG = logging.getLogger(__name__)
_SYMBOL_STALE_SECONDS = 90.0


class _Subscription:
    def __init__(self, symbol: str, callback: StreamCallback) -> None:
        self.symbol = symbol
        self.callback = callback
        self.builder = MinuteBarBuilder()
        self.authoritative_through: datetime | None = None
        self.alive = True


class SchwabStreamSource:
    """One lazily started worker/socket shared by both subscription axes.

    ``get_status(ticker=None)`` is optional, not part of StreamSource. The
    no-argument form describes transport/ACK health; a ticker additionally
    requires a usable bar in this connection epoch. ``close`` is terminal:
    registry owners must construct a new instance after explicitly closing.
    """

    def __init__(
        self, *, seed_lookup: Callable[[str, str], float | None] | None = None,
    ) -> None:
        if seed_lookup is not None:
            LOG.warning("schwab-stream: seed_lookup is deprecated and is not called")
        self._lock = threading.RLock()
        self._closed = False
        self._subs: dict[str, list[_Subscription]] = {}
        self._quote_callbacks: list[SchwabQuoteSubscription] = []
        self._quote_symbols: set[str] = set()
        self._symbols_subscribed: set[str] = set()
        self._generation = 0
        self._images: dict[str, int] = {}
        self._symbol_received: dict[str, float] = {}
        self._symbol_bar_time: dict[str, float] = {}
        self._connection: _Connection | None = None
        self._status = StreamStatus(StreamState.IDLE, "No subscriptions", time.monotonic())

    def get_status(self, ticker: str | None = None) -> StreamStatus:
        with self._lock:
            status = self._status
            if (
                status.state == StreamState.LIVE and status.last_received_at is not None
                and time.monotonic() - status.last_received_at >= _STALE_TIMEOUT
            ):
                status = replace(status, state=StreamState.STALE, message="Streamer heartbeat timed out")
            if ticker is None or status.state != StreamState.LIVE:
                return status
            symbol = ticker.strip().upper()
            if symbol not in self._subs:
                return replace(status, state=StreamState.IDLE, message="No bar subscription")
            received = self._symbol_received.get(symbol)
            if received is None:
                return replace(status, state=StreamState.CONNECTING,
                               message="Waiting for the first usable symbol bar", last_received_at=None)
            if (
                time.monotonic() - received > _SYMBOL_STALE_SECONDS
                or time.time() - self._symbol_bar_time[symbol] > _SYMBOL_STALE_SECONDS
            ):
                return replace(status, state=StreamState.STALE,
                               message="No recent symbol bar; shared transport is healthy",
                               last_received_at=received)
            return replace(status, last_received_at=received)

    def _status_locked(self, state: StreamState, message: str) -> None:
        if (state, message) != (self._status.state, self._status.message):
            self._status = replace(self._status, state=state, message=message, changed_at=time.monotonic())

    def _set_status(self, conn: _Connection, state: StreamState, message: str) -> None:
        with self._lock:
            if self._connection is conn and not self._closed and not conn.stopping:
                self._status_locked(state, message)

    def _traffic(self, conn: _Connection) -> None:
        with self._lock:
            if self._connection is conn and not self._closed and not conn.stopping:
                self._status = replace(self._status, last_received_at=time.monotonic())

    def _new_epoch(self, conn: _Connection) -> None:
        with self._lock:
            if self._connection is conn:
                self._symbol_received.clear()
                self._symbol_bar_time.clear()
                self._status = replace(self._status, last_received_at=None, generation=self._status.generation + 1)
                for subs in self._subs.values():
                    for sub in subs:
                        sub.builder = MinuteBarBuilder()

    def _snapshot(self) -> tuple[set[str], set[str], dict[str, int], int]:
        with self._lock:
            return set(self._subs), set(self._quote_symbols), dict(self._images), self._generation

    def _ready(self, conn: _Connection, generation: int) -> bool:
        with self._lock:
            if self._closed or conn.stopping or self._connection is not conn or generation != self._generation:
                return False
            self._status_locked(StreamState.LIVE, "Streaming services acknowledged")
            return True

    def _refresh_symbols_locked(self) -> set[str]:
        self._quote_symbols = set().union(*(s._symbols for s in self._quote_callbacks))
        self._symbols_subscribed = set(self._subs) | self._quote_symbols
        return self._symbols_subscribed

    def _changed_locked(self, reimage: Iterable[str] = ()) -> None:
        self._generation += 1
        wanted = self._refresh_symbols_locked()
        self._images = {s: v for s, v in self._images.items() if s in wanted}
        for symbol in reimage:
            self._images[symbol] = self._generation
        if not wanted:
            self._status_locked(StreamState.IDLE, "No subscriptions")
            if self._connection is not None:
                self._connection.shutdown()
        else:
            self._status_locked(StreamState.CONNECTING, "Updating streaming subscriptions")
            if self._connection is None:
                self._connection = _Connection(self)
                self._connection.start()

    def _connection_finished(self, conn: _Connection) -> None:
        with self._lock:
            if self._connection is not conn:
                return
            self._connection = None
            # A new subscriber can arrive while the old worker is unwinding.
            # Start its successor ONLY after the old socket's finally closed.
            if conn.stopping and not self._closed and self._symbols_subscribed:
                self._connection = _Connection(self)
                self._connection.start()

    def subscribe(
        self, ticker: str, interval: str, on_event: StreamCallback,
    ) -> Callable[[], None]:
        if interval != "1m":
            raise ValueError("Schwab streaming supports only 1m bars")
        symbol = ticker.strip().upper()
        if not symbol:
            raise ValueError("A streaming symbol is required")
        sub = _Subscription(symbol, on_event)
        with self._lock:
            if self._closed:
                raise RuntimeError("Schwab stream source is closed")
            self._subs.setdefault(symbol, []).append(sub)
            self._symbol_received.pop(symbol, None)
            self._symbol_bar_time.pop(symbol, None)
            self._changed_locked([symbol])

        def unsubscribe() -> None:
            with self._lock:
                if not sub.alive:
                    return
                sub.alive = False
                self._subs[symbol].remove(sub)
                if not self._subs[symbol]:
                    del self._subs[symbol]
                    self._symbol_received.pop(symbol, None)
                    self._symbol_bar_time.pop(symbol, None)
                self._changed_locked()
        return unsubscribe

    def subscribe_quotes(self, symbols: Sequence[str], on_quote: QuoteCallback) -> SchwabQuoteSubscription:
        from .schwab_quotes import SchwabQuoteSubscription

        sub = SchwabQuoteSubscription(self, on_quote)
        with self._lock:
            if self._closed:
                raise RuntimeError("Schwab stream source is closed")
            self._quote_callbacks.append(sub)
        sub.set_symbols(symbols)
        return sub

    def _set_quote_symbols(self, sub: SchwabQuoteSubscription, wanted: set[str]) -> None:
        with self._lock:
            if self._closed or sub._closed:
                return
            added = wanted - sub._symbols
            if wanted == sub._symbols:
                return
            sub._symbols = wanted
            # Every newcomer needs an image, including a second consumer
            # of a symbol already subscribed by another quote or bar user.
            self._changed_locked(added)

    def _drop_quote_subscription(self, sub: SchwabQuoteSubscription) -> None:
        with self._lock:
            if sub._closed:
                return
            sub._closed = True
            sub._symbols = set()
            self._quote_callbacks.remove(sub)
            self._changed_locked()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for subs in self._subs.values():
                for sub in subs:
                    sub.alive = False
            for sub in self._quote_callbacks:
                sub._closed = True
                sub._symbols = set()
            self._subs.clear()
            self._quote_callbacks.clear()
            self._images.clear()
            self._symbol_received.clear()
            self._symbol_bar_time.clear()
            self._refresh_symbols_locked()
            self._status_locked(StreamState.CLOSED, "Stream source closed")
            if self._connection is not None:
                self._connection.shutdown()

    def _dispatch_quote(self, symbol: str, decoded: dict[str, Any]) -> None:
        from .schwab_quotes import quote_from_levelone

        quote = quote_from_levelone(symbol, decoded)
        with self._lock:
            subs = list(self._quote_callbacks)
        for sub in subs:
            try:
                sub.deliver(quote)
            except Exception:
                LOG.error("schwab-stream: quote subscriber callback raised")

    def _deliver(self, sub: _Subscription, events: list[tuple[str, Candle]]) -> None:
        for kind, candle in events:
            with self._lock:
                if not sub.alive:
                    return
                stamp = candle.date.timestamp()
                if stamp >= self._symbol_bar_time.get(sub.symbol, float("-inf")):
                    self._symbol_received[sub.symbol] = time.monotonic()
                    self._symbol_bar_time[sub.symbol] = stamp
            try:
                sub.callback(kind, candle)
            except Exception:
                LOG.error("schwab-stream: bar subscriber callback raised")

    def _dispatch_levelone(self, symbol: str, decoded: dict[str, Any]) -> None:
        self._dispatch_quote(symbol, decoded)
        with self._lock:
            events = []
            for sub in self._subs.get(symbol, ()):
                items = sub.builder.apply_levelone(decoded)
                if sub.authoritative_through is not None:
                    items = [(kind, c) for kind, c in items if c.date > sub.authoritative_through]
                events.append((sub, items))
        for sub, items in events:
            self._deliver(sub, items)

    def _dispatch_chart_equity(self, symbol: str, decoded: dict[str, Any]) -> None:
        candle = chart_equity_to_candle(decoded)
        if candle is None:
            return
        with self._lock:
            subs = list(self._subs.get(symbol, ()))
            for sub in subs:
                if sub.authoritative_through is None or candle.date > sub.authoritative_through:
                    sub.authoritative_through = candle.date
        for sub in subs:
            self._deliver(sub, [("closed", candle)])
