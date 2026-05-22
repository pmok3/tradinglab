"""Single-instance protection for the frozen redistributable.

Stops a second double-click on ``TradingLab.exe`` (Windows) — or a
second launch of the frozen macOS / Linux bundles — from spawning
two copies that fight over the same on-disk config + cache files.

Two backends exist, picked by ``sys.platform``:

* **Windows** — named kernel mutex
  (``Local\\TradingLab.SingleInstance``). The first instance owns
  the mutex; second-instance attempts fail with
  ``ERROR_ALREADY_EXISTS`` and the helper instead brings the
  existing top-level window to the foreground via
  ``SetForegroundWindow``.

* **POSIX (Linux / macOS)** — exclusive, non-blocking ``fcntl.flock``
  on a lockfile in ``app_data_dir()``. The first instance opens the
  file, takes ``LOCK_EX | LOCK_NB`` on the FD, and keeps it alive
  for the process lifetime. Second-instance attempts get
  ``EWOULDBLOCK`` / ``EAGAIN`` and bail. There is no
  cross-platform focus-window primitive (X11 / Wayland / AppKit
  each require their own dance), so the POSIX helper just prints
  a hint to stderr and exits — clicking the dock icon brings the
  existing window forward anyway.

If ``fcntl`` is unavailable (e.g. a Python build without it) or the
data dir can't be written, the POSIX path silently degrades to "no
protection, you got it" — the user always gets *some* process
running rather than a silent no-launch.

The mutex name uses the ``Local\\`` prefix on Windows so it is
scoped to the current user session — two different Windows users
(or one user running both their normal session and an admin
session) can each launch their own instance without colliding.
The POSIX lockfile lives under each user's ``app_data_dir()`` so
the same scoping holds.

Public API
----------
* :func:`acquire_single_instance` — try to acquire the guard.
  Returns ``(acquired, handle)``. ``acquired=False`` means another
  instance is already running.
* :func:`release_single_instance(handle)` — release the guard
  (no-op when handle is ``None``). Dispatches by handle type:
  POSIX file objects route to ``release_posix_lock``, Windows int
  handles route to ``close_handle``.
* :func:`focus_existing_window` — best-effort find + bring-to-front
  of the existing TradingLab top-level window. Windows-only;
  returns ``False`` on POSIX (the OS-level second-launch UX
  generally raises the existing window from the dock).
* :func:`single_instance_guard()` — convenience all-in-one entry
  point used from ``__main__.py``. Returns ``True`` if this process
  should proceed (we got the guard, OR no protection is available).
  Returns ``False`` if a second instance was detected (existing
  window was raised on Windows; a hint was printed on POSIX) —
  caller should exit 0.

Tests use the lower-level helpers + monkeypatch the platform layer
via :data:`_HANDLERS` so both the Windows and POSIX semantics can
be exercised on any host. See ``tests/unit/test_single_instance.py``.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any

# Canonical mutex + window class names. The Local\ prefix scopes the
# mutex to the current Windows user session. The window class name
# matches the one Tk's wm-class hint sets (see
# :func:`tradinglab.app._identify_to_window_manager`) and the
# AppUserModelID's "TradingLab" suffix.
MUTEX_NAME = "Local\\TradingLab.SingleInstance"
WINDOW_TITLE_PREFIX = "TradingLab"
ERROR_ALREADY_EXISTS = 183  # ERROR_ALREADY_EXISTS from Win32 headers.

# POSIX lockfile name (kept short + namespaced; leading dot keeps
# it out of casual ``ls`` listings since it sits next to user data).
POSIX_LOCK_FILENAME = ".tradinglab.singleinstance.lock"


# Indirection layer so the unit tests can inject fakes for the
# platform entry points we use. Production code keeps the real
# bindings; tests monkeypatch this dict.
#
# Windows keys:
# * ``"create_mutex"(name) -> (handle, last_error)``
# * ``"close_handle"(handle) -> None``
# * ``"find_window"(title_prefix) -> hwnd or 0``
# * ``"focus_window"(hwnd) -> bool``
#
# POSIX keys:
# * ``"acquire_posix_lock"(path) -> (status, handle)`` where
#   ``status`` is one of ``"acquired"`` / ``"held"`` /
#   ``"unsupported"`` and ``handle`` is the open file object on
#   ``"acquired"``, ``None`` otherwise.
# * ``"release_posix_lock"(handle) -> None``
_HANDLERS: dict[str, Callable[..., Any] | None] = {
    "create_mutex": None,
    "close_handle": None,
    "find_window": None,
    "focus_window": None,
    "acquire_posix_lock": None,
    "release_posix_lock": None,
}


def _real_create_mutex(name: str) -> tuple[int | None, int]:
    """Call ``CreateMutexW`` and return ``(handle, GetLastError())``."""
    import ctypes  # local import: keep non-Windows imports clean
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateMutexW
    create.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    create.restype = wintypes.HANDLE
    handle = create(None, False, name)
    last_error = ctypes.get_last_error()
    return (int(handle) if handle else None, int(last_error))


def _real_close_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    close(handle)


def _real_find_window(title_prefix: str) -> int:
    """Walk top-level windows looking for one whose title starts with
    ``title_prefix``. Returns the first match's HWND or 0.

    We can't use ``FindWindowW`` directly because the Tk window
    class is ``"Tk"`` (every Python+Tk app shares it) and we only
    want our own. Matching by the **title** prefix is robust:
    ``ChartApp.__init__`` sets ``self.title(f"TradingLab v{version}")``,
    so any window whose title starts with "TradingLab" is ours.
    """
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    GetWindowTextW.restype = ctypes.c_int
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    GetWindowTextLengthW.argtypes = [wintypes.HWND]
    GetWindowTextLengthW.restype = ctypes.c_int
    IsWindowVisible = user32.IsWindowVisible
    IsWindowVisible.argtypes = [wintypes.HWND]
    IsWindowVisible.restype = wintypes.BOOL

    found: dict[str, int] = {"hwnd": 0}

    def _cb(hwnd: int, _lparam: int) -> int:
        if not IsWindowVisible(hwnd):
            return 1  # keep enumerating
        length = GetWindowTextLengthW(hwnd)
        if length <= 0:
            return 1
        buf = ctypes.create_unicode_buffer(length + 2)
        GetWindowTextW(hwnd, buf, length + 2)
        title = buf.value or ""
        if title.startswith(title_prefix):
            found["hwnd"] = int(hwnd)
            return 0  # stop enumeration
        return 1

    EnumWindows(EnumWindowsProc(_cb), 0)
    return found["hwnd"]


def _real_focus_window(hwnd: int) -> bool:
    """Bring ``hwnd`` to the foreground. Best effort."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    # If the window is minimized, restore it first. SW_RESTORE = 9.
    ShowWindow = user32.ShowWindow
    ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    ShowWindow.restype = wintypes.BOOL
    SetForegroundWindow = user32.SetForegroundWindow
    SetForegroundWindow.argtypes = [wintypes.HWND]
    SetForegroundWindow.restype = wintypes.BOOL
    IsIconic = user32.IsIconic
    IsIconic.argtypes = [wintypes.HWND]
    IsIconic.restype = wintypes.BOOL

    try:
        if IsIconic(hwnd):
            ShowWindow(hwnd, 9)  # SW_RESTORE
        return bool(SetForegroundWindow(hwnd))
    except Exception:  # noqa: BLE001
        return False


