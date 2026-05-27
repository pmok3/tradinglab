# lru_dict.py — Spec

## Purpose
Provide a single bounded-capacity LRU dictionary primitive so
unbounded process-lifetime caches don't drift back into the codebase.
Used by `strategy_tester/warmup.py` (warmup-bar cache) and
`app.py:_events_cache` (per-ticker EventBundle cache) — both
previously plain `dict[...]` instances that grew without eviction
over a long-running session.

## Public API
- `LRUDict(maxsize: int)` — generic `OrderedDict[K, V]` subclass.
  - `__setitem__` — insert + LRU-touch; evicts oldest while
    `len(self) > maxsize`.
  - `get(key, default=None)` — LRU-touch on hit; no side effects on
    miss.
  - `maxsize` property — read-only capacity.
  - All other `OrderedDict` methods (`clear`, `pop`, `__contains__`,
    `__delitem__`, `__iter__`, `keys`, `values`, `items`, etc.)
    inherited unchanged.

## Dependencies
- Internal: none.
- External: `collections.OrderedDict` (stdlib).

## Design Decisions
- **Subclass `OrderedDict`** rather than wrap, so existing call
  sites that read/write/iterate via the dict ABI need ZERO changes
  when their plain `dict` is replaced with an `LRUDict`.
- **No touch on miss.** `.get(k)` on a missing key does not insert a
  None placeholder — that would silently inflate cache size.
- **Touch on hit only** — write paths (`__setitem__`) and reads
  (`.get`) both update recency. Membership checks (`k in cache`) do
  NOT update recency, matching `OrderedDict` semantics; callers that
  want a touch-on-membership pattern should switch to `.get`.
- **Not thread-safe.** Existing call sites are single-threaded
  (Tk thread for `_events_cache`; module-level + worker pool for the
  warmup cache, but worker writes go through a Tk-thread bounce). A
  future multi-threaded caller must layer its own lock.
- **Generic-typed.** `LRUDict[K, V]` parameterises the key + value
  types so call sites get the same mypy / pylance experience as
  `dict[K, V]`.

## Invariants
- `len(self) <= maxsize` after every public mutation.
- The LRU end (`next(iter(self))`) is the next eviction victim.
- The MRU end is the most recently inserted-or-read key.
- `clear()` resets the dict but preserves `maxsize`.

## Testing
- `tests/core/test_lru_dict.py` — covers: capacity enforcement,
  eviction order (FIFO of LRU keys), `.get` LRU-touches, miss
  doesn't insert phantom, `__setitem__` of an existing key
  refreshes recency, `clear()` empties without resetting maxsize,
  `maxsize <= 0` raises.
