# data/prefetch/priority.py — Spec

## Purpose
The ordering value types the scheduler heaps on: `PriorityKey` (total order) and
`FetchJob` (immutable work unit).

## Public API
- `FOREGROUND_BAND = -1` — sentinel band for user-blocking fetches.
- `@dataclass(frozen=True, order=True) PriorityKey(band_index, tier_rank,
  interval_rank, seq)`.
- `@dataclass(frozen=True) FetchJob(source, symbol, interval, band_index,
  tier_rank, interval_rank, generation, seq=0)` with:
  - `dedup_key -> (source, symbol, interval, band_index)`
  - `series_key -> (source, symbol, interval)` (band-independent)
  - `is_foreground -> bool` (band ≤ FOREGROUND_BAND)
  - `priority() -> PriorityKey`
  - `with_seq(seq) -> FetchJob`

## Contract
- **Field order is precedence** (Decision: pure band-major): `band_index`
  dominates `tier_rank` dominates `interval_rank` dominates `seq`. So band-0 of
  the lowest tier beats band-1 of the highest tier (breadth-first).
- A negative `band_index` (foreground) sorts before every background band.
- `PriorityKey` is orderable/heapable directly; `FetchJob` is ordered via
  `.priority()` (push `(key, tiebreak, job)` tuples so equal keys never compare
  jobs).
- Both are frozen (immutable); `with_seq` returns a copy.
- `dedup_key` includes the band (queue dedup + promotion); `series_key` omits it
  (attach-to-in-flight + planner keying).

## Design Decisions
- `seq` lives IN `PriorityKey` (not a separate heap tiebreak) so keys are unique
  under a monotonic enqueue counter and heapq never needs to compare `FetchJob`s.

## Testing
`tests/unit/data/prefetch/test_priority.py` — band-major precedence, tier /
interval / seq tiebreaks, foreground-first, heap-pop order, frozen, dedup vs
series keys, `with_seq`.
