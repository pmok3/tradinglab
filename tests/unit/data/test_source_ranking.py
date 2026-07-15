"""Unit tests for the global, tier-aware source priority ranking.

Pins the owner's fixed order (``alpaca paid > schwab > yfinance > alpaca free``)
and the module contract. Alpaca's tier is injected via ``alpaca_paid`` so the
tests are deterministic offline (no credential reads).
"""

from __future__ import annotations

from tradinglab.data import source_ranking as sr

# All real registered sources + a BYOD/local (unlisted) source.
_ALL = ["yfinance", "alpaca", "schwab", "polygon", "yfinance+alpaca", "local-mydata"]


# ---------------------------------------------------------------------------
# The owner's stated global order (tier-aware)
# ---------------------------------------------------------------------------


def test_owner_pairwise_order_holds():
    # alpaca(paid) > schwab > yfinance > alpaca(free) — lower rank = better.
    r_paid_alpaca = sr.global_rank("alpaca", alpaca_paid=True)
    r_schwab = sr.global_rank("schwab")
    r_yf = sr.global_rank("yfinance")
    r_free_alpaca = sr.global_rank("alpaca", alpaca_paid=False)
    assert r_paid_alpaca < r_schwab < r_yf < r_free_alpaca


def test_rank_all_paid_alpaca_is_best():
    ranked = sr.rank_sources(_ALL, alpaca_paid=True)
    assert ranked[0] == "alpaca"
    # BYOD/unlisted always trails the named sources.
    assert ranked[-1] == "local-mydata"


def test_rank_all_free_alpaca_is_worst_real_source():
    ranked = sr.rank_sources(_ALL, alpaca_paid=False)
    assert ranked[0] == "schwab"                 # best real source when alpaca is free
    # free alpaca sits below every other real source, above only unlisted BYOD.
    assert ranked.index("alpaca") == len(ranked) - 2
    assert ranked[-1] == "local-mydata"


def test_tier_flip_changes_best_between_yfinance_and_alpaca():
    assert sr.best_source(["yfinance", "alpaca"], alpaca_paid=True) == "alpaca"
    assert sr.best_source(["yfinance", "alpaca"], alpaca_paid=False) == "yfinance"


def test_hybrid_ranks_just_above_yfinance():
    assert sr.global_rank("yfinance+alpaca") < sr.global_rank("yfinance")
    # …and below the full-volume deep vendors.
    assert sr.global_rank("schwab") < sr.global_rank("yfinance+alpaca")
    assert sr.global_rank("polygon") < sr.global_rank("yfinance+alpaca")


# ---------------------------------------------------------------------------
# Token resolution + tier resolution
# ---------------------------------------------------------------------------


def test_resolve_priority_token_tier_aware_for_alpaca():
    assert sr.resolve_priority_token("alpaca", alpaca_paid=True) == "alpaca@paid"
    assert sr.resolve_priority_token("alpaca", alpaca_paid=False) == "alpaca@free"
    # Case + whitespace normalised.
    assert sr.resolve_priority_token("  ALPACA ", alpaca_paid=False) == "alpaca@free"


def test_resolve_priority_token_passthrough_for_others():
    for name in ("yfinance", "schwab", "polygon", "yfinance+alpaca", "local-x"):
        assert sr.resolve_priority_token(name) == name


def test_default_tier_resolves_via_is_live_capable(monkeypatch):
    # With no explicit alpaca_paid, the tier comes from alpaca_source.is_live_capable.
    monkeypatch.setattr("tradinglab.data.alpaca_source.is_live_capable", lambda: True)
    assert sr.resolve_priority_token("alpaca") == "alpaca@paid"
    monkeypatch.setattr("tradinglab.data.alpaca_source.is_live_capable", lambda: False)
    assert sr.resolve_priority_token("alpaca") == "alpaca@free"


# ---------------------------------------------------------------------------
# Unlisted sources, dedupe, determinism
# ---------------------------------------------------------------------------


def test_unlisted_sources_trail_named_and_sort_by_name():
    ranked = sr.rank_sources(["local-z", "yfinance", "local-a"])
    assert ranked[0] == "yfinance"
    # Two unlisted sources tie on rank → deterministic name order.
    assert ranked[1:] == ["local-a", "local-z"]


def test_rank_dedupes_case_insensitively():
    ranked = sr.rank_sources(["yfinance", "YFinance", "alpaca"], alpaca_paid=False)
    assert ranked == ["yfinance", "alpaca"]


def test_rank_is_deterministic():
    a = sr.rank_sources(_ALL, alpaca_paid=True)
    b = sr.rank_sources(list(reversed(_ALL)), alpaca_paid=True)
    assert a == b


def test_best_source_empty_is_none():
    assert sr.best_source([]) is None


# ---------------------------------------------------------------------------
# preferred_source contract (respect explicit non-standard choices)
# ---------------------------------------------------------------------------


def test_preferred_upgrades_among_candidates():
    assert sr.preferred_source(
        "yfinance", candidates=["yfinance", "alpaca"], alpaca_paid=True
    ) == "alpaca"


def test_preferred_respects_non_candidate_active():
    # Internal/scaffolding source not in candidates → returned unchanged.
    assert sr.preferred_source(
        "synthetic", candidates=["yfinance", "alpaca"], alpaca_paid=True
    ) == "synthetic"


def test_preferred_single_candidate_is_noop():
    assert sr.preferred_source("yfinance", candidates=["yfinance"]) == "yfinance"


def test_preferred_defaults_candidates_via_injected_fn():
    called = {"n": 0}

    def _fn():
        called["n"] += 1
        return ["yfinance", "alpaca"]

    out = sr.preferred_source("yfinance", candidates_fn=_fn, alpaca_paid=True)
    assert out == "alpaca"
    assert called["n"] == 1


# ---------------------------------------------------------------------------
# The priority tuple itself
# ---------------------------------------------------------------------------


def test_global_priority_tuple_encodes_owner_order():
    p = sr.GLOBAL_SOURCE_PRIORITY
    assert p.index("alpaca@paid") < p.index("schwab") < p.index("yfinance") < p.index(
        "alpaca@free")
