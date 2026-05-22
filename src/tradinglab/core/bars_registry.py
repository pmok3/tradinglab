"""Shared ``(symbol, interval) → (BarsBuffer, IndicatorMemo)`` registry.

Layer 0 of the exit-strategies design (see ``exits_v1_plan.md``).
:class:`BarsRegistry` is the value-add layer that sits **on top of**
:class:`tradinglab.data.multi_interval_cache.MultiIntervalCache` and
adds an :class:`~tradinglab.scanner.engine.IndicatorMemo` lifecycle
keyed by the same ``(symbol, interval)`` tuple. It owns NO buffers
itself — every :meth:`get_view` re-acquires the buffer from the cache,
so the cache remains the single source of truth for OHLCV data.

The registry is the seam that lets both :class:`ScanRunner` and (in a
later slice) the future ``ExitEvaluator`` share one memo per
``(symbol, interval)`` per tick — meaning a 5m EMA(50) computed for a
scan condition is reused when an exit trigger references the same
indicator on the same bars, instead of being recomputed from scratch.

Naming
------

The plan calls this module ``IndicatorCacheRegistry``. There is
already an unrelated :mod:`tradinglab.indicators.cache` /
``IndicatorCache``; this module deliberately picks
:class:`BarsRegistry` to avoid the name collision while keeping the
"registry of bars+memos" semantics explicit.

Threading
---------

The registry is single-writer (typically the GUI thread) on
:meth:`get_view`. The underlying :class:`MultiIntervalCache` takes its
own ``RLock`` for buffer access; the registry adds no further
locking. Memos are mutable but not shared across symbols / intervals,
so per-key access is naturally serialised by the GUI-thread caller.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..data.multi_interval_cache import MultiIntervalCache
from ..models import Candle
from ..scanner.engine import IndicatorMemo
from .bars import Bars
from .bars_buffer import BarsBuffer

# Fingerprint shape mirrors ``scanner.runner._Fingerprint`` exactly so
# the registry's reuse / rebuild semantics match what the runner does
# in its local-state path. ``(id_of_list, n, ts_ns, open, high, low,
# close, volume)``.
_Fingerprint = tuple[int, int, int, float, float, float, float, float]


def _fingerprint(candles: Sequence[Candle]) -> _Fingerprint:
    """Compute the ``(id, len, last_ts_ns, last_OHLCV)`` fingerprint."""
    n = len(candles)
    if n == 0:
        return (0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    last = candles[-1]
    try:
        ts_ns = int(last.date.timestamp() * 1_000_000_000)
    except (AttributeError, OSError, ValueError):
        ts_ns = 0

    def _f(name: str) -> float:
        try:
            return float(getattr(last, name))
        except (AttributeError, TypeError, ValueError):
            return 0.0

    return (
        id(candles), n, ts_ns,
        _f("open"), _f("high"), _f("low"), _f("close"), _f("volume"),
    )


@dataclass(frozen=True)
class BarsView:
    """One ``(bars, memo)`` pair returned by :meth:`BarsRegistry.get_view`.

    The :class:`Bars` is a frozen NumPy view over the buffer's storage
    (no copy). The :class:`IndicatorMemo` is the cached output store
    for indicators computed against these bars; the registry rebuilds
    it whenever the underlying candle list's fingerprint changes.

    The ``buffer`` and ``fingerprint`` fields are provided for callers
    that want to inspect the underlying state (tests, diagnostics);
    the canonical "do work against this view" handles are ``bars``
    and ``memo``.

    Frozen for thread-share safety; ``memo`` is itself mutable but it
    is owned by the registry — callers MUST NOT mutate it directly
    (use :meth:`BarsRegistry.invalidate` to drop and rebuild).
    """

    bars: Bars
    memo: IndicatorMemo
    fingerprint: tuple[Any, ...]
    buffer: BarsBuffer


_Key = tuple[str, str]


class BarsRegistry:
    """Shared ``(symbol, interval) → (BarsBuffer, IndicatorMemo)`` registry.

    Acts as a thin value-add layer on top of
    :class:`MultiIntervalCache`:

    * The cache owns buffers; the registry owns memos.
    * On :meth:`get_view`, the registry pulls the current buffer from
      the cache, computes a fingerprint of the candle list, and either
      reuses (same fingerprint) or rebuilds (changed) the cached
      :class:`IndicatorMemo`.
    * If the cache has no buffer yet for a key (lazy-load in flight,
      or the key was never requested), :meth:`get_view` returns
      ``None`` — callers (the runner, exit evaluator) treat this as
      "skip this symbol gracefully on this tick".

    The registry has no notion of "request this key" or stale
    eviction — that is the cache's job (and a future slice). Memo
    invalidation is explicit via :meth:`invalidate` / :meth:`clear`.
    """

    def __init__(self, multi_interval_cache: MultiIntervalCache) -> None:
        """``multi_interval_cache`` is the source of truth for buffers.

        The registry holds a reference to it and consults it on every
        :meth:`get_view`. Tests can pass a fresh
        :class:`MultiIntervalCache` populated via
        :meth:`MultiIntervalCache.set_bars`.
        """
        self._cache: MultiIntervalCache = multi_interval_cache
        self._memos: dict[_Key, IndicatorMemo] = {}
        self._fingerprints: dict[_Key, _Fingerprint] = {}
        self._stats: dict[str, int] = {
            "views_built": 0,
            "memos_reused": 0,
            "memos_rebuilt": 0,
        }

    # ------------------------------------------------------------------ public

    def get_view(self, symbol: str, interval: str) -> BarsView | None:
        """Return a :class:`BarsView` for ``(symbol, interval)``.

        Returns ``None`` if the cache has no buffer for the key yet
        (lazy-load in flight). Otherwise returns a fresh
        :class:`BarsView` with:

        * ``bars`` — a :class:`Bars` snapshot of the buffer's current
          populated prefix (no copy).
        * ``memo`` — the cached :class:`IndicatorMemo`, reused when
          the candle list's fingerprint matches the previous call,
          rebuilt otherwise.

        The fingerprint shape matches ``scanner.runner._fingerprint``
        exactly (``(id, n, last_ts_ns, last_OHLCV)``) so cross-layer
        reuse semantics agree.
        """
        key: _Key = (symbol, interval)
        buf = self._cache.get_bars(symbol, interval)
        if buf is None:
            return None

        candles = self._cache_candles(symbol, interval)
        if candles is None:
            # Buffer exists but the parallel candle list is missing —
            # treat as not-yet-ready so callers skip gracefully rather
            # than feeding indicators a mismatched view.
            return None

        fp = _fingerprint(candles)
        prev_memo = self._memos.get(key)
        prev_fp = self._fingerprints.get(key)

        if prev_memo is not None and prev_fp == fp:
            memo = prev_memo
            self._stats["memos_reused"] += 1
        else:
            memo = IndicatorMemo(candles=list(candles))
            self._memos[key] = memo
            self._fingerprints[key] = fp
            self._stats["memos_rebuilt"] += 1

        bars = buf.view(candles=list(candles))
        # Bind the snapshot view onto the memo so indicator computes
        # share it instead of building their own (mirrors what
        # ``make_context`` does for the runner's local-state path).
        memo._bars = bars
        self._stats["views_built"] += 1
        return BarsView(bars=bars, memo=memo, fingerprint=fp, buffer=buf)

    def invalidate(self, symbol: str, interval: str | None = None) -> None:
        """Drop cached memo(s) for ``symbol`` (and optionally just one interval).

        ``interval=None`` drops every memo for the symbol across all
        intervals. ``interval="5m"`` drops only that pair. The next
        :meth:`get_view` rebuilds.

        Buffers in the underlying :class:`MultiIntervalCache` are NOT
        touched — the registry doesn't own them.
        """
        if interval is not None:
            key: _Key = (symbol, interval)
            self._memos.pop(key, None)
            self._fingerprints.pop(key, None)
            return
        # Drop every key that starts with this symbol.
        stale = [k for k in self._memos if k[0] == symbol]
        for k in stale:
            self._memos.pop(k, None)
            self._fingerprints.pop(k, None)

    def clear(self) -> None:
        """Drop every cached memo + fingerprint. Counters preserved.

        Buffers in the underlying cache are NOT touched.
        """
        self._memos.clear()
        self._fingerprints.clear()

    def stats(self) -> dict[str, int]:
        """Return a shallow copy of the diagnostic counters."""
        return dict(self._stats)

    # --------------------------------------------------------------- internals

    def _cache_candles(self, symbol: str, interval: str) -> Sequence[Candle] | None:
        """Pull the parallel candle list from the underlying cache.

        :class:`MultiIntervalCache` maintains a private
        ``_candles: Dict[(sym, iv), List[Candle]]`` parallel to its
        :class:`BarsBuffer` map specifically so consumers needing the
        original candle objects (for indicator fallback paths and for
        memo construction) can reuse them. The registry is one of
        those consumers; reaching across this attribute is the
        intended coupling between the two collaborators in this slice.
        """
        cache_candles = getattr(self._cache, "_candles", None)
        if cache_candles is None:
            return None
        return cache_candles.get((symbol, interval))


__all__ = ["BarsRegistry", "BarsView"]
