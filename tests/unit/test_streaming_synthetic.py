"""Unit tests for :mod:`tradinglab.streaming.synthetic`.

Batch 14 of the test-coverage audit. Validates:

* Non-intraday subscriptions return a no-op unsubscribe and never spawn a
  thread (no events emitted).
* The very first emission for an intraday subscription is a ``"rollover"``
  event (so the consumer has an in-progress bar to mutate).
* ``unsubscribe()`` reliably stops the daemon thread within one
  ``tick_period`` — no further events arrive after that point.

Coordination uses a tiny ``tick_period=0.05`` and a :class:`threading.Event`
so the tests don't depend on wall-clock-sized sleeps. Subscriptions are
always torn down in ``finally`` so a stray daemon thread can't leak across
tests.
"""

from __future__ import annotations

import gc
import threading
import time

import pytest

from tradinglab.streaming.synthetic import SyntheticStreamSource


@pytest.fixture(autouse=True)
def _no_cyclic_gc_on_daemon_threads():
    """Disable the cyclic garbage collector for each streaming test.

    These tests spawn daemon tick threads (``SyntheticStreamSource``)
    that allocate in their ``on_event`` callbacks. If Python's cyclic
    collector fires *on one of those daemon threads* it can reclaim a
    leftover Tk object from an earlier GUI test and run its Tcl
    finalizer (``Variable`` / ``Image`` / ``font.Font``) off the main
    thread → ``Tcl_AsyncDelete`` → ``Windows fatal exception: code
    0x80000003`` (SIGABRT) on the Windows CI runners (see CLAUDE.md
    §7.5). The top-level ``conftest`` neuters those finalizers; this
    fixture is belt-and-suspenders — with the cyclic collector off,
    the daemon threads never run it at all, so leftover Tk objects
    stay alive until the main thread (or interpreter teardown)
    reclaims them safely. Reference-counted deallocation is
    unaffected (the daemon threads hold no Tk references).
    """
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


def test_non_intraday_returns_noop_unsubscribe() -> None:
    """Daily+ intervals must not stream — no thread, no events."""
    recorder: list[tuple[str, object]] = []
    src = SyntheticStreamSource(tick_period=0.05)

    unsubscribe = src.subscribe("AAPL", "1d", on_event=lambda kind, candle: recorder.append((kind, candle)))
    try:
        # Give any (incorrectly-spawned) thread a chance to emit.
        time.sleep(0.1)
        assert recorder == [], (
            f"non-intraday subscription should be silent, got: {recorder!r}"
        )
    finally:
        unsubscribe()

    # The unsubscribe is a no-op lambda; calling it again must be safe.
    unsubscribe()
    assert recorder == []


def test_first_emission_is_rollover_not_tick() -> None:
    """The first event of an intraday subscription must be ``rollover``."""
    recorder: list[tuple[str, object]] = []
    first_event = threading.Event()

    def on_event(kind: str, candle: object) -> None:
        recorder.append((kind, candle))
        first_event.set()

    src = SyntheticStreamSource(tick_period=0.05)
    unsubscribe = src.subscribe("AAPL", "1m", on_event=on_event)
    try:
        # The initial rollover is emitted synchronously inside _run before
        # the first stop.wait(); it should arrive within milliseconds.
        assert first_event.wait(timeout=1.0), "no event emitted within 1s"
        assert recorder, "recorder unexpectedly empty after event fired"
        kind, _candle = recorder[0]
        assert kind == "rollover", f"first emission must be rollover, got {kind!r}"
    finally:
        unsubscribe()


def test_unsubscribe_exits_thread_within_one_tick() -> None:
    """After unsubscribe(), no further events should arrive."""
    recorder: list[tuple[str, object]] = []
    lock = threading.Lock()

    def on_event(kind: str, candle: object) -> None:
        with lock:
            recorder.append((kind, candle))

    tick_period = 0.05
    src = SyntheticStreamSource(tick_period=tick_period)
    unsubscribe = src.subscribe("MSFT", "1m", on_event=on_event)
    try:
        # Let several ticks accumulate (200 ms / 50 ms ≈ 4 ticks plus the
        # initial rollover).
        time.sleep(0.2)
        with lock:
            assert len(recorder) >= 2, (
                f"expected multiple events after 200ms, got {len(recorder)}"
            )
    finally:
        unsubscribe()

    # Snapshot AFTER unsubscribe. The thread may still be mid-tick; give
    # it well over one tick_period to wind down, then confirm no further
    # events arrive.
    time.sleep(2 * tick_period)
    with lock:
        snapshot = len(recorder)
    # A second observation window — if the thread were still running, more
    # ticks would land here.
    time.sleep(2 * tick_period)
    with lock:
        final = len(recorder)
    assert final == snapshot, (
        f"events kept arriving after unsubscribe: {snapshot} -> {final}"
    )
