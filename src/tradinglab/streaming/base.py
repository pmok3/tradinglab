"""Streaming-source protocol and registry.

A *stream source* produces bar-level events for a ``(ticker, interval)``
subscription:

* ``("tick", bar)``    — the in-progress bar's OHLCV changed; consumers
  should *replace* their rightmost bar.
* ``("rollover", bar)`` — a new interval has opened; consumers should
  *append*.

The subscribe call returns an ``unsubscribe`` callable. Callbacks run on
the source's own thread (typically a daemon), so consumers are expected
to marshal back to their UI thread — see ``ChartApp._on_stream_event``.

Registration mirrors the data-source package: each streaming module
calls :func:`register_stream` at import time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ..models import Candle

EventKind = str  # "tick" | "rollover"
StreamCallback = Callable[[EventKind, Candle], None]


class StreamSource(Protocol):
    """Emits bar-level events for a (ticker, interval) subscription.

    Contract:
      * ``subscribe`` returns an ``unsubscribe`` callable. After it
        returns, no *new* callbacks fire for that subscription — at
        most one already-in-flight callback may still complete, so
        consumers should be idempotent against a single trailing event.
      * Callbacks may fire from any thread; the consumer is responsible
        for marshalling to its UI thread.
      * Event kinds:
          - ``"tick"``     → the in-progress bar's OHLC/volume updated;
                             consumer should *replace* the rightmost bar.
          - ``"rollover"`` → a new bar has opened; consumer should
                             *append*.
    """

    def subscribe(
        self,
        ticker: str,
        interval: str,
        on_event: StreamCallback,
    ) -> Callable[[], None]:
        ...


# Global registry populated by sub-modules at import time.
STREAM_SOURCES: dict[str, StreamSource] = {}


def register_stream(name: str, source: StreamSource) -> None:
    """Register a streaming source under ``name`` (idempotent)."""
    STREAM_SOURCES[name] = source
