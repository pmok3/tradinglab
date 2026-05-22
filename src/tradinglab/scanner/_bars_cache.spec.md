# BarsKeyedCache

Tiny LRU cache shared by the Heikin-Ashi and key-bar field clusters in
`scanner/fields.py`. Replaces the two ad-hoc `OrderedDict + Lock` blocks
that grew up independently.

## Design

- Keyed by `(id(bars), extra_key)`; an LRU bound (default 64) caps memory.
- Stores the `bars` object alongside the value as a strong reference so a
  recycled `id()` after GC cannot return a stale entry (verified by
  `test_bars_keyed_cache_id_recycle_guard`).
- `compute` is invoked **outside** the lock so a contended miss on snapshot
  *A* does not block a concurrent miss on snapshot *B*. Duplicate compute
  on the same bars under contention is acceptable and self-corrects.
- `extra_key` lets a single cache instance distinguish multiple
  derivations of the same `BarsNp` (the HA cache uses `len(b)` as a
  tiebreaker; KB cache leaves it `()`).

## Public API

```python
class BarsKeyedCache(Generic[V]):
    def __init__(self, max_size: int = 64) -> None: ...
    def get_or_compute(
        self, bars: Any, compute: Callable[[Any], V], *,
        extra_key: Hashable = (),
    ) -> V: ...
    def __len__(self) -> int: ...
    def clear(self) -> None: ...
```

## Validation

`tests/scanner/test_key_bar_np.py`:
- `test_bars_keyed_cache_memoizes`
- `test_bars_keyed_cache_extra_key_disambiguates`
- `test_bars_keyed_cache_lru_eviction`
- `test_bars_keyed_cache_id_recycle_guard`
