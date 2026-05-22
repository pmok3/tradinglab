"""Cross-symbol reference-data registry for indicators (e.g. RRVOL).

Some indicators need OHLCV for a *second* symbol — RRVOL divides the
primary's RVOL by SPY's RVOL of the same flavor. This module is the
bridge between the indicator compute path (synchronous, on the render
or scanner-worker thread) and the app's async fetch machinery.

Design
------

* **App-scoped singleton state** held at module level. The hosting
  ``ChartApp`` registers a *provider* (a callable that schedules a
  fetch and resolves a list of candles) plus an *on-arrival* callback
  that the registry invokes once a fetch completes. Because there is
  only one ChartApp per process in production, a singleton is the
  simplest correct shape; tests reset state via :func:`clear`.

* **Source-aware cache key** ``(source, symbol, interval)``. Switching
  data sources (e.g. yfinance ↔ synthetic) MUST NOT reuse SPY bars
  fetched from the prior source — different timestamp conventions and
  history depths.

* **Generation counter**. Every cache mutation bumps a monotonic
  counter that callers (e.g. :class:`IndicatorCache`) can include in
  their compute hash, OR that the on-arrival callback uses to clear
  the indicator cache. Either approach correctly invalidates stale
  RRVOL outputs.

* **Synchronous read path**. Indicators call
  :func:`get_reference_bars` from inside ``compute_arr``. On a cache
  hit they get a :class:`Bars` view immediately. On a miss the
  registry returns ``None`` AND schedules a background fetch (deduped
  via ``_inflight``); the indicator emits all-NaN for this render and
  the on-arrival callback triggers a re-render once data arrives.

* **Thread-safety**. All mutation paths are guarded by a single
  module-level ``RLock``. The provider is invoked under the lock only
  long enough to start the work; the actual fetch runs without the
  lock held.

Public API
----------

``set_provider(provider, *, on_arrival=None)`` — install the app's
    fetcher. ``provider(source, symbol, interval) -> None`` is
    expected to schedule the fetch and call
    :func:`set_reference_bars` once the result lands. A None provider
    disables auto-fetch (tests usually inject results directly).

``get_reference_bars(source, symbol, interval) -> Optional[Bars]`` —
    cache-only read. Triggers ``provider`` on miss but never blocks.

``set_reference_bars(source, symbol, interval, bars)`` — populate the
    cache. Bumps the generation counter and invokes the on-arrival
    callback. Used by the provider on completion AND by tests/tools
    that want to inject data synchronously.

``generation() -> int`` — current monotonic version. Useful for cache
    keys.

``clear()`` — reset all state. Tests only.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from .bars import Bars

# ----------------------------------------------------------------------
# Module state
# ----------------------------------------------------------------------

_lock = threading.RLock()
_cache: dict[tuple[str, str, str], Bars] = {}
_inflight: set[tuple[str, str, str]] = set()
_provider: Callable[[str, str, str], None] | None = None
_on_arrival: Callable[[], None] | None = None
_generation: int = 0


# Provider callable: ``(source, symbol, interval) -> None``.
# It is the provider's responsibility to call
# :func:`set_reference_bars` (sync or async) when the fetch finishes.
ProviderFn = Callable[[str, str, str], None]
# On-arrival callback: ``() -> None``.
OnArrivalFn = Callable[[], None]


def _norm(source: str, symbol: str, interval: str) -> tuple[str, str, str]:
    return (source.lower(), symbol.upper(), interval)


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def set_provider(
    provider: ProviderFn | None,
    *,
    on_arrival: OnArrivalFn | None = None,
) -> None:
    """Install (or clear) the data provider + arrival callback.

    Calling with ``provider=None`` disables auto-fetching: subsequent
    cache misses simply return ``None`` without scheduling work. This
    is what tests and tournament tools want — they pre-populate the
    cache via :func:`set_reference_bars` directly.
    """
    global _provider, _on_arrival
    with _lock:
        _provider = provider
        _on_arrival = on_arrival


def generation() -> int:
    """Return the current monotonic generation counter."""
    with _lock:
        return _generation


def get_reference_bars(
    source: str, symbol: str, interval: str,
) -> Bars | None:
    """Return cached :class:`Bars` for ``(source, symbol, interval)`` or ``None``.

    On miss, schedules a background fetch via the registered provider
    (deduped by ``(source, symbol, interval)``) but never blocks.
    """
    if not source or not symbol or not interval:
        return None
    key = _norm(source, symbol, interval)
    provider_to_call: ProviderFn | None = None
    with _lock:
        bars = _cache.get(key)
        if bars is not None:
            return bars
        # Cache miss — schedule fetch unless one is already in flight.
        if _provider is not None and key not in _inflight:
            _inflight.add(key)
            provider_to_call = _provider
    if provider_to_call is not None:
        try:
            provider_to_call(*key)
        except Exception:  # noqa: BLE001
            # Don't let a misbehaving provider poison the registry —
            # release the inflight slot so a future read can retry.
            with _lock:
                _inflight.discard(key)
    return None


def set_reference_bars(
    source: str, symbol: str, interval: str, bars: Bars,
) -> None:
    """Populate the cache and notify subscribers.

    Bumps the generation counter (so any cache key that incorporates
    it invalidates) and invokes the on-arrival callback (so the
    indicator cache can be cleared and a re-render triggered). Either
    mechanism alone is sufficient; both are exposed for flexibility.
    """
    if bars is None:
        return
    key = _norm(source, symbol, interval)
    cb: OnArrivalFn | None = None
    global _generation
    with _lock:
        _cache[key] = bars
        _inflight.discard(key)
        _generation += 1
        cb = _on_arrival
    if cb is not None:
        try:
            cb()
        except Exception:  # noqa: BLE001
            # Arrival callback failures must not corrupt cache state.
            pass


def mark_fetch_failed(source: str, symbol: str, interval: str) -> None:
    """Release the in-flight slot without populating the cache.

    Providers that detect a fetch failure (network error, empty
    candle list) should call this so a future ``get_reference_bars``
    can retry instead of silently returning None forever.
    """
    key = _norm(source, symbol, interval)
    with _lock:
        _inflight.discard(key)


def clear() -> None:
    """Reset all module state. Tests only."""
    global _provider, _on_arrival, _generation
    with _lock:
        _cache.clear()
        _inflight.clear()
        _provider = None
        _on_arrival = None
        _generation = 0
