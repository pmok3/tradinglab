"""Tick source abstraction for live (or simulated-live) scan-runner ticks.

The :class:`ScanRunner` is *driven* externally — it does not own a
clock. For sandbox replay, the driver is
:class:`tradinglab.backtest.replay.SandboxController`. For a real
live-trading session, the driver is some upstream feed (a polling
HTTP fetch, a websocket, a kdb subscription, ...). This module
provides a small Protocol + a couple of concrete adapters so that
"feed → runner" plumbing can be wired without coupling to any
specific transport, and so that test code can drive the runner
deterministically.

Design notes
------------

* **No GUI coupling.** Subscribers receive :class:`Tick` objects on
  whichever thread the source uses internally; if the consumer is a
  Tk-based GUI, it is responsible for marshalling to its own thread
  (``app.after(0, ...)``). :class:`QueuedTickSource` provides the
  canonical thread-boundary buffer for that pattern.
* **No `runner.run()` call in this module.** The source emits ticks;
  a separate dispatcher (or the GUI's tick hook) is responsible for
  translating a :class:`Tick` into a runner call. This keeps the
  source decoupled from the runner's evolving signature.
* **Subscribers must be tolerant of background threads.** The
  protocol contract: ``subscribe(callback)`` registers a callable
  that will be invoked from the source's internal thread. Concrete
  sources isolate subscriber exceptions so a single bad subscriber
  cannot kill the source.

This is the streaming "last mile" — pure plumbing, zero strategy.
The interesting logic lives in the runner.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Mapping, Optional, Protocol, Sequence

from ..models import Candle

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tick payload
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tick:
    """One unit of streaming work for the scan runner.

    ``tick_id`` is monotonic per-source (autoincrement). ``forming``
    indicates that the last bar in every symbol's list is provisional
    (intrabar update). ``timestamp`` is the source's notion of "now"
    at emission — typically the source's local wall-clock or the
    feed's reported event time.

    ``candles_by_symbol`` is intentionally NOT copied. Sources that
    expose mutating in-place lists must serialize: either the
    consumer drains the tick before the next one is emitted, or the
    source emits snapshots. :class:`QueuedTickSource` is the standard
    snapshot-or-buffer choice.
    """

    tick_id: int
    candles_by_symbol: Mapping[str, List[Candle]]
    forming: bool
    timestamp: datetime


TickCallback = Callable[[Tick], None]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class TickSource(Protocol):
    """Minimal interface every concrete tick source implements."""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def subscribe(self, callback: TickCallback) -> None: ...
    def latest_candles_by_symbol(self) -> Mapping[str, List[Candle]]: ...


# ---------------------------------------------------------------------------
# Polling adapter
# ---------------------------------------------------------------------------


FetchFn = Callable[[Sequence[str]], Mapping[str, List[Candle]]]


class PollingTickSource:
    """Background-thread polling source.

    Calls ``fetch_fn(symbols)`` every ``interval_s`` seconds on a
    daemon thread. Each successful fetch becomes a :class:`Tick`
    dispatched to all subscribers. Subscriber exceptions are caught
    and logged; the source continues.

    ``forming`` is per-source policy — usually False for "give me the
    latest closed bar" feeds and True for intrabar feeds. Callers can
    flip it at runtime via :attr:`forming`.

    Threading: ``start`` / ``stop`` are idempotent and safe to call
    from any thread; subscriber callbacks run on the polling thread.
    """

    def __init__(
        self,
        fetch_fn: FetchFn,
        symbols: Sequence[str],
        *,
        interval_s: float = 1.0,
        forming: bool = False,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._fetch_fn = fetch_fn
        self._symbols: List[str] = list(symbols)
        self.interval_s = float(interval_s)
        self.forming = bool(forming)
        self._clock = clock

        self._subs: List[TickCallback] = []
        self._lock = threading.RLock()
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tick_id = 0
        self._latest: Dict[str, List[Candle]] = {}

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_evt.clear()
            t = threading.Thread(
                target=self._run_loop, name="polling-tick-source", daemon=True,
            )
            self._thread = t
            t.start()

    def stop(self) -> None:
        self._stop_evt.set()
        t: Optional[threading.Thread]
        with self._lock:
            t = self._thread
            self._thread = None
        if t is not None:
            t.join(timeout=max(self.interval_s * 2, 1.0))

    # -- pub/sub -------------------------------------------------------------

    def subscribe(self, callback: TickCallback) -> None:
        with self._lock:
            self._subs.append(callback)

    def unsubscribe(self, callback: TickCallback) -> None:
        with self._lock:
            try:
                self._subs.remove(callback)
            except ValueError:
                pass

    def latest_candles_by_symbol(self) -> Mapping[str, List[Candle]]:
        with self._lock:
            return dict(self._latest)

    def set_symbols(self, symbols: Sequence[str]) -> None:
        with self._lock:
            self._symbols = list(symbols)

    # -- internals -----------------------------------------------------------

    def _run_loop(self) -> None:
        while not self._stop_evt.is_set():
            self._tick_once()
            # Sleep in small slices so stop() is responsive.
            self._stop_evt.wait(timeout=self.interval_s)

    def _tick_once(self) -> None:
        with self._lock:
            symbols = list(self._symbols)
            subs = list(self._subs)
            forming = self.forming
        try:
            data = self._fetch_fn(symbols)
        except Exception:  # noqa: BLE001
            LOG.exception("PollingTickSource: fetch_fn raised; skipping tick")
            return
        if data is None:
            return
        # Snapshot the data dict so subscribers see a stable view.
        snapshot: Dict[str, List[Candle]] = {
            sym: list(candles) for sym, candles in data.items()
        }
        with self._lock:
            self._latest = snapshot
            self._tick_id += 1
            tick_id = self._tick_id
        tick = Tick(
            tick_id=tick_id,
            candles_by_symbol=snapshot,
            forming=forming,
            timestamp=self._clock(),
        )
        for cb in subs:
            try:
                cb(tick)
            except Exception:  # noqa: BLE001
                LOG.exception("PollingTickSource: subscriber raised; continuing")


# ---------------------------------------------------------------------------
# Queued thread-boundary adapter
# ---------------------------------------------------------------------------


class QueuedTickSource:
    """Thread-boundary buffer between an upstream :class:`TickSource`
    and a single-threaded consumer (typically a Tk main loop).

    The upstream source pushes :class:`Tick` payloads onto an
    internal :class:`queue.Queue`; the consumer drains via
    :meth:`drain` (one tick at a time) or :meth:`drain_all`. Drain
    methods are non-blocking by default — the typical Tk pattern is
    to schedule a periodic ``after(50, queued.drain_all_into(...))``
    callback.

    Bounded queues (``maxsize > 0``) drop the oldest tick on overflow
    so that a slow consumer doesn't unbounded-buffer ticks from a
    fast source. Drops are logged.

    Lifecycle: ``start`` / ``stop`` forward to the upstream source.
    """

    def __init__(
        self,
        upstream: TickSource,
        *,
        maxsize: int = 0,
    ) -> None:
        self._upstream = upstream
        self._queue: "queue.Queue[Tick]" = queue.Queue(maxsize=maxsize)
        self._maxsize = maxsize
        self._dropped = 0
        upstream.subscribe(self._on_tick)

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._upstream.start()

    def stop(self) -> None:
        self._upstream.stop()

    # -- subscribe (callback dispatch) --------------------------------------

    def subscribe(self, callback: TickCallback) -> None:
        # Direct subscription to the upstream — bypasses the queue.
        # Most callers should use ``drain`` instead.
        self._upstream.subscribe(callback)

    def latest_candles_by_symbol(self) -> Mapping[str, List[Candle]]:
        return self._upstream.latest_candles_by_symbol()

    # -- consumer-side drain -------------------------------------------------

    def drain(self, timeout: Optional[float] = None) -> Optional[Tick]:
        """Pop one tick. ``timeout=None`` returns immediately (None if empty)."""
        try:
            if timeout is None:
                return self._queue.get_nowait()
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain_all(self) -> List[Tick]:
        """Pop every queued tick (FIFO). Empty list if nothing pending."""
        out: List[Tick] = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    # -- producer-side ------------------------------------------------------

    def _on_tick(self, tick: Tick) -> None:
        if self._maxsize > 0 and self._queue.full():
            try:
                _ = self._queue.get_nowait()
                self._dropped += 1
                LOG.warning("QueuedTickSource: dropped oldest tick (queue full)")
            except queue.Empty:
                pass
        self._queue.put(tick)


__all__ = [
    "Tick",
    "TickCallback",
    "TickSource",
    "PollingTickSource",
    "QueuedTickSource",
]
