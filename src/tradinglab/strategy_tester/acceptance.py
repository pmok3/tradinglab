"""Cancellation token for in-flight strategy-tester runs.

A run can span 100s of symbols × thousands of bars. The user must be
able to hit "Stop" and have the orchestrator yield without leaving
half-written per-symbol JSONs or zombie worker threads.

The token is a thin wrapper over :class:`threading.Event` so the GUI
thread can flip a single bit and worker threads observe it cheaply.
Workers MUST poll :meth:`AcceptanceToken.is_cancelled` between symbols
(per-symbol engine ticks are bounded and uninterruptible; no need to
poll inside :meth:`SandboxEngine.run_to_completion`).

The name "acceptance" mirrors the existing
``preload/service.py:cancel_event`` pattern but is reified as a class
so callers can pass it around without exposing the raw threading
primitive.
"""

from __future__ import annotations

import threading


class AcceptanceToken:
    """Cancellable contract between the GUI thread and the runner pool.

    Default state is "accepted" (i.e., not cancelled). Calling
    :meth:`cancel` flips the bit; once cancelled a token cannot be
    re-armed (matches the semantics of a Stop button — a new Run gets
    a fresh token).
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Mark this run as cancelled. Idempotent."""
        self._event.set()

    def is_cancelled(self) -> bool:
        """Return True iff :meth:`cancel` has been called."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise :class:`RunCancelled` if cancelled. Cheap guard for runner inner loops."""
        if self._event.is_set():
            raise RunCancelled()


class RunCancelled(RuntimeError):
    """Raised by :meth:`AcceptanceToken.raise_if_cancelled`.

    The runner catches this at the symbol boundary to short-circuit
    the per-symbol loop cleanly without leaking it to the caller —
    partial results from completed symbols are still returned with
    a ``RunStatus.CANCELLED`` manifest.
    """


__all__ = ("AcceptanceToken", "RunCancelled")
