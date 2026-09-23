"""Offline tests for the hybrid (yfinance+alpaca) composite data source.

No network: the composite fetcher's sub-fetchers, deep-cache loader/saver are
injected, so the stitch logic (yfinance recent winning over Alpaca deep) is
exercised deterministically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tradinglab import disk_cache
from tradinglab.core.lru_dict import LRUDict
from tradinglab.data import hybrid_source
from tradinglab.data.hybrid_source import (
    HYBRID_SOURCE_NAME,
    _deep_leg_restated,
    fetch_hybrid_data,
    merge_prefer_recent,
)
from tradinglab.models import Candle


@pytest.fixture(autouse=True)
def _isolated_hybrid(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGLAB_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(hybrid_source, "_RECOVERY", LRUDict(maxsize=128))
    monkeypatch.setattr(disk_cache, "_HISTORY_REVISIONS", LRUDict(maxsize=128))
    clock = [100.0]
    monkeypatch.setattr(hybrid_source, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return clock


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
    deep = [_c(d, volume=100) for d in range(1, 9)]
    recent = [_c(d, volume=999) for d in range(4, 11)]
    out = fetch_hybrid_data(
        "AAPL", "5m",
        recent_fetcher=lambda t, i: list(recent),
        deep_fetcher=lambda t, i: list(deep),
        deep_loader=lambda t, i: None,           # cold cache
        deep_saver=lambda t, i, b: saved.__setitem__((t, i), b),
    )
    assert _days(out) == list(range(1, 11))
    assert _vol_by_day(out) == {d: 100 if d < 4 else 999 for d in range(1, 11)}
    assert saved[("AAPL", "5m")] == deep                  # cold deep persisted


def test_fetch_reuses_cached_deep_without_network():
    calls = {"deep": 0}

    def deep_fetcher(t, i):
        calls["deep"] += 1
        return []

    out = fetch_hybrid_data(
        "AAPL", "5m",
        recent_fetcher=lambda t, i: [_c(d, volume=999) for d in range(6, 14)],
        deep_fetcher=deep_fetcher,
        deep_loader=lambda t, i: [_c(d, volume=100) for d in range(1, 11)],
        deep_saver=lambda t, i, b: None,
    )
    assert calls["deep"] == 0                              # reused disk, no Alpaca hit
    assert _days(out) == list(range(1, 14))


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


def test_expired_overlap_revalidates_before_reusing_deep():
    old = _leg(range(1, 11), 400, drift=0)
    recent = _leg(range(20, 31), 100, volume=999, drift=0)
    replacement = _leg(range(1, 31), 100, drift=0)
    saved = []
    out = fetch_hybrid_data(
        "AAPL", "5m", recent_fetcher=lambda *_: recent,
        deep_fetcher=lambda *_: replacement, deep_loader=lambda *_: old,
        deep_saver=lambda t, i, bars: saved.append(bars),
    )
    assert saved == [replacement]
    assert _days(out) == list(range(1, 31))
    assert _max_adjacent_jump([c.close for c in out]) == 0
    assert all(c.volume == 999 for c in out if c.date.day >= 20)


@pytest.mark.parametrize("source", [HYBRID_SOURCE_NAME, "Auto"])
@pytest.mark.parametrize("replacement", [None, [], "unadjusted", "disjoint"])
@pytest.mark.parametrize("recent_start", [15, 21])
def test_failed_replacement_cannot_restore_old_outer_tail(source, replacement, recent_start, caplog):
    old = _leg(range(1, 21), 400, drift=0)
    recent = _leg(range(recent_start, min(recent_start + 11, 31)), 100, drift=0)
    fetched = old if replacement == "unadjusted" else (
        _leg(range(1, 10), 100, drift=0) if replacement == "disjoint" else replacement
    )
    disk_cache.save(source, "AAPL", "5m", old)
    memory = disk_cache.load(source, "AAPL", "5m")
    out = fetch_hybrid_data(
        "AAPL", "5m", recent_fetcher=lambda *_: recent,
        deep_fetcher=lambda *_: fetched, deep_loader=lambda *_: old,
        deep_saver=lambda *_: pytest.fail("unverified replacement must not be saved"),
    )
    assert out == recent
    assert not memory, "in-flight reads and memory-cache snapshots must be fenced"
    assert disk_cache.load(source, "AAPL", "5m") is None
    for previous in (memory, old):
        merged = disk_cache.merge_candles(previous, out)
        assert merged == recent
        assert disk_cache.save(source, "AAPL", "5m", merged)
        assert disk_cache.load(source, "AAPL", "5m") == recent
    assert "withholding deep history" in caplog.text
    assert "verified recent bars only" in disk_cache.history_notice(source, "AAPL", "5m")


def test_delayed_adjustment_backs_off_then_recovers(_isolated_hybrid):
    clock = _isolated_hybrid
    old = _leg(range(1, 21), 400, drift=0)
    good = _leg(range(1, 21), 100, drift=0)
    recent = _leg(range(15, 26), 100, drift=0)
    disk = [old]
    calls = []
    replacement = [old]

    def fetch():
        return fetch_hybrid_data(
            "AAPL", "5m", recent_fetcher=lambda *_: recent,
            deep_fetcher=lambda *_: (calls.append(clock[0]) or replacement[0]),
            deep_loader=lambda *_: disk[0],
            deep_saver=lambda t, i, bars: disk.__setitem__(0, bars) or True,
        )

    assert fetch() == recent
    for clock[0] in (101, 110, 159):
        assert fetch() == recent
    assert calls == [100]
    clock[0] = 160
    assert fetch() == recent
    assert calls == [100, 160]
    replacement[0] = good
    clock[0] = 279
    assert fetch() == recent
    clock[0] = 280
    recovered = fetch()
    assert len(recovered) == 25 and all(c.close == 100 for c in recovered)
    assert disk[0] == good
    assert not disk_cache.history_pending("AAPL", "5m")
    assert disk_cache.history_notice(HYBRID_SOURCE_NAME, "AAPL", "5m") is None
    assert fetch() == recovered
    assert calls == [100, 160, 280]


def test_quarantine_survives_restart_and_recent_outage(monkeypatch):
    old = _leg(range(1, 21), 400, drift=0)
    recent = _leg(range(15, 26), 100, drift=0)
    fetch_hybrid_data(
        "AAPL", "5m", recent_fetcher=lambda *_: recent,
        deep_fetcher=lambda *_: old, deep_loader=lambda *_: old, deep_saver=lambda *_: None,
    )
    monkeypatch.setattr(hybrid_source, "_RECOVERY", LRUDict(maxsize=128))
    monkeypatch.setattr(disk_cache, "_HISTORY_REVISIONS", LRUDict(maxsize=128))
    out = fetch_hybrid_data(
        "AAPL", "5m", recent_fetcher=lambda *_: None,
        deep_fetcher=lambda *_: old, deep_loader=lambda *_: old,
        deep_saver=lambda *_: pytest.fail("outage must not bless quarantined data"),
    )
    assert out is None
    assert disk_cache.history_pending("AAPL", "5m")


def test_none_noop_saver_keeps_quarantine_across_restart_and_yahoo_outage(monkeypatch):
    old = _leg(range(1, 21), 400, drift=0)
    good = _leg(range(1, 21), 100, drift=0)
    recent = _leg(range(15, 26), 100, drift=0)
    disk_cache.save("alpaca", "AAPL", "5m", old)
    out = fetch_hybrid_data(
        "AAPL", "5m", recent_fetcher=lambda *_: recent,
        deep_fetcher=lambda *_: good, deep_saver=lambda *_: None,
    )
    assert out and all(c.close == 100 for c in out), "verified replacement remains usable in memory"
    assert disk_cache.load("alpaca", "AAPL", "5m") == old
    assert disk_cache.history_pending("AAPL", "5m"), "None must not prove that rejected bytes were replaced"
    monkeypatch.setattr(hybrid_source, "_RECOVERY", LRUDict(maxsize=128))
    monkeypatch.setattr(disk_cache, "_HISTORY_REVISIONS", LRUDict(maxsize=128))
    out = fetch_hybrid_data(
        "AAPL", "5m", recent_fetcher=lambda *_: None,
        deep_fetcher=lambda *_: old,
        deep_saver=lambda *_: pytest.fail("restart/outage must not bless the still-rejected file"),
    )
    assert out is None
    assert disk_cache.history_pending("AAPL", "5m")


def test_save_failure_does_not_refetch_verified_replacement_each_poll(caplog):
    old = _leg(range(1, 21), 400, drift=0)
    recent = _leg(range(15, 26), 100, drift=0)
    good = _leg(range(1, 21), 100, drift=0)
    calls = []
    for _ in range(3):
        out = fetch_hybrid_data(
            "AAPL", "5m", recent_fetcher=lambda *_: recent,
            deep_fetcher=lambda *_: (calls.append(1) or good),
            deep_loader=lambda *_: old, deep_saver=lambda *_: False,
        )
        assert len(out) == 25 and all(c.close == 100 for c in out)
    assert calls == [1]
    assert "could not be saved" in caplog.text
    assert disk_cache.history_pending("AAPL", "5m"), "restart must revalidate an unsaved repair"


def test_stale_worker_cannot_overwrite_recovered_history(monkeypatch):
    old = disk_cache.history_snapshot("AAPL", "5m", _leg(range(1, 21), 400, drift=0))
    disk_cache.invalidate_history("AAPL", "5m")
    fresh = disk_cache.history_snapshot("AAPL", "5m", _leg(range(1, 26), 100, drift=0))
    assert disk_cache.save("Auto", "AAPL", "5m", fresh)
    assert not disk_cache.save("Auto", "AAPL", "5m", old)
    assert disk_cache.merge_candles(fresh, old) == fresh
    assert disk_cache.load("Auto", "AAPL", "5m") == fresh
    # Invalidate during serialization, after save's initial check.
    original = disk_cache._candle_to_dict
    invoked = []

    def invalidate_during_write(candle):
        if not invoked:
            invoked.append(True)
            disk_cache.invalidate_history("AAPL", "5m")
        return original(candle)

    monkeypatch.setattr(disk_cache, "_candle_to_dict", invalidate_during_write)
    assert not disk_cache.save("Auto", "AAPL", "5m", fresh)
    assert disk_cache.load("Auto", "AAPL", "5m") is None
    assert list(disk_cache._cache_dir().glob("*.tmp")) == []


def test_invalidation_only_retires_target_pair_and_preserves_normal_merges():
    bars = _leg(range(1, 21), 100, drift=0)
    for key in [(HYBRID_SOURCE_NAME, "MSFT", "5m"), ("Auto", "AAPL", "1d"),
                ("yfinance", "AAPL", "5m"), ("alpaca", "AAPL", "5m")]:
        disk_cache.save(*key, bars)
    disk_cache.invalidate_history("AAPL", "5m")
    for key in [(HYBRID_SOURCE_NAME, "MSFT", "5m"), ("Auto", "AAPL", "1d"),
                ("yfinance", "AAPL", "5m"), ("alpaca", "AAPL", "5m")]:
        assert disk_cache.load(*key) == bars
    recent = disk_cache.history_snapshot("MSFT", "5m", _leg(range(15, 26), 100, drift=0))
    assert len(disk_cache.merge_candles(bars, recent)) == 25


def test_invalid_prices_and_duplicate_dates_do_not_prove_restatement():
    recent = _leg(range(1, 11), 100, drift=0)
    assert not _deep_leg_restated([_c(1, 400)] * 10, recent)
    for price in (float("nan"), float("inf"), 0, -400):
        assert not _deep_leg_restated(_leg(range(1, 11), price, drift=0), recent)
    noisy = [_c(d, 50 if d % 2 else 400) for d in range(1, 11)]
    assert not _deep_leg_restated(noisy, recent), "inconsistent prices are not a coherent split"
    assert not _deep_leg_restated(
        _leg(range(1, 11), 1e308, drift=0), _leg(range(1, 11), 1e-308, drift=0),
    )


def test_recovery_metadata_and_retries_are_bounded(_isolated_hybrid):
    clock = _isolated_hybrid
    old = _leg(range(1, 21), 400, drift=0)
    recent = _leg(range(15, 26), 100, drift=0)
    calls = []
    for _ in range(10):
        fetch_hybrid_data(
            "AAPL", "5m", recent_fetcher=lambda *_: recent,
            deep_fetcher=lambda *_: calls.append(clock[0]),
            deep_loader=lambda *_: old, deep_saver=lambda *_: None,
        )
        recovery = next(iter(hybrid_source._RECOVERY.values()))
        assert 0 < recovery.retry_at - clock[0] <= 1800
        clock[0] = recovery.retry_at
    assert calls[-1] - calls[-2] == 1800
    good = _leg(range(1, 26), 100, drift=0)
    first = None
    for i in range(140):
        out = fetch_hybrid_data(
            f"T{i}", "5m", recent_fetcher=lambda *_: recent,
            deep_fetcher=lambda *_: pytest.fail("compatible history needs no fetch"),
            deep_loader=lambda *_: good, deep_saver=lambda *_: None,
        )
        if first is None:
            first = out
    assert len(hybrid_source._RECOVERY) == len(disk_cache._HISTORY_REVISIONS) == 128
    assert not first


def test_prefetch_persists_interior_only_revision_and_rejects_late_result():
    from tradinglab.data.fetch_service import FetchService

    key = ("Auto", "AAPL", "5m")
    old = disk_cache.history_snapshot("AAPL", "5m", _leg(range(1, 26), 100, drift=0))
    disk_cache.save(*key, old)
    memory = {key: old}
    revised = list(old)
    revised[0] = _c(1, 101)
    fresh = disk_cache.history_snapshot("AAPL", "5m", revised)
    assert not disk_cache.merge_adds_nothing(old, fresh)
    service = FetchService(worker_count=1)
    try:
        merged = service.apply_prefetch_result(
            key, fresh, memory, disk_cache, lambda k, bars: memory.__setitem__(k, bars),
        )
        assert merged == revised == disk_cache.load(*key)
        disk_cache.invalidate_history("AAPL", "5m")
        newest = disk_cache.history_snapshot("AAPL", "5m", _leg(range(15, 26), 25, drift=0))
        memory[key] = newest
        disk_cache.save(*key, newest)
        assert service.apply_prefetch_result(
            key, old, memory, disk_cache, lambda k, bars: memory.__setitem__(k, bars),
        ) is None
        assert memory[key] == disk_cache.load(*key) == newest
    finally:
        service.shutdown()


@pytest.mark.parametrize("symbol,changed", [("AAPL/4", "AAPL"), ("AAPL/MSFT", "AAPL"),
                                         ("AAPL/MSFT", "MSFT")])
def test_registered_ratio_and_scaled_memory_follow_either_leg_invalidation(monkeypatch, symbol, changed):
    from tradinglab.data.base import DATA_SOURCES, _ratio_aware

    bars = {
        "AAPL": disk_cache.history_snapshot("AAPL", "5m", _leg(range(1, 26), 400, drift=0)),
        "MSFT": disk_cache.history_snapshot("MSFT", "5m", _leg(range(1, 26), 100, drift=0)),
    }
    monkeypatch.setitem(DATA_SOURCES, HYBRID_SOURCE_NAME,
                        _ratio_aware(lambda ticker, _: bars[ticker], HYBRID_SOURCE_NAME))
    old = DATA_SOURCES[HYBRID_SOURCE_NAME](symbol, "5m")
    assert old and isinstance(old, disk_cache.HistorySnapshot)
    copied = disk_cache.copy_candles(old)
    disk_cache.invalidate_history(changed, "5m")
    assert not old and not copied
    bars[changed] = disk_cache.history_snapshot(changed, "5m", _leg(range(15, 26), 50, drift=0))
    new = DATA_SOURCES[HYBRID_SOURCE_NAME](symbol, "5m")
    assert new and len(new) == 11
    assert disk_cache.merge_candles(old, new) == new
    assert disk_cache.load(HYBRID_SOURCE_NAME, symbol, "5m") is None


@pytest.mark.parametrize("first_result", [False, None])
def test_failed_deep_save_retries_without_refetch_then_clears_quarantine(_isolated_hybrid, first_result, caplog):
    clock = _isolated_hybrid
    calls, saves = [], []
    old = _leg(range(1, 21), 400, drift=0)
    good = _leg(range(1, 21), 100, drift=0)
    recent = _leg(range(15, 26), 100, drift=0)

    def saver(*_):
        saves.append(clock[0])
        return True if len(saves) > 1 else first_result

    for clock[0] in (100, 101, 159, 160):
        out = fetch_hybrid_data(
            "AAPL", "5m", recent_fetcher=lambda *_: recent,
            deep_fetcher=lambda *_: (calls.append(1) or good),
            deep_loader=lambda *_: old, deep_saver=saver,
        )
        assert len(out) == 25
    assert calls == [1]
    assert saves == [100, 160]
    assert not disk_cache.history_pending("AAPL", "5m")
    if first_result is None:
        assert "persistence is unconfirmed" in caplog.text


def test_failed_file_retirement_remains_blocked_across_restart(monkeypatch, caplog):
    from pathlib import Path

    old = _leg(range(1, 21), 400, drift=0)
    disk_cache.save("Auto", "AAPL", "5m", old)
    path = disk_cache._path_for("Auto", "AAPL", "5m")
    unlink = Path.unlink

    def deny_retirement(self, *args, **kwargs):
        if self == path:
            raise PermissionError("file busy")
        return unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_retirement)
    disk_cache.invalidate_history("AAPL", "5m")
    assert path.exists()
    assert disk_cache.load("Auto", "AAPL", "5m") is None
    monkeypatch.setattr(disk_cache, "_HISTORY_REVISIONS", LRUDict(maxsize=128))
    assert disk_cache.load("Auto", "AAPL", "5m") is None
    assert "Cannot retire Auto history" in caplog.text


def test_concurrent_fetches_serialize_observations_and_fence_old_result():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    entered, release, second_started = Event(), Event(), Event()
    old = _leg(range(1, 21), 400, drift=0)
    recent_old = _leg(range(15, 26), 400, drift=0)
    recent_new = _leg(range(15, 26), 100, drift=0)
    new = _leg(range(1, 21), 100, drift=0)
    calls = []

    def old_recent(*_):
        entered.set()
        assert release.wait(5)
        return recent_old

    def invoke(recent_fetcher):
        return fetch_hybrid_data(
            "AAPL", "5m", recent_fetcher=recent_fetcher,
            deep_fetcher=lambda *_: (calls.append(1) or new),
            deep_loader=lambda *_: old, deep_saver=lambda *_: None,
        )

    def second():
        second_started.set()
        return invoke(lambda *_: recent_new)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(invoke, old_recent)
        try:
            assert entered.wait(5)
            later = executor.submit(second)
            assert second_started.wait(5)
        finally:
            release.set()
        old_result, new_result = first.result(timeout=5), later.result(timeout=5)
    assert calls == [1]
    assert not old_result
    assert new_result and all(c.close == 100 for c in new_result)
    assert disk_cache.merge_candles(new_result, old_result) == new_result
