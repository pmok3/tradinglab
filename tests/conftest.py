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
