"""Unit tests for ``data.prefetch.tiers`` — the relevance ladder.

Contract (design §5, Decisions 5/15): given a frozen ``PrefetchContext`` snapshot
of the app state, expand each registered ``TierProvider`` (gap-ranked: active 10
< compare 20 < focused-watchlist 30 < other-watchlists 40 < universe 90) into
band-0 ``FetchJob``s, applying the shared dual-interval policy per symbol and
**dedup-by-highest-tier** (a symbol in several tiers is fetched once, at its
highest tier).
"""
from __future__ import annotations

import pytest

from tradinglab.data.prefetch.tiers import (
    PrefetchContext,
    TierProvider,
    expand_all,
    standard_tiers,
)


def _ctx(**kw) -> PrefetchContext:
    base = dict(
        source="alpaca", active_symbol="AMD", active_interval="5m",
        compare_symbol="SPY", focused_watchlist=("NVDA", "INTC"),
        other_watchlists=("MSFT",), universe=("AMD", "SPY", "TSLA"),
    )
    base.update(kw)
    return PrefetchContext(**base)


def test_standard_tiers_ranks_and_names():
    tiers = standard_tiers()
    assert [t.rank for t in tiers] == [10, 20, 30, 40, 90]
    assert [t.name for t in tiers] == [
        "active", "compare", "focused_watchlist", "other_watchlists", "universe",
    ]


def test_active_dual_interval_band0():
    jobs = expand_all(standard_tiers(), _ctx())
    amd = [(j.tier_rank, j.interval, j.interval_rank, j.band_index)
           for j in jobs if j.symbol == "AMD"]
    assert amd == [(10, "5m", 0, 0), (10, "1d", 1, 0)]


def test_dedup_by_highest_tier():
    jobs = expand_all(standard_tiers(), _ctx())
    assert {j.tier_rank for j in jobs if j.symbol == "AMD"} == {10}   # active, not universe
    assert {j.tier_rank for j in jobs if j.symbol == "SPY"} == {20}   # compare, not universe
    assert {j.tier_rank for j in jobs if j.symbol == "TSLA"} == {90}  # only universe


def test_empty_compare_skips_tier():
    jobs = expand_all(standard_tiers(), _ctx(compare_symbol=""))
    assert not any(j.tier_rank == 20 for j in jobs)


def test_symbol_normalization_upper_strip():
    jobs = expand_all(standard_tiers(),
                      _ctx(active_symbol="  amd ", compare_symbol="spy"))
    assert any(j.symbol == "AMD" and j.tier_rank == 10 for j in jobs)
    assert any(j.symbol == "SPY" and j.tier_rank == 20 for j in jobs)


def test_generation_stamped_per_tier():
    gen = {10: 5, 20: 6, 30: 7, 40: 8, 90: 9}
    jobs = expand_all(standard_tiers(), _ctx(), gen_of=lambda r: gen[r])
    for j in jobs:
        assert j.generation == gen[j.tier_rank]


def test_all_jobs_are_band_zero():
    assert all(j.band_index == 0 for j in expand_all(standard_tiers(), _ctx()))


def test_watchlist_dual_interval():
    jobs = expand_all(standard_tiers(), _ctx())
    nvda = [(j.interval, j.interval_rank) for j in jobs if j.symbol == "NVDA"]
    assert nvda == [("5m", 0), ("1d", 1)]
    assert all(j.tier_rank == 30 for j in jobs if j.symbol == "NVDA")


def test_seq_is_monotonic_and_unique():
    seqs = [j.seq for j in expand_all(standard_tiers(), _ctx())]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_duplicate_symbol_within_a_tier_is_deduped():
    jobs = expand_all(standard_tiers(),
                      _ctx(focused_watchlist=("NVDA", "NVDA", "INTC")))
    nvda_5m = [j for j in jobs if j.symbol == "NVDA" and j.interval == "5m"]
    assert len(nvda_5m) == 1


def test_custom_interval_policy_override():
    tiers = standard_tiers()
    uni = next(t for t in tiers if t.name == "universe")
    uni_1d = TierProvider(rank=90, name="universe", symbols=uni.symbols,
                          interval_policy=lambda ctx, sym: ["1d"])
    others = [t for t in tiers if t.name != "universe"]
    jobs = expand_all([*others, uni_1d], _ctx(universe=("TSLA",)))
    tsla = [(j.interval, j.interval_rank) for j in jobs if j.symbol == "TSLA"]
    assert tsla == [("1d", 0)]


def test_context_is_frozen():
    ctx = _ctx()
    with pytest.raises((AttributeError, TypeError)):
        ctx.active_symbol = "X"  # type: ignore[misc]


def test_active_1d_orders_5m_second():
    jobs = expand_all(standard_tiers(), _ctx(active_interval="1d"))
    amd = [(j.interval, j.interval_rank) for j in jobs if j.symbol == "AMD"]
    assert amd == [("1d", 0), ("5m", 1)]
