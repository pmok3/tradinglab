"""Client-side token-bucket rate limiter.

A small, thread-safe token bucket used to **proactively** pace outbound
vendor API calls so we stay under a per-minute quota instead of reactively
absorbing HTTP 429s. Currently used for Alpaca (see
``alpaca_source._http_get_page``), whose free "Basic" plan allows 200
requests/minute and whose paid plan allows 10,000/minute.

Why a token bucket (and not exponential backoff) for a *fixed quota*: a
per-minute limit has a knowable recovery time, so the right primary tool is
proactive pacing — shape traffic to stay under budget — with the reactive
``Retry-After`` handling in ``alpaca_source`` as a safety net for the rare
overshoot. See the perf-review discussion in ``alpaca_source.spec.md``.

Sizing (see :meth:`TokenBucket.configure`): the sustained refill rate is set
to ``safety`` (default 0.9) of the nominal limit, and the bucket capacity to
the remaining headroom, so the worst-case burst in any rolling 60 s window
(``capacity + refill_per_sec * 60``) stays at or under the nominal limit.
For 200/min that's ~3 tokens/s sustained + a 20-token burst; for 10,000/min,
~150/s + a 1,000-token burst.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucket:
    """Thread-safe token bucket with continuous (fractional) refill.

    ``acquire`` blocks until a token is available; ``try_acquire`` is the
    non-blocking primitive (fully deterministic under an injected ``clock``,
    so the pacing math is unit-testable with no real sleeps). The refill rate
    can be changed live via :meth:`configure` — used so an Alpaca free→paid
    tier change (or a header-driven auto-detect) takes effect without a
    restart.
    """

    def __init__(
        self,
        rate_per_min: float,
        *,
        burst: float | None = None,
        safety: float = 0.9,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._tokens = 0.0
        self._last = clock()
        self._refill_per_sec = 0.0
        self._capacity = 0.0
        self._rate_per_min = 0.0
        with self._lock:
            self._configure_locked(rate_per_min, burst, safety)
            self._tokens = self._capacity  # start full — allow an initial burst

    # -- configuration -----------------------------------------------------

    def _configure_locked(
        self, rate_per_min: float, burst: float | None, safety: float
    ) -> None:
        rate_per_min = max(float(rate_per_min), 1e-9)
        safety = min(max(float(safety), 0.0), 1.0)
        self._rate_per_min = rate_per_min
        # Sustained refill at ``safety`` of the nominal limit; capacity is the
        # remaining headroom so ``capacity + refill*60 <= rate_per_min``.
        self._refill_per_sec = max(rate_per_min * safety / 60.0, 1e-9)
        if burst is None:
            # Headroom so worst-case rolling-minute (capacity + refill*60)
            # stays at/under the nominal limit. ``round`` avoids float warts
            # like 200*(1-0.9)=19.9999 → a clean 20-token burst.
            burst = max(1.0, round(rate_per_min * (1.0 - safety)))
        self._capacity = max(1.0, float(burst))
        # Never hold more than the (possibly reduced) capacity.
        self._tokens = min(self._tokens, self._capacity)

    def configure(
        self, rate_per_min: float, *, burst: float | None = None, safety: float = 0.9
    ) -> None:
        """Re-set the sustained rate / capacity live (e.g. tier change)."""
        with self._lock:
            self._refill_locked()  # bank tokens accrued under the old rate first
            self._configure_locked(rate_per_min, burst, safety)

    @property
    def rate_per_min(self) -> float:
        return self._rate_per_min

    # -- acquire -----------------------------------------------------------

    def _refill_locked(self) -> None:
        now = self._clock()
        dt = now - self._last
        if dt > 0:
            self._tokens = min(self._capacity, self._tokens + dt * self._refill_per_sec)
            self._last = now

    def try_acquire(self, n: float = 1.0) -> bool:
        """Consume ``n`` tokens if available right now; else return False.

        Non-blocking. Deterministic under an injected ``clock`` — the unit
        of test coverage for the pacing math.
        """
        with self._lock:
            self._refill_locked()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def time_until_available(self, n: float = 1.0) -> float:
        """Seconds until ``n`` tokens would be available (0.0 if already)."""
        with self._lock:
            self._refill_locked()
            if self._tokens >= n:
                return 0.0
            return (n - self._tokens) / self._refill_per_sec

    def acquire(
        self,
        n: float = 1.0,
        *,
        cancel: threading.Event | None = None,
        poll: float = 0.02,
    ) -> bool:
        """Block until ``n`` tokens are consumed; True on success.

        Returns False only if ``cancel`` is set while waiting. ``n`` is
        clamped to capacity (asking for more than the bucket can ever hold
        would otherwise wait forever). Polls every ``poll`` seconds so a
        ``cancel`` (e.g. the preloader's Stop) is honoured promptly; the
        continuous refill keeps a single-token wait short (≈ 1 / refill_rate).
        """
        with self._lock:
            n = min(float(n), self._capacity)
        while True:
            if self.try_acquire(n):
                return True
            if cancel is not None and cancel.is_set():
                return False
            time.sleep(poll)


__all__ = ["TokenBucket"]
