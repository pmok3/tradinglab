"""Tests for the yfinance fetch timeout.

P0 regression: ``fetch_live_data`` passed no timeout to
``yf.Ticker(...).history(...)``, so a stalled connection occupied a fetch
worker indefinitely and starved the pool. The fix passes
``YFINANCE_TIMEOUT_S`` (15 s, matching the other REST vendors) to the
``history()`` call; yfinance raises on timeout and the source-layer
``except Exception`` coerces it to ``None`` per the ``data/base.py``
no-raise contract.

``yfinance`` is not installed in the test environment, so the tests stub
``sys.modules["yfinance"]`` with a fake ``Ticker`` whose ``history()``
honours the ``timeout`` kwarg the way the real transport does: it blocks,
then raises once the deadline elapses. (The real yfinance raises
``requests.exceptions.ConnectTimeout``/``ReadTimeout`` — both
``OSError`` subclasses; the fake raises builtin ``TimeoutError`` since
``requests`` is only a transitive dependency. The source-layer behavior
under test — ``Exception`` → ``None`` — is identical for either.)
"""
from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType

import pandas as pd
import pytest

from tradinglab.data import yfinance_source as yf_src


def _one_bar_frame() -> pd.DataFrame:
    """Minimal valid Yahoo-shaped frame: one daily bar, tz-aware index."""
    idx = pd.date_range("2024-01-02", periods=1, freq="D", tz="America/New_York")
    return pd.DataFrame(
        {
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.5],
            "Volume": [1_000],
        },
        index=idx,
    )


@pytest.fixture()
def fake_yfinance(monkeypatch):
    """Install a fake ``yfinance`` module.

    Returns ``(behaviors, calls)`` where ``behaviors`` maps a ticker to a
    ``(ticker, kwargs) -> DataFrame`` callable invoked by the fake
    ``Ticker.history()``, and ``calls`` records every ``history()``
    invocation as ``(ticker, kwargs)``.
    """
    behaviors: dict = {}
    calls: list = []

    class _FakeTicker:
        def __init__(self, ticker: str) -> None:
            self._ticker = ticker

        def history(self, **kwargs):
            calls.append((self._ticker, kwargs))
            return behaviors[self._ticker](self._ticker, kwargs)

    mod = ModuleType("yfinance")
    mod.Ticker = _FakeTicker
    monkeypatch.setitem(sys.modules, "yfinance", mod)
    return behaviors, calls


def test_timeout_kwarg_reaches_history_call(fake_yfinance) -> None:
    """The timeout is passed to the actual network call (``history()``),
    not just stored on a config object."""
    behaviors, calls = fake_yfinance
    behaviors["AMD"] = lambda ticker, kwargs: pd.DataFrame()

    assert yf_src.fetch_live_data("AMD", "1d") is None  # empty frame → None

    assert calls, "Ticker.history() was never called"
    _ticker, kwargs = calls[0]
    assert kwargs["timeout"] == yf_src.YFINANCE_TIMEOUT_S
    assert yf_src.YFINANCE_TIMEOUT_S == 15  # matches the other vendors


def test_stalled_fetch_returns_none_promptly(fake_yfinance, monkeypatch) -> None:
    """A stalled connection raises (timeout) instead of hanging forever:
    the fetch returns ``None`` promptly per the no-raise contract."""
    behaviors, _calls = fake_yfinance
    monkeypatch.setattr(yf_src, "YFINANCE_TIMEOUT_S", 0.2)
    never_set = threading.Event()

    def _hang(ticker: str, kwargs: dict):
        timeout = kwargs.get("timeout")
        assert timeout == 0.2, "timeout override must reach the network call"
        # Simulate the transport honouring the timeout: block, then raise
        # once the deadline elapses (never_set is never set).
        if not never_set.wait(timeout):
            raise TimeoutError(f"stalled connection for {ticker}")
        raise AssertionError("unreachable")

    behaviors["STALL"] = _hang

    start = time.monotonic()
    assert yf_src.fetch_live_data("STALL", "1d") is None
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"stalled fetch hung for {elapsed:.1f}s"


def test_timed_out_fetch_releases_worker(fake_yfinance, monkeypatch) -> None:
    """Downstream order effect: a timed-out fetch releases its worker.

    With a single-worker pool, the fast fetch is queued *behind* the
    stalled one. Without the timeout the worker would be occupied forever
    and ``fast.result()`` would raise ``concurrent.futures.TimeoutError``;
    with the fix the stalled fetch resolves to ``None`` quickly and the
    fast fetch still completes.
    """
    behaviors, _calls = fake_yfinance
    monkeypatch.setattr(yf_src, "YFINANCE_TIMEOUT_S", 0.2)
    never_set = threading.Event()

    def _hang(ticker: str, kwargs: dict):
        if not never_set.wait(kwargs["timeout"]):
            raise TimeoutError(f"stalled connection for {ticker}")
        raise AssertionError("unreachable")

    def _fast(ticker: str, kwargs: dict):
        return _one_bar_frame()

    behaviors["STALL"] = _hang
    behaviors["FAST"] = _fast

    with ThreadPoolExecutor(max_workers=1) as pool:
        slow = pool.submit(yf_src.fetch_live_data, "STALL", "1d")
        fast = pool.submit(yf_src.fetch_live_data, "FAST", "1d")

        candles = fast.result(timeout=10)
        assert candles is not None and len(candles) == 1
        assert slow.result(timeout=10) is None
