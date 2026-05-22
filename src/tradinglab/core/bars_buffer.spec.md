# core/bars_buffer.py — Spec

## Purpose
Append-only mutable column store that emits `Bars` snapshots. Shifts the per-tick cost of rebuilding `Bars.from_candles` from O(n) extraction-every-tick to amortised O(1) per `append`.

## Public API
- `class BarsBuffer(initial_capacity: int = 16)`.
  - `from_candles(candles: Sequence[Candle]) -> BarsBuffer` — pre-populated buffer; capacity is rounded up to the next power of 2 ≥ `_INITIAL_CAPACITY`.
  - `append(candle: Candle) -> None` — push one bar; amortised O(1).
  - `update_last(candle: Candle) -> None` — overwrite the last row in place. Raises `IndexError` if the buffer is empty. Designed for the streaming "forming bar" path.
  - `extend(candles: Iterable[Candle]) -> None` — bulk append; pre-grows when `len(candles)` is known.
  - `clear() -> None` — reset length to 0; preserves allocated capacity.
  - `__len__()`, `capacity` (property).
  - `view(candles: Optional[Sequence[Candle]] = None) -> Bars` — return a frozen `Bars` whose arrays are NumPy *views* over the populated prefix. No copy. If `candles` is provided, `len(candles)` must equal `len(self)` — else `ValueError`.

## Dependencies
- Internal: `..models.Candle`, `.bars.{Bars, _to_naive_utc}`.
- External: `numpy`.

## Design Decisions
- **Capacity-doubling like `std::vector`**: `_ensure_capacity` doubles when full. `_grow` allocates a new array and copies the old prefix.
- **`view()` returns NumPy slice views**, not copies. **Lifetime contract**: a returned `Bars` aliases the buffer's own storage. A subsequent `append` may trigger a re-alloc that leaves a previously obtained `Bars` looking at the *old* storage (still valid, but no longer reflecting subsequent writes). `update_last` mutates a slot visible through any outstanding view. **Rule: use the returned `Bars` within the current tick; don't stash in a long-lived cache.**
- **Single-writer / multi-reader within one tick**: scanner runner mutates on the main thread before submitting work, then worker threads only *read* through their captured `Bars` view. No internal locks.
- **`__slots__`**: avoids per-instance dict overhead; scanner can hold hundreds of these for a 200-symbol watchlist.
- **`update_last` mutates in place** rather than reallocating; this is what makes the forming-bar streaming path cheap.
- **`from_candles` rounds initial capacity up to a power of 2** so subsequent appends amortise normally (avoids a doubling on the very first append).

## Invariants
- `len(self) <= self.capacity` at all times.
- After `append`, `view(...).timestamps[-1]` reflects the most recent bar.
- After `update_last(c)`, `view(...).close[-1] == c.close` (and similarly for other fields).
- `clear()` does not reallocate; subsequent `append` reuses the prior capacity.
- `view(candles)` raises `ValueError` if `len(candles) != len(self)`.

## Testing
- Covered indirectly via integration smoke tests (scanner runner, streaming sources).

