"""Shared pytest fixtures.

Forces matplotlib's Agg backend before tradinglab imports anywhere so
smoke tests can run headless in CI without a display server.

Also provides a session-scoped Tk root + per-test ``Toplevel`` fixture
shared by all GUI-touching test packages (scanner_tab, exits_dialog,
exits_tab). Tk has a known quirk on Windows ARM64: once a Tk root is
destroyed, a second one cannot be created in the same process. Hosting
the root at the top-level conftest avoids the per-package fixtures
fighting for ownership.
"""
from __future__ import annotations

import os

os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


import tkinter as tk

import pytest

# ---------------------------------------------------------------------
# Tk-finalizer landmine fix (CLAUDE.md §7.5 + cousin)
# ---------------------------------------------------------------------
# Mirror of the proactive neuter that ``tests/smoke/conftest.py``
# applies for the smoke suite. Lifted to the top-level conftest so
# the SAME protection applies to ``pytest tests/unit tests/scanner -q``
# (the unit gate the release workflow runs BEFORE the smoke step) —
# matplotlib's Tk backend gets imported transitively by enough unit
# tests that leftover ``tk.Variable`` / ``tk.PhotoImage`` instances
# can GC on the wrong thread → ``Tcl_AsyncDelete: async handler
# deleted by the wrong thread`` → SIGABRT.
#
# Symptom in CI: the unit step fails with "Windows fatal exception:
# code 0x80000003" and a ``Tcl_AsyncDelete`` line in the log; the
# 4500+ test pass count locally never reproduces because dev pytest
# sessions terminate before GC reaches the dead objects.
#
# Skipping the Tcl ``unset`` / ``image delete`` call is harmless in
# a test process — the small per-test leak is reclaimed at process
# exit, and the only thing the original finalizer did was issue a
# command against a Tcl interp that's about to be torn down anyway.
try:
    import tkinter as _tk_neuter
    _tk_neuter.Variable.__del__ = lambda self: None  # type: ignore[assignment]
    _tk_neuter.Image.__del__ = lambda self: None  # type: ignore[assignment]
except Exception:  # noqa: BLE001
    pass


@pytest.fixture(scope="session")
def _tk_root():
    try:
        r = tk.Tk()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Tk not available: {e}", allow_module_level=False)
    r.withdraw()
    yield r
    try:
        r.destroy()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture
def root(_tk_root):
    """Per-test Toplevel under the shared Tk root."""
    top = tk.Toplevel(_tk_root)
    top.withdraw()
    yield top
    try:
        top.destroy()
    except Exception:  # noqa: BLE001
        pass
