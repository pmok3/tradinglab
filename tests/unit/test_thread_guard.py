"""Tests for ``tradinglab.core.thread_guard``."""

from __future__ import annotations

import threading
from typing import List

import pytest

from tradinglab.core.thread_guard import (
    TkThreadViolation,
    require_tk_thread,
    tk_thread_check_disabled,
)


class _Holder:
    @require_tk_thread
    def m(self, x: int) -> int:
        return x * 2

    @require_tk_thread
    def raises(self) -> None:
        raise RuntimeError("inner")


def test_decorated_method_runs_on_main_thread():
    h = _Holder()
    assert h.m(3) == 6


def test_decorated_method_raises_off_thread():
    h = _Holder()
    err: List[Exception] = []

    def worker() -> None:
        try:
            h.m(1)
        except Exception as e:  # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert err and isinstance(err[0], TkThreadViolation)
    assert "must be called from the Tk main thread" in str(err[0])


def test_check_disabled_context_bypasses():
    h = _Holder()

    def worker() -> int:
        with tk_thread_check_disabled():
            return h.m(5)

    out: List[int] = []
    t = threading.Thread(target=lambda: out.append(worker()))
    t.start()
    t.join()
    assert out == [10]


def test_check_disabled_restores_check_after_exit():
    h = _Holder()
    with tk_thread_check_disabled():
        # Off-thread: should succeed inside the with-block.
        ok: List[bool] = []

        def worker() -> None:
            try:
                h.m(1)
                ok.append(True)
            except Exception:  # noqa: BLE001
                ok.append(False)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert ok == [True]
    # After the with-block: check is back on.
    err: List[Exception] = []

    def worker2() -> None:
        try:
            h.m(1)
        except Exception as e:  # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=worker2)
    t.start()
    t.join()
    assert err and isinstance(err[0], TkThreadViolation)


def test_decorated_method_preserves_inner_exception():
    h = _Holder()
    with pytest.raises(RuntimeError, match="inner"):
        h.raises()


def test_decorator_preserves_qualname_and_doc():
    @require_tk_thread
    def my_fn() -> int:
        """Docstring."""
        return 1

    assert my_fn.__name__ == "my_fn"
    assert my_fn.__doc__ == "Docstring."
