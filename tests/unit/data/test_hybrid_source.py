"""Offline tests for the hybrid (yfinance+alpaca) composite data source.

No network: the composite fetcher's sub-fetchers, deep-cache loader/saver are
injected, so the stitch logic (yfinance recent winning over Alpaca deep) is
exercised deterministically.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tradinglab.data.hybrid_source import (
    HYBRID_SOURCE_NAME,
    _deep_leg_restated,
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


# ---------------------------------------------------------------------------
# Split / restatement invalidation — the deep leg is revalidated against the
# fresh recent leg on overlapping bars every fetch.
# ---------------------------------------------------------------------------


def _leg(days, base_close: float, volume: int = 100, drift: float = 0.002):
    """Build a leg of candles drifting ``drift``/bar — small realistic moves,
    no artificial cliffs inside the leg itself."""
    return [
        _c(day, close=base_close * (1 + drift * i), volume=volume)
        for i, day in enumerate(days)
    ]


def _max_adjacent_jump(closes: list[float]) -> float:
    """Largest bar-to-bar relative move — the seam-cliff detector."""
    return max(abs(b - a) / a for a, b in zip(closes, closes[1:], strict=False) if a)


def test_split_restatement_invalidates_deep_and_heals_seam():
    # 4:1 split discovered on a routine poll: the deep cache is pre-split
    # (~400), the fresh yfinance leg is restated post-split (~100).
    pre_split_deep = _leg(range(1, 21), base_close=400.0)      # days 1..20
    post_split_recent = _leg(range(15, 26), base_close=100.0)  # days 15..25
    post_split_deep = _leg(range(1, 21), base_close=100.0)     # Alpaca, restated

    saved: dict[tuple[str, str], list[Candle]] = {}
    calls = {"deep": 0}

    def deep_fetcher(t, i):
        calls["deep"] += 1
        return list(post_split_deep)

    out = fetch_hybrid_data(
        "AAPL", "1d",
        recent_fetcher=lambda t, i: list(post_split_recent),
        deep_fetcher=deep_fetcher,
        deep_loader=lambda t, i: list(pre_split_deep),        # stale cache
        deep_saver=lambda t, i, b: saved.__setitem__((t, i), b),
    )

    assert calls["deep"] == 1, "stale pre-split deep leg must be refetched"
    assert saved[("AAPL", "1d")] == post_split_deep  # restated deep persisted

    closes = [c.close for c in out]
    # Downstream effect: NO artificial cliff at the seam — every adjacent
    # move is small (the buggy version leaves a ~75% drop at day 14 -> 15).
    assert _max_adjacent_jump(closes) < 0.10
    assert max(closes) < 150.0  # whole series in post-split scale


def test_no_restatement_no_refetch():
    # Ordinary day, no split: same-scale legs with small vendor drift must
    # reuse the warm deep cache — no spurious Alpaca refetch.
    deep = _leg(range(1, 21), base_close=100.0)
    recent = _leg(range(15, 26), base_close=100.0)
    calls = {"deep": 0}

    def deep_fetcher(t, i):
        calls["deep"] += 1
        return []

    out = fetch_hybrid_data(
        "AAPL", "1d",
        recent_fetcher=lambda t, i: list(recent),
        deep_fetcher=deep_fetcher,
        deep_loader=lambda t, i: list(deep),                  # warm cache
        deep_saver=lambda t, i, b: None,
    )
    assert calls["deep"] == 0, "no restatement -> no deep refetch"
    assert _days(out) == list(range(1, 26))
    assert _max_adjacent_jump([c.close for c in out]) < 0.10


def test_both_orderings_converge_to_same_continuous_series():
    # Order A: the recent-leg refresh sees the split first (stale deep cache
    # -> heal fires on this poll). Order B: the deep leg is already restated
    # when the recent refresh arrives (cold-fetched post-split). Both must
    # converge to the SAME continuous post-split series, and the healed
    # cache must stay stable on the next poll (no flip-flopping).
    pre_split_deep = _leg(range(1, 21), base_close=400.0)
    post_split_deep = _leg(range(1, 21), base_close=100.0)
    post_split_recent = _leg(range(15, 26), base_close=100.0)

    # --- Order A: stale cache + fresh recent -> heal fires ---
    disk_a: dict[tuple[str, str], list[Candle]] = {}
    calls_a = {"deep": 0}

    def deep_fetcher_a(t, i):
        calls_a["deep"] += 1
        return list(post_split_deep)

    out_a = fetch_hybrid_data(
        "AAPL", "1d",
        recent_fetcher=lambda t, i: list(post_split_recent),
        deep_fetcher=deep_fetcher_a,
        deep_loader=lambda t, i: list(pre_split_deep),
        deep_saver=lambda t, i, b: disk_a.__setitem__((t, i), b),
    )

    # --- Order B: deep cache already restated, then the recent refresh ---
    calls_b = {"deep": 0}

    def deep_fetcher_b(t, i):
        calls_b["deep"] += 1
        return list(post_split_deep)  # must never be called

    out_b = fetch_hybrid_data(
        "AAPL", "1d",
        recent_fetcher=lambda t, i: list(post_split_recent),
        deep_fetcher=deep_fetcher_b,
        deep_loader=lambda t, i: list(post_split_deep),
        deep_saver=lambda t, i, b: None,
    )

    assert calls_a["deep"] == 1
    assert calls_b["deep"] == 0, "already-restated deep leg must not refetch"

    closes_a = [c.close for c in out_a]
    closes_b = [c.close for c in out_b]
    assert closes_a == closes_b, "both orderings converge to the same series"
    assert _max_adjacent_jump(closes_a) < 0.10
    assert _max_adjacent_jump(closes_b) < 0.10

    # Next poll with the healed cache: stable, no second refetch.
    out_a2 = fetch_hybrid_data(
        "AAPL", "1d",
        recent_fetcher=lambda t, i: list(post_split_recent),
        deep_fetcher=deep_fetcher_a,
        deep_loader=lambda t, i: disk_a[("AAPL", "1d")],
        deep_saver=lambda t, i, b: None,
    )
    assert calls_a["deep"] == 1, "healed deep leg must not refetch again"
    assert [c.close for c in out_a2] == closes_a


# ---------------------------------------------------------------------------
# _deep_leg_restated — detector unit tests
# ---------------------------------------------------------------------------


def test_detector_flags_forward_split():
    cached = _leg(range(1, 11), base_close=400.0)
    fresh = _leg(range(1, 11), base_close=100.0)   # 4:1 split
    assert _deep_leg_restated(cached, fresh) is True


def test_detector_flags_reverse_split():
    cached = _leg(range(1, 11), base_close=10.0)
    fresh = _leg(range(1, 11), base_close=100.0)   # 1:10 reverse split
    assert _deep_leg_restated(cached, fresh) is True


def test_detector_ignores_small_vendor_drift():
    cached = _leg(range(1, 11), base_close=100.0)
    fresh = _leg(range(1, 11), base_close=102.0)   # 2% systematic drift
    assert _deep_leg_restated(cached, fresh) is False


def test_detector_needs_minimum_overlap():
    cached = _leg(range(1, 11), base_close=400.0)
    fresh = _leg(range(9, 13), base_close=100.0)   # only 2 overlapping bars
    assert _deep_leg_restated(cached, fresh) is False


def test_detector_no_overlap_no_verdict():
    cached = _leg(range(1, 11), base_close=400.0)
    fresh = _leg(range(20, 26), base_close=100.0)  # disjoint timestamps
    assert _deep_leg_restated(cached, fresh) is False


def test_detector_empty_legs():
    assert _deep_leg_restated([], _leg(range(1, 11), base_close=100.0)) is False
    assert _deep_leg_restated(_leg(range(1, 11), base_close=100.0), []) is False
