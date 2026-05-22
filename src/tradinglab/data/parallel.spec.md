# data/parallel.py — Spec

## Purpose
Shared primitive for running I/O-bound fetch chunks concurrently. Rationale: the GIL is released during network syscalls, so splitting one logical fetch into N independent sub-requests and joining the results is a real speedup for any provider with a date-range or page API. CPU-bound work (Candle construction, session tagging) does **not** benefit and should stay serialized after the merge.

## Public API
- `fetch_chunks_parallel(chunks, worker, *, executor=None, max_workers=4) -> List[R]` — submits `worker(chunk)` for each chunk, concatenates results in **input order**. `worker` may return `None` (treated as empty); raised exceptions propagate (callers that want best-effort wrap the worker themselves). If `executor` is omitted, a short-lived `ThreadPoolExecutor` is created and shut down on return.

## Dependencies
- Internal: none.
- External: `concurrent.futures`.

## Design Decisions
- **Threading, not multiprocessing**: I/O-bound workload, GIL is released. Multiprocessing would add pickle/IPC overhead that dominates for small JSON responses.
- **Input-order concatenation**, not completion-order. Bars must stay sorted by date; reordering a chunk would force a post-sort pass over the full result (wasteful when chunks themselves are monotonically ordered).
- **Optional shared executor**: callers can pass `ChartApp._fetch_executor` to avoid per-fetch thread churn; if omitted, the function owns its pool and shuts it down cleanly. `cancel_futures=False` on shutdown because we've already awaited all futures.
- **`max_workers = min(max_workers, len(chunks))`** so we don't spawn 4 threads for 2 chunks.
- **Exceptions propagate** (don't swallow) — a fetcher like Polygon can report rate limits that the caller needs to know about. Best-effort wrapping is deferred to the caller's lambda.

## Invariants
- Output order matches input chunk order (not completion order).
- If `executor` was not provided, the owned pool is always shut down on return (even on exception via `finally`).
- `None` from a worker contributes no elements (not a `None` in the list).

## Testing
- Not exercised by the current smoke suite (yfinance single-request path doesn't need chunking, and synthetic is in-process). The primitive is kept ready for Polygon-style providers.

## Known limitations
- **Reserved for future Polygon integration** — module is documented but not used by any shipped data source. Will be exercised once a multi-symbol-per-request source is added.
- No per-chunk retry/backoff policy. If/when a flaky provider is integrated, wrap `worker` with retry logic at the call site rather than adding parameters here.

