"""Committed real-market-data test source (``testdata``).

Loads the small, sealed 5-minute OHLCV snapshot captured by
``tools/fetch_test_fixtures.py`` (5 RTH trading days of SPY / AMD / NVDA /
INTC / MSFT / AAPL, sourced from yfinance) so the end-to-end strategy-tester
flow can run against **real** market microstructure — genuine EMA crosses,
opening gaps, RTH boundaries, real volume — rather than only engineered
synthetic candles.

This is a *distinguished test data source*: a dedicated ``testdata`` namespace
with its own committed JSONL fixtures (``testdata__<TICKER>__5m.jsonl``),
wired at the TEST level (via :func:`fetcher`, injected as a StrategyTab
``candles_fetcher``) rather than registered into the app's ``DATA_SOURCES`` —
so the real data lives under ``tests/`` and never ships in the frozen exe.

The fixtures use the same JSONL shape as the live disk cache
(``disk_cache._candle_to_dict``) and rehydrate through the same tested
``_candle_from_dict`` deserialiser.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path

from tradinglab.disk_cache import _candle_from_dict
from tradinglab.models import Candle

#: The distinguished source namespace (matches the fixture filename prefix).
SOURCE = "testdata"
#: The only interval captured.
INTERVAL = "5m"
#: Tickers present in the committed snapshot (see tools/fetch_test_fixtures.py).
TICKERS: tuple[str, ...] = ("SPY", "AMD", "NVDA", "INTC", "MSFT", "AAPL")

_DATA_DIR = Path(__file__).resolve().parent / "market_data"


def _path_for(ticker: str, interval: str) -> Path:
    return _DATA_DIR / f"{SOURCE}__{ticker.upper()}__{interval}.jsonl"


def available(ticker: str = "SPY", interval: str = INTERVAL) -> bool:
    """True if the committed fixture for ``ticker`` / ``interval`` exists."""
    return _path_for(ticker, interval).is_file()


@functools.cache
def load(ticker: str, interval: str = INTERVAL) -> tuple[Candle, ...]:
    """Return the committed candles for ``ticker`` / ``interval`` (cached).

    Empty tuple if the fixture is missing (so callers can gate on it). The
    result is a tuple so the ``lru_cache`` value is immutable — copy to a
    list before mutating.
    """
    path = _path_for(ticker, interval)
    if not path.is_file():
        return ()
    out: list[Candle] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            c = _candle_from_dict(json.loads(line))
            if c is not None:
                out.append(c)
    return tuple(out)


def fetcher(ticker: str, interval: str) -> list[Candle] | None:
    """``candles_fetcher``-compatible callable over the committed fixtures.

    Returns a fresh list of candles for a known ticker at the captured
    interval, else ``None`` (so a strategy run on an unknown symbol / interval
    fails the same way a live source would). Ratio symbols are NOT handled
    here — inject via ``data.base``'s ratio-aware wrapper if a ratio fixture
    is ever needed.
    """
    if interval != INTERVAL:
        return None
    candles = load(ticker.upper(), interval)
    return list(candles) if candles else None


def manifest() -> dict:
    """Return the fixture provenance manifest (source, capture date, per-ticker
    day range + bar counts), or ``{}`` if absent."""
    path = _DATA_DIR / "manifest.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
