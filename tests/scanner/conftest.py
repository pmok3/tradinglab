"""Shared pytest fixtures for scanner tests.

Per-test isolation only — the session-scoped Tk root is defined in
:mod:`tests.conftest` so multiple GUI test packages can share one root
on Windows ARM64 (where Tk root destroy/recreate is broken).

We also redirect :func:`tradinglab.scanner.storage._cache_dir` to a
session-scoped tmp dir for *every* scanner test. Without this the
session-scoped ChartApp fixture autoload + per-test save round-trips
silently leak files into the developer's real cache. Per-test
``monkeypatch`` cannot reach the session fixture's `_app` setup, so we
patch at session scope before any ChartApp is constructed.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _sandbox_scanner_cache_dir():
    """Redirect scanner storage to a session tmp dir so tests never
    write into the real ``<cache>/scans/``."""
    tmp = Path(tempfile.mkdtemp(prefix="scanner_test_cache_"))
    from tradinglab.scanner import storage as _scan_storage

    original = _scan_storage._cache_dir
    _scan_storage._cache_dir = lambda: tmp  # type: ignore[assignment]
    try:
        yield tmp
    finally:
        _scan_storage._cache_dir = original  # type: ignore[assignment]
        # Best-effort cleanup; ignore in-use files on Windows.
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass
