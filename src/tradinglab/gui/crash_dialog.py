"""Crash dialog displayed when an unhandled exception escapes the Tk loop.

The standard Tkinter behavior on an unhandled exception inside an
event handler is to print the traceback to ``sys.stderr`` and
continue running. In a frozen ``--windowed`` PyInstaller build
there is no console attached, so the user sees nothing — the chart
just stops responding or the window vanishes.

This module installs two hooks so a crash always produces

1. A timestamped ``crash-YYYY-MM-DDTHH-MM-SS.txt`` file under
   :func:`tradinglab.paths.logs_dir` containing the traceback +
   system metadata, and
2. A modal ``tkinter.messagebox`` dialog with the path to that file
   so the user knows where to find it (and can attach it to a bug
   report).

Hooks
-----
* :func:`install_crash_handler()` — replaces ``sys.excepthook`` so
  exceptions that escape the main thread are captured.
* :func:`install_tk_excepthook(root)` — replaces ``root.report_callback_exception``
  so exceptions inside Tk callbacks are captured (these don't go
  through ``sys.excepthook``).

Both hooks delegate to :func:`_handle_crash` which is idempotent
in the sense that repeated invocations write distinct files (the
timestamp resolution is 1 second; collisions are vanishingly
unlikely outside contrived tests).

Tests
-----
Unit tests can verify the crash file is written by calling
:func:`_write_crash_file` directly. The dialog half is best tested
manually because mock Tk message boxes are brittle.
"""
from __future__ import annotations

import datetime
import platform
import sys
import traceback
from pathlib import Path

_CRASH_PREFIX = "crash-"
_CRASH_SUFFIX = ".txt"

#: Maximum number of crash files to keep on disk. Older crash files
#: are removed on each new write so the logs directory doesn't grow
#: unbounded on a chronically-broken install.
MAX_CRASH_FILES_KEPT: int = 30


def _logs_dir_or_fallback() -> Path:
    """Return the logs directory, falling back to cwd on failure."""
    try:
        from ..paths import logs_dir
        return logs_dir()
    except Exception:  # noqa: BLE001
        return Path(".").resolve()


def _write_crash_file(exc_type, exc_value, exc_tb,
                      *, when: datetime.datetime | None = None) -> Path:
    """Serialize the exception triple to a fresh crash file. Return the path."""
    when = when or datetime.datetime.now()
    stamp = when.strftime("%Y-%m-%dT%H-%M-%S")
    out_dir = _logs_dir_or_fallback()
    path = out_dir / f"{_CRASH_PREFIX}{stamp}{_CRASH_SUFFIX}"

    lines = []
    lines.append("TradingLab crash report")
    lines.append(f"Timestamp: {when.isoformat()}")
    try:
        from .._version import version_string
        lines.append(f"Version: {version_string()}")
    except Exception:  # noqa: BLE001
        lines.append("Version: <unknown>")
    lines.append(f"Python: {sys.version.splitlines()[0]}")
    lines.append(f"Platform: {platform.platform()}")
    lines.append(f"Frozen: {bool(getattr(sys, 'frozen', False))}")
    lines.append("")
    lines.append("--- Traceback ---")
    try:
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    except Exception:  # noqa: BLE001
        tb_text = f"{exc_type.__name__}: {exc_value}"
    lines.append(tb_text)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        # If we can't even write the crash file, there's nothing
        # sensible to do but bail. The caller will still show the
        # dialog with whatever path we tried.
        pass

    _prune_old_crash_files(out_dir)
    return path


def _prune_old_crash_files(directory: Path) -> None:
    """Keep the newest :data:`MAX_CRASH_FILES_KEPT` files; delete the rest."""
    try:
        candidates = sorted(
            directory.glob(f"{_CRASH_PREFIX}*{_CRASH_SUFFIX}"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for stale in candidates[MAX_CRASH_FILES_KEPT:]:
        try:
            stale.unlink()
        except OSError:
            pass


def _show_dialog(path: Path, exc_value) -> None:
    """Show a modal messagebox pointing at the crash file. Best-effort."""
    try:
        from tkinter import messagebox
        # Synthesize a one-line summary that's safe to show users.
        msg = (
            f"TradingLab hit an unexpected error and may be unstable.\n\n"
            f"Error: {type(exc_value).__name__}: {str(exc_value)[:200]}\n\n"
            f"A full diagnostic report was saved to:\n{path}\n\n"
            f"Please include this file when reporting the bug."
        )
        messagebox.showerror("TradingLab — error", msg)
    except Exception:  # noqa: BLE001
        # Last-resort fallback to stderr (visible only in console builds).
        try:
            sys.stderr.write(f"\n[crash] report: {path}\n")
        except Exception:  # noqa: BLE001
            pass


def _handle_crash(exc_type, exc_value, exc_tb) -> None:
    """Write the crash file and try to show the dialog. Never raises."""
    try:
        # Skip ``KeyboardInterrupt`` and ``SystemExit`` — those are
        # cooperative shutdown, not crashes.
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            return
        path = _write_crash_file(exc_type, exc_value, exc_tb)
        _show_dialog(path, exc_value)
    except Exception:  # noqa: BLE001
        # The crash handler must NEVER raise.
        pass


# ---------------------------------------------------------------------------
# Public hook installers
# ---------------------------------------------------------------------------


_PREV_EXCEPTHOOK = None  # type: ignore[var-annotated]


def install_crash_handler() -> None:
    """Replace :data:`sys.excepthook` with our crash-writing version.

    Idempotent — repeated calls do not stack handlers. Stores the
    previous excepthook so :func:`_handle_crash` can chain to it
    after writing the file (preserving the stderr dump for users who
    do have a console).
    """
    global _PREV_EXCEPTHOOK
    if _PREV_EXCEPTHOOK is not None:
        return
    _PREV_EXCEPTHOOK = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        _handle_crash(exc_type, exc_value, exc_tb)
        try:
            if _PREV_EXCEPTHOOK is not None:
                _PREV_EXCEPTHOOK(exc_type, exc_value, exc_tb)
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = _hook


def install_tk_excepthook(root) -> None:
    """Replace ``root.report_callback_exception`` so Tk callback errors are caught.

    Tk swallows exceptions raised inside event handlers and routes
    them through ``report_callback_exception`` — they DO NOT reach
    :data:`sys.excepthook`. This is the second half of full
    coverage. Idempotent: monkey-patches the bound method once.
    """
    if getattr(root, "_tradinglab_crash_hook_installed", False):
        return

    def _tk_hook(exc_type, exc_value, exc_tb):  # noqa: ARG001
        _handle_crash(exc_type, exc_value, exc_tb)
        # Don't chain to the original Tk hook because the default
        # behavior is to print + return — our dialog already
        # communicated the issue and the stderr print is harmless on
        # console builds but invisible in --windowed builds.

    try:
        root.report_callback_exception = _tk_hook
        root._tradinglab_crash_hook_installed = True
    except Exception:  # noqa: BLE001
        pass


def reset_for_tests() -> None:
    """Restore ``sys.excepthook`` to its prior value. Test-only."""
    global _PREV_EXCEPTHOOK
    if _PREV_EXCEPTHOOK is not None:
        sys.excepthook = _PREV_EXCEPTHOOK
        _PREV_EXCEPTHOOK = None


__all__ = [
    "install_crash_handler",
    "install_tk_excepthook",
    "reset_for_tests",
    "MAX_CRASH_FILES_KEPT",
]
