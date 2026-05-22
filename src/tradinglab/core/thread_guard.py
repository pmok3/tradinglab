"""Tk-main-thread invariant decorator + bypass for tests.

Several state-owning subsystems (``PositionTracker``, ``ExitEvaluator``,
``AuditLog``, ``PaperBrokerEngine``) are designed to run **only on the Tk
main thread**. Mutating them from a stream-source / worker thread races
against the indicator memo, the Treeview, and the JSONL audit log.

Rather than peppering each public method with an ad-hoc check, we expose
:func:`require_tk_thread` as a decorator that enforces the invariant and
:func:`tk_thread_check_disabled` as a contextmanager for unit tests that
genuinely need to drive a method from a non-main thread.

Production code paths leave the check enabled. Pytest fixtures that need
to bypass should use the contextmanager rather than mutating the global
flag directly so the bypass is scoped.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])


_check_enabled: bool = True
_lock = threading.Lock()


class TkThreadViolation(RuntimeError):
    """Raised when a Tk-main-thread-only function is called off-thread."""


def require_tk_thread(fn: F) -> F:
    """Decorator: raise :class:`TkThreadViolation` unless on the Tk main thread.

    The check is suppressed inside :func:`tk_thread_check_disabled`. The
    function is otherwise transparent — same signature, same return,
    same wrapped name and docstring.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if _check_enabled and threading.current_thread() is not threading.main_thread():
            raise TkThreadViolation(
                f"{fn.__qualname__} must be called from the Tk main thread; "
                f"called from {threading.current_thread().name!r}"
            )
        return fn(*args, **kwargs)

    return cast(F, wrapper)


@contextmanager
def tk_thread_check_disabled() -> Iterator[None]:
    """Temporarily bypass the Tk-thread check (for unit tests only).

    Not re-entrant safe across threads; intended for single-threaded test
    fixtures that drive a method from a worker to assert behavior.
    """
    global _check_enabled
    with _lock:
        prior = _check_enabled
        _check_enabled = False
    try:
        yield
    finally:
        with _lock:
            _check_enabled = prior


__all__ = [
    "TkThreadViolation",
    "require_tk_thread",
    "tk_thread_check_disabled",
]
