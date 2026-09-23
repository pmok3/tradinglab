"""Keep `fetch_chunks_parallel` — the I/O-parallel fetch primitive.

No shipped runtime caller was found in the P1 module-cleanup review.
Keep the helper for compatibility: ``tradinglab.data`` re-exports it and
lists it in ``__all__``. The GUI filter pre-pass references it only in a
docstring and uses a separate executor; that is not a runtime call.

This file exercises the contract directly, with no network access:

1. Facade re-export identity (same object) + ``__all__`` listing.
2. Overlapping workers and input-order concatenation despite proven reverse completion.
3. ``None`` worker results are ignored (empty-range chunks).
4. Empty chunk list returns ``[]`` without calling the worker.
5. Any iterable (e.g. a generator) is accepted.
6. Worker exceptions propagate to the caller.
7. A caller-supplied executor is NOT shut down (shared ownership).
8. ``submit`` is called exactly once per chunk, in order (via mock).
9. Owned pools shut down on return: ``fetch-chunk-*`` threads are gone.
10. ``max_workers`` is clamped to the chunk count for owned pools.
11. A cancelled submitted future propagates ``CancelledError``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError, Executor, Future, ThreadPoolExecutor
from typing import Any
from unittest.mock import Mock, patch

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

    started = [threading.Event() for _ in range(3)]
    release = [threading.Event() for _ in range(3)]
    completed = [threading.Event() for _ in range(3)]
    completion_order: list[int] = []

    def worker(chunk: int) -> list[int]:
        started[chunk].set()
        assert release[chunk].wait(10), f"chunk {chunk} was not released"
        return [chunk, chunk + 10]

    with ThreadPoolExecutor(max_workers=3) as pool, ThreadPoolExecutor(max_workers=1) as caller:
        real_submit = pool.submit

        def submit(fn: Callable[[int], list[int]], chunk: int) -> Future[list[int]]:
            future = real_submit(fn, chunk)

            def on_done(_future: Future[list[int]]) -> None:
                completion_order.append(chunk)
                completed[chunk].set()

            future.add_done_callback(on_done)
            return future

        with patch.object(pool, "submit", side_effect=submit):
            result = caller.submit(fetch_chunks_parallel, [0, 1, 2], worker, executor=pool)
            try:
                for chunk, event in enumerate(started):
                    assert event.wait(5), f"chunk {chunk} did not start concurrently"
                assert not any(event.is_set() for event in completed)
                for chunk in (2, 1, 0):
                    release[chunk].set()
                    assert completed[chunk].wait(5), f"chunk {chunk} did not complete"
                assert completion_order == [2, 1, 0]
                assert result.result(timeout=5) == [0, 10, 1, 11, 2, 12]
            finally:
                for event in release:
                    event.set()


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


def test_cancelled_future_propagates_to_caller() -> None:
    future: Future[list[int]] = Future()
    assert future.cancel()
    executor = Mock(spec=Executor)
    executor.submit.return_value = future
    worker = Mock(return_value=[1])

    with pytest.raises(CancelledError):
        fetch_chunks_parallel([1], worker, executor=executor)

    executor.submit.assert_called_once_with(worker, 1)
    worker.assert_not_called()
    executor.shutdown.assert_not_called()


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
