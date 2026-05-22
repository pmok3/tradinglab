"""Per-package fixtures for GUI tests.

The Tk root + per-test ``root`` fixture are defined in
:mod:`tests.conftest` so they are shared with other GUI-touching test
packages (e.g. ``tests.scanner``).

This conftest only redirects the exits-storage cache dir to a tmp
dir so dialog save/import round-trips don't leak into the real cache.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _sandbox_exits_cache_dir():
    tmp = Path(tempfile.mkdtemp(prefix="exits_dialog_test_cache_"))
    from tradinglab.exits import storage as _exits_storage

    original = _exits_storage._cache_dir
    _exits_storage._cache_dir = lambda: tmp  # type: ignore[assignment]
    try:
        yield tmp
    finally:
        _exits_storage._cache_dir = original  # type: ignore[assignment]
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture(scope="session", autouse=True)
def _sandbox_entries_cache_dir():
    """Redirect entries-storage cache dir to a tmp dir for the GUI test session."""
    tmp = Path(tempfile.mkdtemp(prefix="entries_dialog_test_cache_"))
    from tradinglab.entries import storage as _entries_storage

    original = _entries_storage._cache_dir
    _entries_storage._cache_dir = lambda: tmp  # type: ignore[assignment]
    try:
        yield tmp
    finally:
        _entries_storage._cache_dir = original  # type: ignore[assignment]
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
