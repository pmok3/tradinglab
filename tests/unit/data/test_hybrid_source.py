"""Offline tests for the hybrid (yfinance+alpaca) composite data source.

No network: the composite fetcher's sub-fetchers, deep-cache loader/saver are
injected, so the stitch logic (yfinance recent winning over Alpaca deep) is
exercised deterministically.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tradinglab.data.hybrid_source import (
    HYBRID_SOURCE_NAME,
    fetch_hybrid_data,
    merge_prefer_recent,
)
from tradinglab.models import Candle


def _c(day: int, close: float = 1.0, volume: int = 100) -> Candle:
    return Candle(
        date=datetime(2024, 6, day, tzinfo=timezone.utc),
        open=close, high=close, low=close, close=close, volume=volume,
    )


def _days(candles) -> list[int]:
    return [c.date.day for c in candles]


def _vol_by_day(candles) -> dict[int, int]:
    return {c.date.day: c.volume for c in candles}


# ---------------------------------------------------------------------------
# merge_prefer_recent — yfinance (recent) wins every overlapping bar
# ---------------------------------------------------------------------------


def test_merge_prefer_recent_yfinance_wins_overlap():
    deep = [_c(1, volume=100), _c(2, volume=100), _c(3, volume=100)]      # alpaca IEX
    recent = [_c(2, volume=999), _c(3, volume=999), _c(4, volume=999)]    # yfinance full
    merged = merge_prefer_recent(deep, recent)
    # Union of dates, ascending.
    assert _days(merged) == [1, 2, 3, 4]
    # Overlap days (2, 3) take the RECENT (yfinance) volume; the deep-only
    # tail (day 1) is retained; yfinance-only (day 4) is appended.
    assert _vol_by_day(merged) == {1: 100, 2: 999, 3: 999, 4: 999}


def test_merge_prefer_recent_empty_sides():
    assert merge_prefer_recent([], []) == []
    assert _days(merge_prefer_recent(None, [_c(1)])) == [1]
    assert _days(merge_prefer_recent([_c(1)], None)) == [1]


# ---------------------------------------------------------------------------
# fetch_hybrid_data — stitch, deep-cache reuse, degraded paths
# ---------------------------------------------------------------------------


def test_fetch_stitches_recent_over_deep_and_persists_cold_deep():
    saved: dict[tuple[str, str], list[Candle]] = {}
    deep = [_c(1, volume=100), _c(2, volume=100)]
    recent = [_c(2, volume=999), _c(3, volume=999)]
    out = fetch_hybrid_data(
        "AAPL", "5m",
        recent_fetcher=lambda t, i: list(recent),
        deep_fetcher=lambda t, i: list(deep),
        deep_loader=lambda t, i: None,           # cold cache
        deep_saver=lambda t, i, b: saved.__setitem__((t, i), b),
    )
    assert _days(out) == [1, 2, 3]
    assert _vol_by_day(out) == {1: 100, 2: 999, 3: 999}   # yfinance wins day 2
    assert saved[("AAPL", "5m")] == deep                  # cold deep persisted


def test_fetch_reuses_cached_deep_without_network():
    calls = {"deep": 0}

    def deep_fetcher(t, i):
        calls["deep"] += 1
        return []

    out = fetch_hybrid_data(
        "AAPL", "5m",
        recent_fetcher=lambda t, i: [_c(3, volume=999)],
        deep_fetcher=deep_fetcher,
        deep_loader=lambda t, i: [_c(1, volume=100)],     # warm cache
        deep_saver=lambda t, i, b: None,
    )
    assert calls["deep"] == 0                              # reused disk, no Alpaca hit
    assert _days(out) == [1, 3]


def test_fetch_recent_only_when_no_deep():
    out = fetch_hybrid_data(
        "AAPL", "5m",
        recent_fetcher=lambda t, i: [_c(3, volume=999)],
        deep_fetcher=lambda t, i: [],
        deep_loader=lambda t, i: None,
        deep_saver=lambda t, i, b: None,
    )
    assert _days(out) == [3]


def test_fetch_deep_only_when_yfinance_fails():
    # yfinance hard-fails (None) but Alpaca has history → return the deep data,
    # NOT None (the user still sees a chart).
    out = fetch_hybrid_data(
        "AAPL", "5m",
        recent_fetcher=lambda t, i: None,
        deep_fetcher=lambda t, i: [_c(1, volume=100)],
        deep_loader=lambda t, i: None,
        deep_saver=lambda t, i, b: None,
    )
    assert _days(out) == [1]


def test_fetch_none_when_both_empty_and_yfinance_failed():
    out = fetch_hybrid_data(
        "AAPL", "5m",
        recent_fetcher=lambda t, i: None,
        deep_fetcher=lambda t, i: [],
        deep_loader=lambda t, i: None,
        deep_saver=lambda t, i, b: None,
    )
    assert out is None


def test_fetch_empty_list_when_both_empty_but_yfinance_ok():
    out = fetch_hybrid_data(
        "AAPL", "5m",
        recent_fetcher=lambda t, i: [],
        deep_fetcher=lambda t, i: [],
        deep_loader=lambda t, i: None,
        deep_saver=lambda t, i, b: None,
    )
    assert out == []


def test_ratio_symbol_skips_deep_leg():
    # Ratio pseudo-symbols are a yfinance concept; Alpaca is never queried.
    calls = {"deep": 0}

    def deep_fetcher(t, i):
        calls["deep"] += 1
        return [_c(1, volume=100)]

    out = fetch_hybrid_data(
        "AMD/NVDA", "5m",
        recent_fetcher=lambda t, i: [_c(3, volume=999)],
        deep_fetcher=deep_fetcher,
        deep_loader=lambda t, i: None,
        deep_saver=lambda t, i, b: None,
    )
    assert calls["deep"] == 0
    assert _days(out) == [3]


def test_deep_leg_errors_are_swallowed():
    def boom(t, i):
        raise RuntimeError("network down")

    out = fetch_hybrid_data(
        "AAPL", "5m",
        recent_fetcher=lambda t, i: [_c(3, volume=999)],
        deep_fetcher=boom,
        deep_loader=lambda t, i: None,
        deep_saver=lambda t, i, b: None,
    )
    assert _days(out) == [3]     # recent leg still renders


def test_hybrid_source_name_constant():
    assert HYBRID_SOURCE_NAME == "yfinance+alpaca"
