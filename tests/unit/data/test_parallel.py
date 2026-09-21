"""Keep `fetch_chunks_parallel` — the I/O-parallel fetch primitive.

Verdict of the P1 module-cleanup review: ``src/tradinglab/data/parallel.py``
is USED and stays. Evidence pinned here:

1. ``tradinglab.data.__init__`` re-exports ``fetch_chunks_parallel`` and
   lists it in ``__all__`` (part of the public data facade).
2. ``gui/universe_prepare_dialog.py`` documents its filter pre-pass as
   mirroring ``data/parallel.fetch_chunks_parallel`` semantics — the
   module is the canonical statement of that pattern.
3. Doc index entries in ``src/tradinglab/data/__init__.spec.md``,
   ``docs/spec.md`` and ``docs/SPEC_INDEX.md``.

No dynamic imports touch the module (no ``importlib``/``__import__``
references anywhere), so static greps are sufficient evidence.

This file exercises the contract directly, with no network access:

1. Facade re-export identity (same object) + ``__all__`` listing.
2. Input-order concatenation even when chunks complete in reverse order.
3. ``None`` worker results are ignored (empty-range chunks).
4. Empty chunk list returns ``[]`` without calling the worker.
5. Any iterable (e.g. a generator) is accepted.
6. Worker exceptions propagate to the caller.
7. A caller-supplied executor is NOT shut down (shared ownership).
8. ``submit`` is called exactly once per chunk, in order (via mock).
9. Owned pools shut down on return: ``fetch-chunk-*`` threads are gone.
10. ``max_workers`` is clamped to the chunk count for owned pools.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import pytest

import tradinglab.data
from tradinglab.data import fetch_chunks_parallel
from tradinglab.data.parallel import fetch_chunks_parallel as parallel_impl


# 1. Facade re-export ---------------------------------------------------------
def test_facade_reexports_parallel_primitive() -> None:
    """``tradinglab.data.fetch_chunks_parallel`` is the same object as the
    module-level definition, and it is listed in ``__all__`` — downstream
    code may import it from the facade, so the module must not be
    removed or the facade must keep working."""
    assert tradinglab.data.fetch_chunks_parallel is parallel_impl
    assert "fetch_chunks_parallel" in tradinglab.data.__all__


# 2. Input-order concatenation ------------------------------------------------
def test_concatenates_in_input_order_despite_reversed_completion() -> None:
    """Output order follows the input chunk order, not the order workers
    finish in. Bars must stay date-sorted, so a reordered merge would
    force an expensive post-sort."""

    def worker(chunk: int) -> list[int]:
        # Later chunks finish first: chunk 0 sleeps longest.
        time.sleep(0.03 * (2 - chunk))
        return [chunk]

    result = fetch_chunks_parallel([0, 1, 2], worker)
    assert result == [0, 1, 2]


# 3. None handling ------------------------------------------------------------
def test_none_worker_results_are_ignored() -> None:
    """A worker returning ``None`` (e.g. an empty sub-range) contributes
    no elements — and never a literal ``None`` in the output list."""

    def worker(chunk: int) -> list[int] | None:
        return [chunk] if chunk % 2 else None

    assert fetch_chunks_parallel([0, 1, 2, 3], worker) == [1, 3]


# 4. Empty chunks -------------------------------------------------------------
def test_empty_chunks_returns_empty_without_calling_worker() -> None:
    """An empty chunk list short-circuits: the worker is never invoked and
    no executor is even created."""
    calls: list[Any] = []
    result = fetch_chunks_parallel([], lambda c: calls.append(c) or [c])
    assert result == []
    assert calls == []


# 5. Iterable input -----------------------------------------------------------
def test_accepts_any_iterable_input() -> None:
    """Callers may pass generators or other one-shot iterables; the
    function materializes them exactly once."""

    def chunks():
        yield "a"
        yield "b"
        yield "c"

    assert fetch_chunks_parallel(chunks(), lambda c: [c.upper()]) == ["A", "B", "C"]


# 6. Exception propagation ----------------------------------------------------
def test_worker_exception_propagates_to_caller() -> None:
    """Raised exceptions (e.g. a provider rate-limit) propagate — the
    primitive does not swallow them. Callers that want best-effort
    concatenation must wrap the worker themselves."""

    def worker(chunk: int) -> list[int]:
        if chunk == 1:
            raise ValueError("rate limited")
        return [chunk]

    with pytest.raises(ValueError, match="rate limited"):
        fetch_chunks_parallel([0, 1, 2], worker)


# 7. Shared executor ownership -------------------------------------------------
def test_shared_executor_is_not_shut_down() -> None:
    """When the caller passes its own executor (e.g. the app's main fetch
    executor), ownership stays with the caller — the primitive must not
    shut it down on return."""
    executor = ThreadPoolExecutor(max_workers=2)
    try:
        assert fetch_chunks_parallel([1, 2], lambda c: [c * 10], executor=executor) == [10, 20]
        # Still usable after return: submit must not raise RuntimeError.
        assert executor.submit(lambda: 42).result() == 42
    finally:
        executor.shutdown(wait=True)


def test_submit_called_once_per_chunk_in_order() -> None:
    """With a caller-supplied executor the primitive performs no scheduling
    policy of its own: exactly one ``submit(worker, chunk)`` per chunk,
    in input order."""

    class MockExecutor:
        def __init__(self) -> None:
            self.submits: list[tuple[Any, Any]] = []

        def submit(self, fn: Any, chunk: Any) -> Future[list[int]]:
            self.submits.append((fn, chunk))
            fut: Future[list[int]] = Future()
            fut.set_result([chunk])
            return fut

    mock_executor = MockExecutor()
    result = fetch_chunks_parallel([5, 6, 7], lambda c: [c], executor=mock_executor)

    assert result == [5, 6, 7]
    assert len(mock_executor.submits) == 3
    for (fn, chunk), expected in zip(mock_executor.submits, [5, 6, 7], strict=True):
        assert chunk == expected
        assert fn is not None  # the caller-provided worker


# 8. Owned-pool thread cleanup --------------------------------------------------
def test_owned_pool_threads_are_gone_after_return() -> None:
    """When no executor is passed, the owned pool is shut down on return —
    no ``fetch-chunk-*`` threads may linger."""
    assert fetch_chunks_parallel([1, 2, 3], lambda c: [c]) == [1, 2, 3]

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        alive = [
            t.name for t in threading.enumerate() if t.name.startswith("fetch-chunk")
        ]
        if not alive:
            break
        time.sleep(0.01)
    else:  # pragma: no cover - only reached on a real leak
        pytest.fail(f"owned pool threads still alive after return: {alive}")

    assert not [t for t in threading.enumerate() if t.name.startswith("fetch-chunk")]


# 9. max_workers clamping ------------------------------------------------------
def test_max_workers_clamped_to_chunk_count_for_owned_pool() -> None:
    """The owned pool is created with ``min(max_workers, len(chunks))`` —
    we don't spawn 10 threads for 2 chunks, and we don't over-clamp when
    chunks outnumber ``max_workers``."""
    real_pool = ThreadPoolExecutor
    captured: dict[str, Any] = {}

    class SpyPool(real_pool):  # type: ignore[valid-type,misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)
            super().__init__(*args, **kwargs)

    with patch("tradinglab.data.parallel.ThreadPoolExecutor", SpyPool):
        fetch_chunks_parallel([1, 2], lambda c: [c], max_workers=10)
    assert captured["max_workers"] == 2
    assert captured["thread_name_prefix"] == "fetch-chunk"

    captured.clear()
    with patch("tradinglab.data.parallel.ThreadPoolExecutor", SpyPool):
        fetch_chunks_parallel(list(range(12)), lambda c: [c], max_workers=10)
    assert captured["max_workers"] == 10
