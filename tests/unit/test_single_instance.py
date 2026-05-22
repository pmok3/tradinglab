"""Unit tests for :mod:`tradinglab._single_instance`.

The Win32 ctypes layer is abstracted behind a ``_HANDLERS`` dict so
these tests can exercise the mutex / focus logic on any host.
"""
from __future__ import annotations

import sys

import pytest

from tradinglab import _single_instance as si


@pytest.fixture(autouse=True)
def _reset_handlers():
    """Clear any injected handlers between tests so leakage cannot
    cause a test to inadvertently see another's fakes."""
    saved = dict(si._HANDLERS)
    si._HANDLERS["create_mutex"] = None
    si._HANDLERS["close_handle"] = None
    si._HANDLERS["find_window"] = None
    si._HANDLERS["focus_window"] = None
    si._HANDLERS["acquire_posix_lock"] = None
    si._HANDLERS["release_posix_lock"] = None
    yield
    for k, v in saved.items():
        si._HANDLERS[k] = v


class TestConstants:
    def test_mutex_name_uses_local_prefix(self):
        assert si.MUTEX_NAME.startswith("Local\\")
        assert "TradingLab" in si.MUTEX_NAME

    def test_window_title_prefix_matches_app(self):
        assert si.WINDOW_TITLE_PREFIX == "TradingLab"


class TestAcquireOnNonWindows:
    def test_non_windows_with_no_fcntl_degrades_cleanly(
        self, monkeypatch,
    ):
        """When ``sys.platform != 'win32'`` and no test handler is
        registered, the real ``_real_acquire_posix_lock`` is invoked.
        Running these tests on a Windows host means ``fcntl`` is
        unavailable → the real impl returns ``("unsupported", None)``
        → guard degrades to ``(True, None)``.

        On a Linux/macOS host the same test still passes because
        the lockfile in ``app_data_dir()`` is free (no other
        instance running under pytest).
        """
        monkeypatch.setattr(sys, "platform", "linux")
        acquired, handle = si.acquire_single_instance()
        assert acquired is True
        # ``handle`` is either None (fcntl unavailable, fallback)
        # or a real file object (Linux host, lock actually
        # acquired). Either way it must NOT be False / 0.
        assert handle is None or hasattr(handle, "fileno"), (
            "POSIX acquire must return either None or a file-like "
            f"handle, got {handle!r}")
        # Clean up if we actually acquired the lock.
        if handle is not None:
            si.release_single_instance(handle)


class TestAcquireWithFakeHandlers:
    def test_first_instance_returns_handle(self):
        si._HANDLERS["create_mutex"] = lambda name: (42, 0)
        acquired, handle = si.acquire_single_instance()
        assert acquired is True
        assert handle == 42

    def test_second_instance_returns_false_and_closes(self):
        closed = []
        si._HANDLERS["create_mutex"] = lambda name: (
            42, si.ERROR_ALREADY_EXISTS)
        si._HANDLERS["close_handle"] = lambda h: closed.append(h)
        acquired, handle = si.acquire_single_instance()
        assert acquired is False
        assert handle is None
        assert closed == [42]

    def test_null_handle_degrades_to_no_protection(self):
        si._HANDLERS["create_mutex"] = lambda name: (None, 5)
        acquired, handle = si.acquire_single_instance()
        assert acquired is True
        assert handle is None

    def test_handler_exception_degrades_gracefully(self):
        def _boom(_name):
            raise RuntimeError("simulated DLL failure")
        si._HANDLERS["create_mutex"] = _boom
        acquired, handle = si.acquire_single_instance()
        assert acquired is True
        assert handle is None

    def test_close_handle_exception_swallowed(self):
        """Even if close_handle raises, the ERROR_ALREADY_EXISTS
        branch still returns (False, None) cleanly."""
        si._HANDLERS["create_mutex"] = lambda name: (
            42, si.ERROR_ALREADY_EXISTS)

        def _boom(_h):
            raise RuntimeError("close failure")

        si._HANDLERS["close_handle"] = _boom
        acquired, handle = si.acquire_single_instance()
        assert acquired is False
        assert handle is None


