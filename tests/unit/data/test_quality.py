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
# Ranking shims → data/source_ranking (perf item #7 / global priority)
# ---------------------------------------------------------------------------


def test_ranking_shims_delegate_to_source_ranking():
    # quality.* ranking helpers are now thin back-compat shims over the global,
    # tier-aware ranking in data/source_ranking (full coverage lives in
    # test_source_ranking.py). They accept ``interval`` for back-compat but it
    # no longer affects the order (the global priority is interval-independent).
    assert q.rank_sources(["yfinance", "alpaca"], interval="5m") == (
        q.rank_sources(["yfinance", "alpaca"], interval="1d"))
    # Non-candidate active source is returned unchanged (contract preserved).
    assert q.preferred_source(
        "synthetic", interval="5m", candidates=["yfinance", "alpaca"]) == "synthetic"
    assert q.best_source([], interval="5m") is None


def test_hybrid_volume_is_full():
    # The visible/recent window is yfinance → full volume, no partial warning.
    assert q.volume_quality("yfinance+alpaca") == q.VOLUME_FULL
    assert q.is_partial_volume("yfinance+alpaca") is False
    assert q.partial_volume_warning("yfinance+alpaca") is None


# ---------------------------------------------------------------------------
# source_capability_line — the shared source-picker hint
# ---------------------------------------------------------------------------


def test_capability_line_states_reach_and_volume_tier():
    """Both source dropdowns (Start Sandbox Session, Prepare Universe
    Data) render this one line, so it must carry the two facts that
    actually decide the choice: how far back the source reaches, and
    whether its volume is the real tape."""
    line = q.source_capability_line("yfinance")
    assert line.startswith("yfinance:")
    assert "60d intraday" in line
    assert "30y daily" in line
    assert "full consolidated volume" in line


def test_capability_line_is_feed_aware_for_alpaca(monkeypatch):
    """Alpaca's tier is resolved live from the configured feed, so the
    hint must say PARTIAL on the free IEX feed and not on SIP."""
    class _Creds:
        class alpaca:
            feed = "iex"

    import tradinglab.data.credentials as creds_mod

    monkeypatch.setattr(creds_mod, "get_credentials", lambda: _Creds)
    assert "PARTIAL" in q.source_capability_line("alpaca")

    _Creds.alpaca.feed = "sip"
    assert "full consolidated volume" in q.source_capability_line("alpaca")


def test_capability_line_blank_for_empty_name():
    """Decoration, never a gate — an empty/unknown name yields no text
    rather than raising into a dialog build."""
    assert q.source_capability_line("") == ""
    assert q.source_capability_line("   ") == ""


def test_capability_line_handles_unknown_source():
    """An unregistered name still renders (via the default descriptor)
    so a pinned-but-unknown source doesn't blank the hint."""
    line = q.source_capability_line("totally-unknown")
    assert line.startswith("totally-unknown:")