def _real_acquire_posix_lock(
    path: str,
) -> tuple[str, Any | None]:
    """Open ``path`` and try an exclusive, non-blocking ``flock``.

    Returns ``(status, handle)`` where ``status`` is one of:

    * ``"acquired"`` — we own the lock; ``handle`` is the open
      file object. Caller must keep it alive for the lifetime
      of the process; closing the FD (or the process dying)
      releases the lock automatically.
    * ``"held"`` — another live process holds the lock.
      ``handle`` is ``None``.
    * ``"unsupported"`` — ``fcntl`` isn't available on this
      host, the lockfile path can't be created, or the file
      can't be opened. ``handle`` is ``None``. Caller treats
      this as "no protection, proceed".

    Stale lockfiles from a crashed previous instance are handled
    by the kernel: ``flock`` is per-FD, so the OS releases the
    lock when the dead process's FDs close, regardless of whether
    the file on disk still exists.
    """
    try:
        import fcntl  # type: ignore[import-not-found]
    except ImportError:
        return ("unsupported", None)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    except OSError:
        return ("unsupported", None)
    try:
        # noqa: SIM115 — the FD must outlive this function.
        fp = open(path, "w", encoding="utf-8")
    except OSError:
        return ("unsupported", None)
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        try:
            fp.close()
        except Exception:  # noqa: BLE001
            pass
        return ("held", None)
    try:
        fp.write(str(os.getpid()))
        fp.flush()
    except OSError:
        # Writing the PID is purely informational; don't release
        # the lock if it fails.
        pass
    return ("acquired", fp)


