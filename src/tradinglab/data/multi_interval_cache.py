"""Per-(symbol, interval) bar cache fed by the live 1m stream.

Companion to :mod:`tradinglab.streaming.resampler`. Owns one
:class:`BarsBuffer` per ``(symbol, interval)`` plus, for every
non-1m intraday interval, a :class:`BarResampler` that turns the
live 1-minute tick stream into rolled-up bars.

Lifecycle
---------

1. **Lazy backfill.** First :meth:`get_bars` for a key submits
   ``fetch_history(symbol, interval)`` to the executor, marks the
   key in-flight, and returns ``None``. While in-flight, repeat
   calls return ``None`` without re-submitting.
2. **Arrival.** When the fetch completes successfully, a
   :class:`BarsBuffer` is built from the candles, the in-flight
   marker is cleared, and ``on_arrival(symbol, interval)`` (if
   provided) fires on the executor thread.
3. **Live updates.** Each subsequent :meth:`on_1m_tick` is fanned
   out to every resampler tracking that symbol; the resulting
   :class:`BarEvent`s are folded into the matching ``BarsBuffer``
   (``append`` for closed events, ``update_last``/``append`` for
   forming events).
4. **1m fast path.** No resampler is built for 1m — the candle is
   written directly into the symbol's 1m buffer (creating it if
   needed) without consulting ``fetch_history``.

Failure handling
----------------

If ``fetch_history`` returns ``None`` *or* raises, the in-flight
marker is cleared so the next ``get_bars`` retries. Exceptions are
logged but do **not** propagate.

Threading
---------

`get_bars` is typically called from the GUI thread; fetch completion
runs on the executor's thread; ``on_1m_tick`` is called from the
streaming queue drain. All three paths take the same ``RLock`` to
keep the buffers / resamplers / in-flight set internally consistent.
The ``on_arrival`` callback is invoked **outside** the lock — the
caller's responsibility is to marshal back to its own thread (the
``ChartApp`` does this via ``app.after``).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Executor

from ..core.bars_buffer import BarsBuffer
from ..models import Candle
from ..streaming.resampler import BarResampler, supported_intervals

logger = logging.getLogger(__name__)


_FetchHistory = Callable[[str, str], list[Candle] | None]
_OnArrival = Callable[[str, str], None]
_Key = tuple[str, str]


class MultiIntervalCache:
    """Registry of `(symbol, interval)` BarsBuffers with lazy backfill.

    See module docstring for the full lifecycle contract.

    Parameters
    ----------
    fetch_history:
        Backfill callable. Returns ``None`` on failure (network, no
        data, ImportError) — the cache treats that as retryable. Set
        to ``None`` to disable backfill entirely (manual injection
        via :meth:`set_bars` only).
    executor:
        Where to run ``fetch_history``. ``None`` (the test default)
        runs the fetch synchronously inside :meth:`get_bars` itself —
        the call still returns ``None`` (per the lazy-load contract)
        but the buffer is populated by the time control returns.
    on_arrival:
        Optional callback fired after a successful backfill. Runs on
        whatever thread completed the fetch (the executor's worker,
        or the caller's thread for the synchronous path). Must
        marshal to the consumer's own thread.
    """

    def __init__(
        self,
        *,
        fetch_history: _FetchHistory | None = None,
        executor: Executor | None = None,
        on_arrival: _OnArrival | None = None,
    ) -> None:
        self._fetch_history: _FetchHistory | None = fetch_history
        self._executor: Executor | None = executor
        self._on_arrival: _OnArrival | None = on_arrival
        self._buffers: dict[_Key, BarsBuffer] = {}
        # Parallel candle list per buffer so BarsBuffer.view(candles=...)
        # has a back-reference for indicators without compute_arr.
        self._candles: dict[_Key, list[Candle]] = {}
        self._resamplers: dict[_Key, BarResampler] = {}
        self._inflight: set[_Key] = set()
        self._lock = threading.RLock()
        self._supported: frozenset[str] = frozenset(supported_intervals())

    # ------------------------------------------------------------------ public

    def get_bars(
        self, symbol: str, interval: str
    ) -> BarsBuffer | None:
        """Return the cached ``BarsBuffer`` for ``(symbol, interval)``.

        On the first call for a non-1m key, kicks off a background
        fetch via the executor (or runs it synchronously if no
        executor was supplied) and returns ``None``. Subsequent calls
        while the fetch is in flight also return ``None`` without
        re-submitting. Once the fetch completes, the buffer is
        cached and returned by future calls.

        ``1m`` is a no-op for backfill: live 1m bars are pushed
        directly via :meth:`on_1m_tick`, so ``get_bars(sym, "1m")``
        simply returns whatever's been streamed so far (possibly
        ``None`` until the first tick arrives).
        """
        key: _Key = (symbol, interval)
        submit = False
        with self._lock:
            buf = self._buffers.get(key)
            if buf is not None:
                return buf
            if interval == "1m":
                # 1m is fed by on_1m_tick exclusively; no backfill here.
                return None
            # Ensure a resampler exists for supported higher intervals.
            # Daily+ keys legitimately have no resampler — they stay
            # historical-only.
            if interval in self._supported and key not in self._resamplers:
                self._resamplers[key] = BarResampler(interval)
            if key in self._inflight:
                return None
            if self._fetch_history is None:
                return None
            self._inflight.add(key)
            submit = True

        if submit:
            if self._executor is None:
                # Synchronous path — used in tests and in process
                # paths that don't want a thread pool.
                self._run_fetch(symbol, interval)
            else:
                self._executor.submit(self._run_fetch, symbol, interval)
        return None

    def set_bars(
        self, symbol: str, interval: str, candles: list[Candle]
    ) -> None:
        """Inject a buffer manually. Used by tests and importers."""
        key: _Key = (symbol, interval)
        with self._lock:
            buf = BarsBuffer.from_candles(candles)
            self._buffers[key] = buf
            self._candles[key] = [_copy(c) for c in candles]
            self._inflight.discard(key)
            if (
                interval != "1m"
                and interval in self._supported
                and key not in self._resamplers
            ):
                self._resamplers[key] = BarResampler(interval)

    def on_1m_tick(
        self, symbol: str, candle: Candle, *, forming: bool
    ) -> None:
        """Push one 1m candle into the cache.

        Updates the symbol's 1m buffer directly, then fans out to
        every ``(symbol, *)`` resampler — folding their emitted
        :class:`BarEvent`s into the matching higher-interval buffer.
        Higher-interval buffers that haven't completed their backfill
        yet are skipped (the resampler still advances its bucket
        state so once the buffer arrives, future ticks are
        consistent).
        """
        with self._lock:
            self._update_1m_buffer(symbol, candle, forming=forming)
            self._fanout_to_resamplers(symbol, candle, forming=forming)

    def clear(self) -> None:
        """Drop every buffer, resampler, and pending fetch marker."""
        with self._lock:
            self._buffers.clear()
            self._candles.clear()
            self._resamplers.clear()
            self._inflight.clear()

    def stats(self) -> dict[str, int]:
        """Return summary counters for diagnostics / tests."""
        with self._lock:
            return {
                "buffers": len(self._buffers),
                "resamplers": len(self._resamplers),
                "inflight": len(self._inflight),
                "candles_total": sum(
                    len(c) for c in self._candles.values()
                ),
            }

    # --------------------------------------------------------------- internals

    def _run_fetch(self, symbol: str, interval: str) -> None:
        """Execute one backfill and wire its result into the cache.

        Runs on the executor's thread (or inline for the synchronous
        path). Always clears the in-flight marker, even on failure,
        so a future ``get_bars`` retries.
        """
        key: _Key = (symbol, interval)
        candles: list[Candle] | None = None
        try:
            assert self._fetch_history is not None
            candles = self._fetch_history(symbol, interval)
        except Exception:  # pragma: no cover - logged
            logger.exception(
                "MultiIntervalCache: fetch_history(%s, %s) raised",
                symbol, interval,
            )
            candles = None

        delivered = False
        with self._lock:
            self._inflight.discard(key)
            if candles:
                buf = BarsBuffer.from_candles(candles)
                self._buffers[key] = buf
                self._candles[key] = [_copy(c) for c in candles]
                delivered = True

        if delivered and self._on_arrival is not None:
            try:
                self._on_arrival(symbol, interval)
            except Exception:  # pragma: no cover - logged
                logger.exception(
                    "MultiIntervalCache: on_arrival(%s, %s) raised",
                    symbol, interval,
                )

    def _update_1m_buffer(
        self, symbol: str, candle: Candle, *, forming: bool
    ) -> None:
        key: _Key = (symbol, "1m")
        buf = self._buffers.get(key)
        if buf is None:
            buf = BarsBuffer()
            self._buffers[key] = buf
            self._candles[key] = []
        candles = self._candles[key]
        snapshot = _copy(candle)
        if candles and candles[-1].date == candle.date:
            buf.update_last(snapshot)
            candles[-1] = snapshot
        else:
            buf.append(snapshot)
            candles.append(snapshot)
        # ``forming`` is intentionally not used here: the 1m
        # buffer's last-row update vs append is driven by
        # whether the timestamp changed, which subsumes the
        # forming/closed distinction at this granularity.
        _ = forming

    def _fanout_to_resamplers(
        self, symbol: str, candle: Candle, *, forming: bool
    ) -> None:
        # Snapshot keys to avoid mutation-during-iteration if a fetch
        # arrival adds a resampler concurrently. The lock makes this
        # paranoid but cheap.
        keys = [k for k in self._resamplers if k[0] == symbol]
        for key in keys:
            resampler = self._resamplers[key]
            events = resampler.on_1m_tick(candle, forming=forming)
            buf = self._buffers.get(key)
            if buf is None:
                # Buffer not backfilled yet; resampler state still
                # advances so once it arrives we don't lose alignment.
                continue
            candles = self._candles.setdefault(key, [])
            for ev in events:
                if ev.closed:
                    snap = _copy(ev.candle)
                    if candles and candles[-1].date == ev.candle.date:
                        # Closing a bucket whose forming row was
                        # already in the buffer — promote in place.
                        buf.update_last(snap)
                        candles[-1] = snap
                    else:
                        buf.append(snap)
                        candles.append(snap)
                else:
                    snap = _copy(ev.candle)
                    if candles and candles[-1].date == ev.candle.date:
                        buf.update_last(snap)
                        candles[-1] = snap
                    else:
                        buf.append(snap)
                        candles.append(snap)


def _copy(c: Candle) -> Candle:
    """Return a fresh ``Candle`` with the same field values.

    The streaming pipeline mutates 1m candle instances during forming
    updates; the cache stores its own copies so a later mutation can't
    silently rewrite history.
    """
    return Candle(
        date=c.date,
        open=c.open, high=c.high, low=c.low, close=c.close,
        volume=c.volume, session=c.session,
    )


__all__ = ["MultiIntervalCache"]