class TestReleaseSingleInstance:
    def test_release_none_is_noop(self):
        si.release_single_instance(None)  # no raise

    def test_release_calls_close_handle(self):
        closed = []
        si._HANDLERS["close_handle"] = lambda h: closed.append(h)
        si.release_single_instance(42)
        assert closed == [42]

    def test_release_swallows_exceptions(self):
        def _boom(_h):
            raise RuntimeError("close failure")
        si._HANDLERS["close_handle"] = _boom
        si.release_single_instance(42)  # no raise

    def test_release_noop_when_handler_absent(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        si.release_single_instance(42)  # no raise


class TestFocusExistingWindow:
    def test_no_handlers_returns_false(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert si.focus_existing_window() is False

    def test_no_window_found(self):
        si._HANDLERS["find_window"] = lambda prefix: 0
        si._HANDLERS["focus_window"] = lambda hwnd: True
        assert si.focus_existing_window() is False

    def test_window_found_and_focused(self):
        focused = []
        si._HANDLERS["find_window"] = lambda prefix: 1234
        si._HANDLERS["focus_window"] = lambda hwnd: focused.append(hwnd) or True
        assert si.focus_existing_window() is True
        assert focused == [1234]

    def test_focus_failure_returns_false(self):
        si._HANDLERS["find_window"] = lambda prefix: 1234
        si._HANDLERS["focus_window"] = lambda hwnd: False
        assert si.focus_existing_window() is False

    def test_find_exception_returns_false(self):
        def _boom(_prefix):
            raise RuntimeError("enum failed")
        si._HANDLERS["find_window"] = _boom
        si._HANDLERS["focus_window"] = lambda hwnd: True
        assert si.focus_existing_window() is False

    def test_focus_exception_returns_false(self):
        def _boom(_hwnd):
            raise RuntimeError("set foreground failed")
        si._HANDLERS["find_window"] = lambda prefix: 1234
        si._HANDLERS["focus_window"] = _boom
        assert si.focus_existing_window() is False

    def test_custom_prefix_threads_through(self):
        seen = []
        si._HANDLERS["find_window"] = lambda prefix: seen.append(prefix) or 0
        si._HANDLERS["focus_window"] = lambda hwnd: True
        si.focus_existing_window("MyCustomPrefix")
        assert seen == ["MyCustomPrefix"]


class TestSingleInstanceGuard:
    def test_first_instance_proceeds(self):
        si._HANDLERS["create_mutex"] = lambda name: (99, 0)
        proceed, handle = si.single_instance_guard()
        assert proceed is True
        assert handle == 99

    def test_second_instance_focuses_and_blocks(self):
        focused = []
        si._HANDLERS["create_mutex"] = lambda name: (
            99, si.ERROR_ALREADY_EXISTS)
        si._HANDLERS["close_handle"] = lambda h: None
        si._HANDLERS["find_window"] = lambda prefix: 5555
        si._HANDLERS["focus_window"] = lambda hwnd: focused.append(hwnd) or True
        proceed, handle = si.single_instance_guard()
        assert proceed is False
        assert handle is None
        assert focused == [5555]

    def test_second_instance_blocks_even_without_window(self):
        """If the existing window isn't found (race condition: it just
        closed), the second instance still exits — it does NOT fall
        back to launching a duplicate."""
        si._HANDLERS["create_mutex"] = lambda name: (
            99, si.ERROR_ALREADY_EXISTS)
        si._HANDLERS["close_handle"] = lambda h: None
        si._HANDLERS["find_window"] = lambda prefix: 0
        si._HANDLERS["focus_window"] = lambda hwnd: True
        proceed, handle = si.single_instance_guard()
        assert proceed is False
        assert handle is None

    def test_non_windows_proceeds_when_fcntl_unavailable(
        self, monkeypatch, capsys,
    ):
        """On non-Windows hosts where ``fcntl`` is unavailable (e.g.
        Windows under WSL with a stripped Python build, or these
        tests running on a real Windows host with the platform
        spoofed), the POSIX path degrades to ``(True, None)`` and
        the user launches normally. No "already running" message
        on stderr in that branch."""
        monkeypatch.setattr(sys, "platform", "darwin")
        proceed, handle = si.single_instance_guard()
        assert proceed is True
        # See TestAcquireOnNonWindows above for the
        # handle-may-be-real-file caveat on Linux hosts.
        if handle is not None:
            si.release_single_instance(handle)


# ===================================================================
# POSIX backend — new in C5 (2026-05). Tests inject fake handlers
# via ``_HANDLERS["acquire_posix_lock"]`` /
# ``_HANDLERS["release_posix_lock"]`` so the platform-specific
# semantics can be exercised on any host. The real fcntl path is
# covered by ``TestPosixFcntlRoundTrip`` below, gated by
# ``pytest.mark.skipif`` so it only runs on hosts where ``fcntl``
# is actually importable.
# ===================================================================


class _FakeFileHandle:
    """Minimal file-like for testing POSIX dispatch.

    Has ``fileno()`` and ``close()`` so it satisfies
    ``release_single_instance``'s duck-type sniff for
    "file-like → POSIX path".
    """
    def __init__(self, fd: int = 7):
        self._fd = fd
        self.closed = False

    def fileno(self) -> int:
        return self._fd

    def close(self) -> None:
        self.closed = True


class TestPosixAcquireWithFakeHandlers:
    """Test the POSIX path while running on a Windows host by
    injecting handlers and spoofing ``sys.platform``."""

    def test_acquired_returns_handle(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        fp = _FakeFileHandle(fd=11)
        si._HANDLERS["acquire_posix_lock"] = (
            lambda path: ("acquired", fp))
        acquired, handle = si.acquire_single_instance()
        assert acquired is True
        assert handle is fp

    def test_held_returns_false(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        si._HANDLERS["acquire_posix_lock"] = (
            lambda path: ("held", None))
        acquired, handle = si.acquire_single_instance()
        assert acquired is False
        assert handle is None

    def test_unsupported_returns_true_none(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        si._HANDLERS["acquire_posix_lock"] = (
            lambda path: ("unsupported", None))
        acquired, handle = si.acquire_single_instance()
        assert acquired is True
        assert handle is None

    def test_handler_exception_degrades_to_true_none(
        self, monkeypatch,
    ):
        monkeypatch.setattr(sys, "platform", "linux")

        def _boom(_path):
            raise RuntimeError("simulated fcntl failure")

        si._HANDLERS["acquire_posix_lock"] = _boom
        acquired, handle = si.acquire_single_instance()
        assert acquired is True
        assert handle is None

    def test_bad_return_shape_degrades_to_true_none(
        self, monkeypatch,
    ):
        monkeypatch.setattr(sys, "platform", "linux")
        # Returning a bare None (not a tuple) — handler contract
        # violation. Must not crash the launch.
        si._HANDLERS["acquire_posix_lock"] = lambda path: None
        acquired, handle = si.acquire_single_instance()
        assert acquired is True
        assert handle is None

    def test_unknown_status_treated_as_unsupported(
        self, monkeypatch,
    ):
        monkeypatch.setattr(sys, "platform", "linux")
        si._HANDLERS["acquire_posix_lock"] = (
            lambda path: ("???", None))
        acquired, handle = si.acquire_single_instance()
        assert acquired is True
        assert handle is None

    def test_custom_posix_lock_path_threads_through(
        self, monkeypatch,
    ):
        monkeypatch.setattr(sys, "platform", "linux")
        seen = []

        def _spy(path):
            seen.append(path)
            return ("acquired", _FakeFileHandle())

        si._HANDLERS["acquire_posix_lock"] = _spy
        si.acquire_single_instance(posix_lock_path="/tmp/custom.lock")
        assert seen == ["/tmp/custom.lock"]

    def test_default_path_under_app_data_dir(self, monkeypatch):
        """When no path is passed, the helper must resolve to
        ``app_data_dir() / POSIX_LOCK_FILENAME``."""
        monkeypatch.setattr(sys, "platform", "linux")
        seen = []
        si._HANDLERS["acquire_posix_lock"] = (
            lambda path: seen.append(path) or (
                "acquired", _FakeFileHandle()))
        si.acquire_single_instance()
        assert len(seen) == 1
        assert seen[0].endswith(si.POSIX_LOCK_FILENAME)

    def test_windows_handler_takes_precedence_over_platform(
        self, monkeypatch,
    ):
        """If a test injects ``create_mutex``, the Windows path
        must run even when ``sys.platform`` says otherwise. This
        keeps the existing Win32 test suite working on a Linux CI
        host."""
        monkeypatch.setattr(sys, "platform", "linux")
        # POSIX handler also injected to prove it's NOT called.
        posix_called = []
        si._HANDLERS["acquire_posix_lock"] = (
            lambda path: posix_called.append(path)
            or ("acquired", _FakeFileHandle()))
        win_called = []
        si._HANDLERS["create_mutex"] = (
            lambda name: win_called.append(name) or (99, 0))
        acquired, handle = si.acquire_single_instance()
        assert acquired is True
        assert handle == 99
        assert win_called == [si.MUTEX_NAME]
        assert posix_called == []


class TestReleasePosixDispatch:
    """``release_single_instance`` must dispatch by handle shape:
    file-like → POSIX release; int → Windows close."""

    def test_file_like_routes_to_release_posix_lock(self):
        released = []
        si._HANDLERS["release_posix_lock"] = (
            lambda h: released.append(h))
        fp = _FakeFileHandle()
        si.release_single_instance(fp)
        assert released == [fp]

    def test_file_like_falls_back_to_close_when_no_handler(
        self, monkeypatch,
    ):
        """If no POSIX release handler exists and the resolver
        returns None (Windows host, no injection), we still close
        the file ourselves so the lock and FD don't leak."""
        monkeypatch.setattr(sys, "platform", "win32")
        fp = _FakeFileHandle()
        si.release_single_instance(fp)
        assert fp.closed is True

    def test_int_handle_routes_to_close_handle(self):
        closed = []
        si._HANDLERS["close_handle"] = lambda h: closed.append(h)
        si.release_single_instance(7)
        assert closed == [7]

    def test_posix_release_handler_exception_swallowed(self):
        def _boom(_h):
            raise RuntimeError("fcntl unlock failed")

        si._HANDLERS["release_posix_lock"] = _boom
        si.release_single_instance(_FakeFileHandle())  # no raise


class TestSingleInstanceGuardPosix:
    """End-to-end ``single_instance_guard()`` over the POSIX path."""

    def test_first_instance_proceeds_with_handle(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        fp = _FakeFileHandle(fd=5)
        si._HANDLERS["acquire_posix_lock"] = (
            lambda path: ("acquired", fp))
        proceed, handle = si.single_instance_guard()
        assert proceed is True
        assert handle is fp

    def test_second_instance_blocks_and_prints_hint(
        self, monkeypatch, capsys,
    ):
        """No focus primitive on POSIX → the guard prints a hint to
        stderr instead so the user knows why this launch is a no-op."""
        monkeypatch.setattr(sys, "platform", "linux")
        si._HANDLERS["acquire_posix_lock"] = (
            lambda path: ("held", None))
        proceed, handle = si.single_instance_guard()
        assert proceed is False
        assert handle is None
        captured = capsys.readouterr()
        assert "already running" in captured.err.lower(), (
            "POSIX second-instance must print a hint to stderr; "
            f"got err={captured.err!r}")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="fcntl.flock is POSIX-only",
)
class TestPosixFcntlRoundTrip:
    """Exercise the real ``fcntl.flock`` path. Skipped on Windows
    (where ``fcntl`` doesn't exist) and on any host where the
    user can't write to the tmp dir we lockfile under."""

    def test_acquire_then_release_then_reacquire(self, tmp_path):
        path = str(tmp_path / "single.lock")
        s1, h1 = si._real_acquire_posix_lock(path)
        assert s1 == "acquired"
        assert h1 is not None
        si._real_release_posix_lock(h1)
        # Should succeed again now that the first FD is closed.
        s2, h2 = si._real_acquire_posix_lock(path)
        assert s2 == "acquired"
        si._real_release_posix_lock(h2)

    def test_second_acquire_while_first_held_returns_held(
        self, tmp_path,
    ):
        path = str(tmp_path / "single.lock")
        s1, h1 = si._real_acquire_posix_lock(path)
        try:
            assert s1 == "acquired"
            s2, h2 = si._real_acquire_posix_lock(path)
            assert s2 == "held"
            assert h2 is None
        finally:
            si._real_release_posix_lock(h1)

    def test_lockfile_records_pid(self, tmp_path):
        path = str(tmp_path / "single.lock")
        s, h = si._real_acquire_posix_lock(path)
        try:
            assert s == "acquired"
            # PID written to the file as informational metadata.
            with open(path, encoding="utf-8") as fp:
                content = fp.read()
            assert content.strip().isdigit()
            import os as _os
            assert int(content.strip()) == _os.getpid()
        finally:
            si._real_release_posix_lock(h)