def _real_release_posix_lock(handle: Any) -> None:
    """Release the POSIX lock and close the file descriptor."""
    if handle is None:
        return
    try:
        import fcntl  # type: ignore[import-not-found]
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001
            pass
    except ImportError:
        pass
    try:
        handle.close()
    except Exception:  # noqa: BLE001
        pass


def _posix_lock_path() -> str:
    """Return the default POSIX lockfile path under ``app_data_dir()``."""
    from .paths import app_data_dir
    return str(app_data_dir() / POSIX_LOCK_FILENAME)


def _resolve_handler(key: str) -> Callable[..., Any] | None:
    """Return the registered handler for ``key``.

    Defaults to the real platform implementation when one exists
    for the current ``sys.platform``: the Windows ctypes layer on
    ``"win32"``, the POSIX ``fcntl`` layer everywhere else.
    """
    cur = _HANDLERS.get(key)
    if cur is not None:
        return cur
    if sys.platform == "win32":
        return {
            "create_mutex": _real_create_mutex,
            "close_handle": _real_close_handle,
            "find_window": _real_find_window,
            "focus_window": _real_focus_window,
        }.get(key)
    return {
        "acquire_posix_lock": _real_acquire_posix_lock,
        "release_posix_lock": _real_release_posix_lock,
    }.get(key)


def _is_windows_active() -> bool:
    """Should we run the Windows acquire path?

    True when we're actually on Windows, OR when a test has
    injected a ``create_mutex`` handler — that's how the existing
    test suite exercises the Windows logic from a Linux/macOS CI
    host.
    """
    return sys.platform == "win32" or _HANDLERS.get("create_mutex") is not None


def _acquire_windows(name: str) -> tuple[bool, int | None]:
    handler = _resolve_handler("create_mutex")
    if handler is None:
        return (True, None)
    try:
        handle, last_error = handler(name)
    except Exception:  # noqa: BLE001
        return (True, None)
    if handle is None:
        return (True, None)
    if last_error == ERROR_ALREADY_EXISTS:
        try:
            close = _resolve_handler("close_handle")
            if close is not None:
                close(handle)
        except Exception:  # noqa: BLE001
            pass
        return (False, None)
    return (True, handle)


def _acquire_posix(
    posix_lock_path: str | None,
) -> tuple[bool, Any | None]:
    handler = _resolve_handler("acquire_posix_lock")
    if handler is None:
        return (True, None)
    if posix_lock_path is None:
        try:
            posix_lock_path = _posix_lock_path()
        except Exception:  # noqa: BLE001
            return (True, None)
    try:
        result = handler(posix_lock_path)
    except Exception:  # noqa: BLE001
        return (True, None)
    try:
        status, handle = result
    except (TypeError, ValueError):
        return (True, None)
    if status == "acquired":
        return (True, handle)
    if status == "held":
        return (False, None)
    return (True, None)


