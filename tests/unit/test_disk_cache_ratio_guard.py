"""Ratio pseudo-symbols are never persisted to the on-disk candle cache.

A ratio (``AMD/NVDA``) is *derived* from its two legs — which DO cache
individually — so persisting it would (a) require slugging the
filename-illegal ``/`` and (b) risk going stale vs the legs.
``disk_cache.save``/``load`` short-circuit for ratio tickers; the in-memory
``_full_cache`` still provides session-level responsiveness.
"""
from __future__ import annotations

import datetime as dt

import pytest

from tradinglab import disk_cache
from tradinglab.models import Candle


def _candles():
    return [
        Candle(
            date=dt.datetime(2026, 6, 17, 9, 30),
            open=1.0, high=2.0, low=0.5, close=1.5, volume=10, session="regular",
        ),
    ]


@pytest.fixture
def sandbox_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(disk_cache, "_cache_dir", lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize("ratio", ["AMD/NVDA", "amd/nvda", "RSP/SPY"])
def test_ratio_save_is_noop(sandbox_cache, ratio):
    disk_cache.save("yfinance", ratio, "1d", _candles())
    assert list(sandbox_cache.glob("*.jsonl")) == []
    assert disk_cache.load("yfinance", ratio, "1d") is None


def test_ratio_load_ignores_preexisting_file(sandbox_cache):
    # Even if a stale ratio file exists on disk, load short-circuits to None.
    legacy = sandbox_cache / "yfinance__RSP_SPY__1d.jsonl"
    legacy.write_text('{"t":1,"o":1,"h":2,"l":0.5,"c":1.5,"v":10}\n', encoding="utf-8")
    assert disk_cache.load("yfinance", "RSP/SPY", "1d") is None


def test_normal_ticker_still_persists(sandbox_cache):
    disk_cache.save("yfinance", "AMD", "1d", _candles())
    files = [p.name for p in sandbox_cache.glob("*.jsonl")]
    assert files == ["yfinance__AMD__1d.jsonl"]
    loaded = disk_cache.load("yfinance", "AMD", "1d")
    assert loaded is not None and len(loaded) == 1


def test_is_ratio_ticker_predicate():
    assert disk_cache._is_ratio_ticker("AMD/NVDA")
    assert disk_cache._is_ratio_ticker("RSP/SPY")
    assert not disk_cache._is_ratio_ticker("AMD")
    assert not disk_cache._is_ratio_ticker("")
    # Legacy separator-free shorthand is no longer a ratio.
    assert not disk_cache._is_ratio_ticker("RSPSPY")
