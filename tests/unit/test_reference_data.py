"""Unit tests for :mod:`tradinglab.core.reference_data`."""

from __future__ import annotations

import threading
from typing import List

import numpy as np
import pytest

from tradinglab.core import reference_data as rd
from tradinglab.core.bars import Bars


@pytest.fixture(autouse=True)
def _reset_registry():
    rd.clear()
    yield
    rd.clear()


def _bars(n: int = 5) -> Bars:
    o = np.linspace(1, n, n, dtype=np.float64)
    return Bars.from_arrays(
        open=o, high=o, low=o, close=o,
        volume=np.full(n, 1000.0, dtype=np.float64),
    )


def test_get_returns_none_when_empty_and_no_provider():
    assert rd.get_reference_bars("yfinance", "SPY", "5m") is None
    assert rd.generation() == 0


def test_set_then_get_returns_bars():
    b = _bars(3)
    rd.set_reference_bars("yfinance", "SPY", "5m", b)
    out = rd.get_reference_bars("yfinance", "SPY", "5m")
    assert out is b
    assert rd.generation() == 1


def test_source_aware_keying():
    b1 = _bars(2)
    b2 = _bars(7)
    rd.set_reference_bars("yfinance", "SPY", "5m", b1)
    rd.set_reference_bars("synthetic", "SPY", "5m", b2)
    assert rd.get_reference_bars("yfinance", "SPY", "5m") is b1
    assert rd.get_reference_bars("synthetic", "SPY", "5m") is b2


def test_symbol_normalization_to_upper():
    b = _bars(2)
    rd.set_reference_bars("yfinance", "spy", "5m", b)
    assert rd.get_reference_bars("yfinance", "SPY", "5m") is b
    assert rd.get_reference_bars("yfinance", "Spy", "5m") is b


def test_provider_invoked_on_miss_only_once_until_resolved():
    calls: List[tuple] = []

    def provider(source: str, symbol: str, interval: str) -> None:
        calls.append((source, symbol, interval))

    rd.set_provider(provider)
    # First miss schedules a fetch.
    assert rd.get_reference_bars("yfinance", "SPY", "5m") is None
    # Second miss while fetch is "in flight" must not double-schedule.
    assert rd.get_reference_bars("yfinance", "SPY", "5m") is None
    assert calls == [("yfinance", "SPY", "5m")]
    # Provider resolves: clears the inflight slot.
    rd.set_reference_bars("yfinance", "SPY", "5m", _bars(4))
    # Subsequent reads now hit cache.
    assert rd.get_reference_bars("yfinance", "SPY", "5m") is not None
    assert calls == [("yfinance", "SPY", "5m")]  # no new schedule


def test_arrival_callback_fires_on_set():
    fired = []

    def cb() -> None:
        fired.append(rd.generation())

    rd.set_provider(None, on_arrival=cb)
    rd.set_reference_bars("yfinance", "SPY", "5m", _bars(2))
    rd.set_reference_bars("yfinance", "QQQ", "5m", _bars(2))
    assert fired == [1, 2]


def test_arrival_callback_exception_swallowed():
    rd.set_provider(None, on_arrival=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    # Must not propagate.
    rd.set_reference_bars("yfinance", "SPY", "5m", _bars(2))
    assert rd.get_reference_bars("yfinance", "SPY", "5m") is not None


def test_provider_exception_releases_inflight():
    def bad(source: str, symbol: str, interval: str) -> None:
        raise RuntimeError("network down")

    rd.set_provider(bad)
    assert rd.get_reference_bars("yfinance", "SPY", "5m") is None
    # A second call must retry — the in-flight slot was released.
    counter = {"n": 0}

    def good(source: str, symbol: str, interval: str) -> None:
        counter["n"] += 1

    rd.set_provider(good)
    rd.get_reference_bars("yfinance", "SPY", "5m")
    rd.get_reference_bars("yfinance", "SPY", "5m")
    assert counter["n"] == 1  # still inflight after first call


def test_mark_fetch_failed_allows_retry():
    calls = {"n": 0}

    def provider(*_a) -> None:
        calls["n"] += 1

    rd.set_provider(provider)
    rd.get_reference_bars("yfinance", "SPY", "5m")
    rd.mark_fetch_failed("yfinance", "SPY", "5m")
    rd.get_reference_bars("yfinance", "SPY", "5m")
    assert calls["n"] == 2


def test_clear_resets_everything():
    rd.set_provider(lambda *_: None, on_arrival=lambda: None)
    rd.set_reference_bars("yfinance", "SPY", "5m", _bars(2))
    rd.clear()
    assert rd.generation() == 0
    assert rd.get_reference_bars("yfinance", "SPY", "5m") is None  # no provider after clear


def test_concurrent_set_and_get_no_crash():
    """Smoke-level concurrency check: parallel writers don't crash readers."""
    barrier = threading.Barrier(8)
    errors: List[Exception] = []

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=2)
            for j in range(50):
                rd.set_reference_bars("yfinance", f"S{i}", "5m", _bars(2 + j % 3))
                rd.get_reference_bars("yfinance", f"S{i}", "5m")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not errors, errors
    assert rd.generation() == 8 * 50
