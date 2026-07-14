"""Per-source rate-limiter registry + adaptive controller for the prefetch
scheduler.

:class:`SourceBucketRegistry` is the **single accounting gate** for every fetch
path to a source (Decision 1) — foreground load, live poll, drilldown,
compare-fill, and the background scheduler all acquire from the same per-source
:class:`~tradinglab.data.rate_limiter.TokenBucket`.

:class:`AIMDRateController` self-tunes a source's rate (Decision 10) — used for
yfinance, which returns no rate-limit headers: additively increase the rate
while healthy, multiplicatively back off on an inferred throttle. Alpaca keeps
its header-driven rate. Internal / offline sources are effectively unlimited.

Pure — no Tk / IO / network. See ``PREFETCH_SCHEDULER_DESIGN.md`` §6.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable

from ..rate_limiter import TokenBucket

#: Effectively-unlimited rate for offline / internal sources.
UNLIMITED_RATE = 1_000_000.0
#: Fallback budget (req/min) for an unregistered / unknown source.
CONSERVATIVE_DEFAULT_RATE = 60.0
#: Default per-source request budgets (req/min). yfinance is AIMD-managed from
#: this starting point; Alpaca uses its plan limit; internal sources unlimited.
DEFAULT_RATES: dict[str, float] = {
    "yfinance": 100.0,
    "alpaca": 200.0,
    "polygon": 100.0,
    "synthetic": UNLIMITED_RATE,
    "synthetic-stream": UNLIMITED_RATE,
    "testdata": UNLIMITED_RATE,
}

_THROTTLE_RE = re.compile(
    r"\b(429|999|too many requests|rate limit)\b", re.IGNORECASE,
)


def _norm(source: str) -> str:
    return str(source or "").strip().lower()


def looks_throttled(
    error: BaseException | None,
    *,
    latency_s: float | None = None,
    latency_threshold_s: float = 5.0,
) -> bool:
    """Heuristic: does this fetch outcome look like a provider throttle?

    yfinance has no rate-limit headers, so we infer from the error text
    (HTTP 429 / Yahoo 999 / "rate limit" / "too many requests") or a latency
    spike. An ordinary error or a single empty result is NOT treated as throttle
    here (empty → the poison-symbol path); a *burst* of empties is a
    scheduler-level signal, not this per-call classifier.
    """
    if error is not None and _THROTTLE_RE.search(str(error)):
        return True
    if latency_s is not None and latency_s >= latency_threshold_s:
        return True
    return False


class SourceBucketRegistry:
    """Lazily-created, cached per-source token buckets (Decision 1)."""

    def __init__(
        self,
        *,
        defaults: dict[str, float] | None = None,
        fallback_rate: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._defaults = dict(DEFAULT_RATES)
        if defaults:
            self._defaults.update({_norm(k): float(v) for k, v in defaults.items()})
        self._fallback_rate = (
            None if fallback_rate is None else float(fallback_rate)
        )
        self._clock = clock
        self._buckets: dict[str, TokenBucket] = {}

    def _default_rate(self, source: str) -> float:
        fallback = (
            CONSERVATIVE_DEFAULT_RATE
            if self._fallback_rate is None else self._fallback_rate
        )
        return self._defaults.get(source, fallback)

    def bucket_for(self, source: str) -> TokenBucket:
        key = _norm(source)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(self._default_rate(key), clock=self._clock)
            self._buckets[key] = bucket
        return bucket

    def configure(
        self, source: str, rate_per_min: float, *, burst: float | None = None,
    ) -> None:
        self.bucket_for(source).configure(rate_per_min, burst=burst)

    def rate_for(self, source: str) -> float:
        return self.bucket_for(source).rate_per_min


class AIMDRateController:
    """Additive-increase / multiplicative-decrease rate controller (Decision 10).

    ``on_success`` bumps the rate by ``increase_step`` once every
    ``increase_every`` successes (up to ``max_rate``); ``on_throttle`` multiplies
    the rate by ``decrease_factor`` (down to ``min_rate``) and resets the success
    streak. When a ``bucket`` is supplied, every change is applied live via
    ``TokenBucket.configure``.
    """

    def __init__(
        self,
        *,
        initial: float,
        min_rate: float,
        max_rate: float,
        increase_step: float = 10.0,
        decrease_factor: float = 0.5,
        increase_every: int = 20,
        bucket: TokenBucket | None = None,
    ) -> None:
        self.min_rate = float(min_rate)
        self.max_rate = float(max_rate)
        self.increase_step = float(increase_step)
        self.decrease_factor = float(decrease_factor)
        self.increase_every = max(1, int(increase_every))
        self._bucket = bucket
        self._rate = min(self.max_rate, max(self.min_rate, float(initial)))
        self._success = 0
        self._apply()

    @property
    def rate(self) -> float:
        return self._rate

    def on_success(self) -> None:
        self._success += 1
        if self._success >= self.increase_every:
            self._success = 0
            self._rate = min(self.max_rate, self._rate + self.increase_step)
            self._apply()

    def on_throttle(self) -> None:
        self._success = 0
        self._rate = max(self.min_rate, self._rate * self.decrease_factor)
        self._apply()

    def _apply(self) -> None:
        if self._bucket is not None:
            self._bucket.configure(self._rate)


__all__ = [
    "UNLIMITED_RATE", "CONSERVATIVE_DEFAULT_RATE", "DEFAULT_RATES",
    "looks_throttled", "SourceBucketRegistry", "AIMDRateController",
    "unlimited_bucket_registry",
    "global_bucket_registry", "set_global_bucket_registry",
]


def unlimited_bucket_registry(
    clock: Callable[[], float] = time.monotonic,
) -> SourceBucketRegistry:
    """A throwaway registry where **every** source is effectively unlimited.

    Used by the prefetch scheduler's **shadow** (dry-run) mode so planning /
    dispatch never consumes a token from the process-wide
    :func:`global_bucket_registry` — otherwise shadow "observation" would spend
    real vendor tokens and throttle the live/foreground fetch it is meant to
    passively measure (principal-SWE review, Must-fix). Known sources AND
    unknown (BYOD) sources both ride :data:`UNLIMITED_RATE`.
    """
    return SourceBucketRegistry(
        defaults={src: UNLIMITED_RATE for src in DEFAULT_RATES},
        fallback_rate=UNLIMITED_RATE,
        clock=clock,
    )


# --------------------------------------------------------------- global singleton
# THE process-wide registry (Decision 1): the single accounting gate shared by
# every fetch path — the direct source fetchers (e.g. alpaca_source) AND the
# background prefetch scheduler acquire from the SAME per-source bucket.
_GLOBAL_REGISTRY: SourceBucketRegistry | None = None


def global_bucket_registry() -> SourceBucketRegistry:
    """Return the process-wide :class:`SourceBucketRegistry` (lazily created)."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = SourceBucketRegistry()
    return _GLOBAL_REGISTRY


def set_global_bucket_registry(registry: SourceBucketRegistry | None) -> None:
    """Replace (or reset with ``None``) the process-wide registry — for the app
    wiring and for test isolation."""
    global _GLOBAL_REGISTRY
    _GLOBAL_REGISTRY = registry
