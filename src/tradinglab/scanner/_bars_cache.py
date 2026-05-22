"""Tiny LRU cache utility used by scanner field derivations.

Both the Heikin-Ashi field cluster and the key-bar field cluster need
the same caching primitive: memoize a pure derivation of a per-tick
:class:`BarsNp` snapshot, keyed by the snapshot's identity, with an LRU
bound and a lock for thread safety (scan workers are evaluated on a
``ThreadPoolExecutor``).

Two concrete subtleties drove the original ad-hoc OrderedDict caches:

1. **id() recycling.** A frozen dataclass holding NumPy arrays does not
   support ``__weakref__``, so we cannot use a ``WeakValueDictionary``.
   Keying purely on ``id(bars)`` is unsafe because once the previous
   :class:`BarsNp` is garbage-collected, Python may reuse its address
   for a brand-new snapshot — and that snapshot would receive the
   stale cached value. We defend by storing the ``bars`` object itself
   alongside the value and verifying ``hit_bars is bars`` on read.
2. **Per-tick liveness.** The cache is process-global rather than
   per-runner because each :class:`BarsNp` is rebuilt at bar-close and
   discarded shortly after; the LRU bound guarantees memory stays flat
   even under bursty multi-symbol scans.

The cache exposes a single :meth:`get_or_compute` method that takes a
caller-supplied *extra* key (e.g. ``len(b)``) so the same cache type
can serve both the HA cluster (``(id(b), len(b))``) and the key-bar
cluster (``id(b)``) with one implementation.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Hashable
from threading import Lock
from typing import Any, Generic, TypeVar

V = TypeVar("V")


class BarsKeyedCache(Generic[V]):
    """LRU cache keyed by BarsNp identity (+ optional extra key tuple).

    Parameters
    ----------
    max_size : int
        Maximum number of entries retained before LRU eviction kicks in.
    """

    __slots__ = ("_data", "_lock", "_max_size")

    def __init__(self, max_size: int = 64) -> None:
        self._data: OrderedDict[tuple[int, Hashable], tuple[Any, V]] = OrderedDict()
        self._lock = Lock()
        self._max_size = int(max_size)

    def get_or_compute(
        self,
        bars: Any,
        compute: Callable[[Any], V],
        *,
        extra_key: Hashable = (),
    ) -> V:
        """Return cached value for *bars*; compute and store on miss.

        ``compute`` is invoked **outside** the lock so concurrent misses
        on different bars don't serialise. We accept the small risk of a
        duplicate compute on the same bars under contention; the cache
        still converges to a single entry.
        """
        key = (id(bars), extra_key)
        with self._lock:
            hit = self._data.get(key)
            if hit is not None and hit[0] is bars:
                self._data.move_to_end(key)
                return hit[1]
        value = compute(bars)
        with self._lock:
            self._data[key] = (bars, value)
            self._data.move_to_end(key)
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)
        return value

    # ------------------------------------------------------------------
    # Introspection (for tests)
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
