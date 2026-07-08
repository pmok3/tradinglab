"""Unit tests for the data-source quality/capability model (perf items #1 + #7)."""

from __future__ import annotations

import pytest

from tradinglab.data import quality as q

# ---------------------------------------------------------------------------
# Volume quality + partial-volume warning (perf item #1)
# ---------------------------------------------------------------------------


def test_full_volume_sources_are_not_partial():
    for name in ("yfinance", "schwab", "polygon"):
        assert q.volume_quality(name) == q.VOLUME_FULL
        assert q.is_partial_volume(name) is False
        assert q.partial_volume_warning(name) is None


def test_alpaca_iex_feed_is_partial(monkeypatch):
    class _Creds:
        class alpaca:
            feed = "iex"

    # volume_quality lazily imports get_credentials from .credentials; patch there.
    import tradinglab.data.credentials as creds_mod

    monkeypatch.setattr(creds_mod, "get_credentials", lambda: _Creds)
    assert q.volume_quality("alpaca") == q.VOLUME_PARTIAL
    assert q.is_partial_volume("alpaca") is True
    warn = q.partial_volume_warning("alpaca")
    assert warn is not None and "IEX" in warn and "RVOL" in warn


def test_alpaca_sip_feed_is_full(monkeypatch):
    class _Creds:
        class alpaca:
            feed = "sip"

    import tradinglab.data.credentials as creds_mod

    monkeypatch.setattr(creds_mod, "get_credentials", lambda: _Creds)
    assert q.volume_quality("alpaca") == q.VOLUME_FULL
    assert q.is_partial_volume("alpaca") is False


def test_unknown_source_is_not_partial():
    # Local BYOD / future providers: unknown volume, never false-warns.
    assert q.volume_quality("mydata-sub") == q.VOLUME_UNKNOWN
    assert q.partial_volume_warning("mydata-sub") is None


# ---------------------------------------------------------------------------
# Source ranking (perf item #7)
# ---------------------------------------------------------------------------


def test_rank_intraday_prefers_deep_history_then_volume():
    # Schwab + Polygon (deep + full) beat Alpaca (deep + partial) beat
    # yfinance (full but 60-day intraday cap). Schwab > Polygon on the
    # adjusted tiebreak.
    ranked = q.rank_sources(["yfinance", "alpaca", "schwab", "polygon"], interval="5m")
    assert ranked == ["schwab", "polygon", "alpaca", "yfinance"]


def test_rank_intraday_alpaca_beats_yfinance():
    # The sandbox's core need: deep replayable intraday history. Alpaca's
    # years beat yfinance's ~60-day cap even though Alpaca volume is partial.
    assert q.rank_sources(["yfinance", "alpaca"], interval="5m") == ["alpaca", "yfinance"]


def test_rank_daily_prefers_yfinance_depth():
    # For daily context yfinance's decades win over Alpaca's ~2016 history.
    assert q.rank_sources(["yfinance", "alpaca", "schwab"], interval="1d")[0] == "yfinance"


def test_rank_is_deterministic_and_dedupes():
    a = q.rank_sources(["alpaca", "yfinance", "alpaca"], interval="5m")
    b = q.rank_sources(["yfinance", "alpaca"], interval="5m")
    assert a == b == ["alpaca", "yfinance"]


def test_best_source_and_empty():
    assert q.best_source(["yfinance", "alpaca"], interval="5m") == "alpaca"
    assert q.best_source([], interval="5m") is None


# ---------------------------------------------------------------------------
# preferred_source contract (perf item #7 wiring)
# ---------------------------------------------------------------------------


def test_preferred_upgrades_among_configured_sources():
    # Active is a real, user-visible source → upgrade to the best available.
    assert q.preferred_source(
        "yfinance", interval="5m", candidates=["yfinance", "alpaca"]
    ) == "alpaca"


def test_preferred_respects_non_candidate_active_source():
    # Active is NOT a user-visible source (internal 'synthetic' / a test
    # stub) → returned unchanged so offline/scaffolding flows are untouched.
    assert q.preferred_source(
        "synthetic", interval="5m", candidates=["yfinance", "alpaca"]
    ) == "synthetic"


def test_preferred_single_source_is_noop():
    # The default headless env (only yfinance registered) — no behaviour change.
    assert q.preferred_source(
        "yfinance", interval="5m", candidates=["yfinance"]
    ) == "yfinance"


def test_preferred_defaults_candidates_to_user_visible_sources():
    # With no explicit candidates it consults the live registry. In the test
    # env yfinance is user-visible, so it upgrades to the best of them.
    from tradinglab.data import user_visible_sources

    active = "yfinance"
    expected = q.best_source(user_visible_sources(), interval="5m") or active
    assert q.preferred_source(active, interval="5m") == expected