def acquire_single_instance(
    name: str = MUTEX_NAME,
    *,
    posix_lock_path: str | None = None,
) -> tuple[bool, Any | None]:
    """Try to acquire the single-instance guard.

    Returns ``(acquired, handle)``:

    * ``(True, handle)`` — first instance. Handle must be kept
      alive for the lifetime of the process and released via
      :func:`release_single_instance` on exit. ``handle`` is an
      ``int`` (Windows mutex) or a file-like object (POSIX
      ``flock`` FD).
    * ``(True, None)`` — no protection available on this host
      (POSIX without ``fcntl``, unwritable data dir, etc.).
      Caller proceeds.
    * ``(False, None)`` — another instance already holds the
      guard. Caller should call :func:`focus_existing_window`
      and exit.

    Never raises. Any internal error degrades to ``(True, None)``
    so the user always gets *some* process running rather than a
    silent no-launch.

    Parameters
    ----------
    name : str
        Windows mutex name (ignored on POSIX).
    posix_lock_path : str | None
        Override the default lockfile path for tests. Ignored on
        Windows.
    """
    if _is_windows_active():
        return _acquire_windows(name)
    return _acquire_posix(posix_lock_path)


def release_single_instance(handle: Any | None) -> None:
    """Release the guard. Safe to call with ``None``.

    Dispatches by handle type: file-like objects (POSIX) route
    through ``release_posix_lock``; everything else (Windows int
    handles) routes through ``close_handle``.
    """
    if handle is None:
        return
    # File-like → POSIX path. We sniff for fileno() + close() to
    # disambiguate from the Windows int handle without importing
    # IO ABCs (the actual production handle is a builtin file
    # object; test fakes can be any compatible duck).
    if hasattr(handle, "fileno") and hasattr(handle, "close"):
        release = _resolve_handler("release_posix_lock")
        if release is None:
            try:
                handle.close()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            release(handle)
        except Exception:  # noqa: BLE001
            pass
        return
    # Windows int handle path.
    close = _resolve_handler("close_handle")
    if close is None:
        return
    try:
        close(handle)
    except Exception:  # noqa: BLE001
        pass


def focus_existing_window(
    title_prefix: str = WINDOW_TITLE_PREFIX,
) -> bool:
    """Find the existing TradingLab window and bring it to the front.

    Returns ``True`` if a window was found and the foreground
    request was issued. Returns ``False`` if no window was found,
    focus failed, or the platform has no find / focus primitives
    (e.g. POSIX without an X11/Wayland focus shim).
    """
    find = _resolve_handler("find_window")
    focus = _resolve_handler("focus_window")
    if find is None or focus is None:
        return False
    try:
        hwnd = int(find(title_prefix) or 0)
    except Exception:  # noqa: BLE001
        return False
    if not hwnd:
        return False
    try:
        return bool(focus(hwnd))
    except Exception:  # noqa: BLE001
        return False


def single_instance_guard() -> tuple[bool, Any | None]:
    """Convenience entry point used by ``__main__``.

    Returns ``(should_proceed, handle)``:

    * ``(True, handle)`` — first instance (or platform has no
      protection available). Caller keeps ``handle`` for the
      lifetime of the process and passes it to
      :func:`release_single_instance` at exit.
    * ``(False, None)`` — another instance is already running.
      :func:`focus_existing_window` has already been called
      (Windows); on POSIX a hint is printed to ``stderr`` since
      no cross-platform focus primitive exists. Caller exits 0.
    """
    acquired, handle = acquire_single_instance()
    if acquired:
        return (True, handle)
    if not focus_existing_window():
        # POSIX (or Windows with a since-closed window). Print a
        # hint so the user knows why this launch became a no-op.
        try:
            print(
                "TradingLab is already running. "
                "Activate the existing window from your "
                "dock / taskbar.",
                file=sys.stderr,
            )
        except Exception:  # noqa: BLE001
            pass
    return (False, None)


__all__ = [
    "MUTEX_NAME",
    "WINDOW_TITLE_PREFIX",
    "POSIX_LOCK_FILENAME",
    "ERROR_ALREADY_EXISTS",
    "acquire_single_instance",
    "release_single_instance",
    "focus_existing_window",
    "single_instance_guard",
]
