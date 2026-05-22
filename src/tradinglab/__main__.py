r"""Enables ``python -m tradinglab`` and serves as the PyInstaller
entry point.

We deliberately use an **absolute** import (``tradinglab.app``)
rather than a relative one (``.app``) because the PyInstaller
bootloader runs this file as a top-level script — ``__name__ ==
"__main__"`` and there is no parent package context, so relative
imports raise ``ImportError: attempted relative import with no
known parent package``. The absolute form works in both modes:

* ``python -m tradinglab`` (package context exists, absolute
  resolution finds the package on ``sys.path``).
* Frozen ``TradingLab.exe`` (PyInstaller injects the package
  on ``sys.path``; the absolute import resolves directly).

``multiprocessing.freeze_support()`` is called unconditionally at
process start so any accidental child-process spawn in a frozen
build returns into the bootloader instead of re-launching the GUI
in a fork-bomb loop. The app does not use :mod:`multiprocessing`
today, but third-party imports (numpy / matplotlib subroutines, the
yfinance fetcher's thread pool helpers) can do so transitively;
this is the documented defensive call that the PyInstaller docs
recommend on Windows.

Single-instance protection (Feature B): before any GUI work we
try to acquire a single-instance guard. On **Windows** that's a
named kernel mutex (``Local\TradingLab.SingleInstance``). On
**POSIX (Linux / macOS)** it's an exclusive ``fcntl.flock`` on a
lockfile under ``app_data_dir()``. A second double-click /
launch detects the existing instance — Windows brings its
window to the foreground; POSIX prints a hint to stderr — and
exits 0. If neither backend is available (e.g. a Python build
without ``fcntl`` on a POSIX-spoofed host) the guard degrades to
a no-op so the user always gets *some* process running.
"""

import multiprocessing

from tradinglab._single_instance import (
    release_single_instance,
    single_instance_guard,
)
from tradinglab.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    import sys

    proceed, handle = single_instance_guard()
    if not proceed:
        # Another instance is already running and has been raised
        # to the foreground; exit cleanly so the desktop shortcut
        # doesn't leave an extra taskbar entry.
        sys.exit(0)
    try:
        sys.exit(main())
    finally:
        release_single_instance(handle)
