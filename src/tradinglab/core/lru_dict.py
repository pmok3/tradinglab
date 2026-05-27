"""Bounded-capacity ``OrderedDict`` subclass with LRU eviction semantics.

Used by call sites that need a process-lifetime memo where the key
space grows unbounded over a long-running session — e.g. the
strategy-tester warmup-bar cache (``strategy_tester/warmup.py``) and
the per-ticker EventBundle cache (``app.py:_events_cache``).

The OrderedDict ABI is preserved so existing call sites that read
via ``cache.get(k)`` / ``cache[k] = v`` / ``cache.clear()`` /
``k in cache`` / ``cache.pop(k, None)`` keep working without
modification — the only behavioural change is that ``__setitem__``
evicts the oldest entry once ``len(cache) > maxsize`` and ``get``
moves the touched key to the most-recently-used end.

Not thread-safe by itself. Callers that share an instance across
threads must guard with their own lock (the existing call sites are
all single-threaded Tk-thread or single-worker contexts).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TypeVar

K = TypeVar("K")
V = TypeVar("V")


class LRUDict(OrderedDict[K, V]):
    """``OrderedDict`` with a hard cap + LRU eviction on insert.

    Args:
        maxsize: Maximum number of entries. Must be > 0. Inserts
            beyond this evict the least-recently-used entry first.

    Behaviour:
        * ``__setitem__(k, v)`` — inserts + moves k to the MRU end;
          evicts the LRU end while ``len(self) > maxsize``.
        * ``get(k, default=None)`` — refreshes k's recency on hit;
          returns default on miss without touching the recency order
          (no phantom inserts).
        * Every other ``OrderedDict`` method (``clear``, ``pop``,
          ``__contains__``, ``__delitem__``, iteration, etc.) is
          inherited unchanged.

    Why this lives in ``core`` (not in the call sites): two
    independent caches need the same pattern, and any future
    per-process memo will hit the same "unbounded growth over a
    multi-day session" footgun. One source of truth keeps the
    eviction policy uniform and the corner cases tested.
    """

    def __init__(self, maxsize: int):
        super().__init__()
        if maxsize <= 0:
            raise ValueError(f"maxsize must be positive; got {maxsize!r}")
        self._maxsize = int(maxsize)

    @property
    def maxsize(self) -> int:
        """Capacity ceiling. Inserts beyond this evict the LRU entry."""
        return self._maxsize

    def __setitem__(self, key: K, value: V) -> None:
        super().__setitem__(key, value)
        # ``move_to_end`` is a no-op when the key is already at the
        # tail (e.g. fresh insert); for updates of an existing key it
        # refreshes recency. Cheap either way.
        self.move_to_end(key)
        while len(self) > self._maxsize:
            self.popitem(last=False)

    def get(self, key, default=None):  # type: ignore[override]
        # ``in`` check first so a miss doesn't trigger ``__getitem__``
        # (which would raise KeyError for the OrderedDict subclass and
        # also wouldn't refresh anything). Cheap because OrderedDict's
        # ``__contains__`` is O(1) hash-table lookup.
        if key in self:
            try:
                self.move_to_end(key)
            except KeyError:
                pass
            return super().__getitem__(key)
        return default
