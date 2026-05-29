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

import datetime as _dt
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


@pytest.fixture(scope="session", autouse=True)
def _stub_yfinance_source_for_chartapp():
    """Keep scanner tests hermetic when app-wiring tests build ChartApp."""
    from tradinglab.data import DATA_SOURCES, register_source
    from tradinglab.models import Candle

    original = DATA_SOURCES.get("yfinance")

    def _fake_fetcher(ticker: str, interval: str) -> list[Candle]:
        del ticker, interval
        start = _dt.datetime(2024, 1, 2, 9, 30)
        candles: list[Candle] = []
        for i in range(120):
            close = 100.0 + i * 0.1
            candles.append(
                Candle(
                    date=start + _dt.timedelta(minutes=5 * i),
                    open=close - 0.2,
                    high=close + 0.4,
                    low=close - 0.4,
                    close=close,
                    volume=10_000 + i,
                )
            )
        return candles

    register_source("yfinance", _fake_fetcher)
    try:
        yield
    finally:
        if original is None:
            DATA_SOURCES.pop("yfinance", None)
        else:
            register_source("yfinance", original)
