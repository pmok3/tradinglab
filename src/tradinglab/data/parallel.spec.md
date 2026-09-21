# data/parallel.py — Spec

Last updated: 2026-09-20

## Purpose
Shared primitive for running I/O-bound fetch chunks concurrently. Rationale: the GIL is released during network syscalls, so splitting one logical fetch into N independent sub-requests and joining the results is a real speedup for any provider with a date-range or page API. CPU-bound work (Candle construction, session tagging) does **not** benefit and should stay serialized after the merge.

## Public API
- `fetch_chunks_parallel(chunks, worker, *, executor=None, max_workers=4) -> List[R]` — submits `worker(chunk)` for each chunk, concatenates results in **input order**. `worker` may return `None` (treated as empty); raised exceptions propagate (callers that want best-effort wrap the worker themselves). If `executor` is omitted, a short-lived `ThreadPoolExecutor` is created and shut down on return.

## Who uses it
Evidence that this module is **in use** and must not be deleted (P1 module-cleanup review, 2026-09-20):
- **Public facade**: `src/tradinglab/data/__init__.py` re-exports `fetch_chunks_parallel` and lists it in `__all__` — it is part of the public `tradinglab.data` API surface that downstream code may import.
- **GUI docstring reference**: `src/tradinglab/gui/universe_prepare_dialog.py` (`_run_filter_prepass`) documents its filter pre-pass as mirroring ``data/parallel.fetch_chunks_parallel`` semantics — this module is the canonical statement of that pattern.
- **Spec/docs references**: `src/tradinglab/data/__init__.spec.md`, `docs/spec.md` (§15.19 and the module listing), and `docs/SPEC_INDEX.md` all describe and index it.
- **No dynamic imports touch it** (repo-wide grep finds no `importlib`/`__import__` references), so the static references above are the complete usage picture. No shipped data source *calls* it today (see Known limitations) — the yfinance path is a single request, Polygon/Alpaca ship as single-request adapters.

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
- `tests/unit/data/test_parallel.py` (10 tests, no network): facade re-export identity + `__all__`; input-order concatenation under reversed completion order; `None`-worker handling; empty chunks (worker not called); generator input; exception propagation; shared-executor ownership (not shut down after return; exactly one `submit` per chunk, in order); owned-pool thread cleanup (no `fetch-chunk-*` threads linger); `max_workers` clamped to `min(max_workers, len(chunks))`.
- Not exercised by the current smoke suite (yfinance single-request path doesn't need chunking, and synthetic is in-process). The primitive is kept ready for Polygon-style providers.

## Known limitations
- **Currently unused by shipped data sources** — Polygon and Alpaca now ship as single-request provider adapters and do not use this primitive. It remains ready for a future multi-symbol-per-request or date-chunked source.
- No per-chunk retry/backoff policy. If/when a flaky provider is integrated, wrap `worker` with retry logic at the call site rather than adding parameters here.

