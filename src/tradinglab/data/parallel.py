"""Shared utilities for running I/O-bound fetch chunks in parallel.

Rationale (see spec.md §15.19): Python threading parallelizes I/O-bound
work well — the GIL is released during network syscalls — so splitting
one logical fetch into N independent sub-requests and joining the
results is a real speedup for any provider that exposes a date-range
or page API. CPU-bound work (Candle construction, session tagging)
does NOT benefit and should stay serialized after the merge.

This module is intentionally tiny: the only primitive is
:func:`fetch_chunks_parallel`, which takes a list of sub-task payloads
+ a worker function and returns the concatenation in input order.
Provider-specific logic (how to split, how to merge) lives in the
fetcher.

Example (Polygon-style, monthly chunks of 1-minute bars)::

    def fetch_polygon_1m(ticker, interval):
        months = _month_chunks_for_last_year()
        chunks = fetch_chunks_parallel(
            months,
            lambda month: _polygon_request(ticker, month),
        )
        return candles_from_json_rows(chunks, ...)

The yfinance source does **not** use this today because yfinance's
``period`` parameter already batches the whole range in one request —
splitting would produce more overhead than it saves.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def fetch_chunks_parallel(
    chunks: Iterable[T],
    worker: Callable[[T], list[R] | None],
    *,
    executor: Executor | None = None,
    max_workers: int = 4,
) -> list[R]:
    """Invoke ``worker`` on every ``chunk`` concurrently, concatenate in order.

    ``worker`` may return ``None`` (e.g. for an empty sub-range); those
    are treated as empty lists. Raised exceptions in a worker propagate
    — callers that want best-effort concatenation should wrap ``worker``
    themselves.

    If ``executor`` is provided we submit to it (useful for sharing the
    app's main fetch executor); otherwise a short-lived pool is created
    and shut down on return.
    """
    chunks_list = list(chunks)
    if not chunks_list:
        return []

    owned_pool = None
    if executor is None:
        owned_pool = ThreadPoolExecutor(
            max_workers=min(max_workers, len(chunks_list)),
            thread_name_prefix="fetch-chunk",
        )
        executor = owned_pool

    try:
        futures = [executor.submit(worker, c) for c in chunks_list]
        result: list[R] = []
        for fut in futures:
            part = fut.result()
            if part:
                result.extend(part)
        return result
    finally:
        if owned_pool is not None:
            owned_pool.shutdown(wait=True, cancel_futures=False)
