"""Tests for the "Auto" data source (resolve-to-best + delegating fetcher)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradinglab.data import auto_source as a
from tradinglab.data.base import DATA_SOURCES
from tradinglab.models import Candle


def _candle() -> Candle:
    return Candle(
        date=datetime(2024, 6, 3, tzinfo=timezone.utc),
        open=1.0, high=1.0, low=1.0, close=1.0, volume=100,
    )


# ---------------------------------------------------------------------------
# resolve_auto_source — best concrete source, excluding "Auto" itself
# ---------------------------------------------------------------------------


def test_resolve_excludes_self_and_falls_back():
    # Only "Auto" available → nothing real → fallback yfinance.
    assert a.resolve_auto_source(candidates=["Auto"]) == "yfinance"
    assert a.resolve_auto_source(candidates=[]) == "yfinance"


def test_resolve_yfinance_only():
    assert a.resolve_auto_source(candidates=["Auto", "yfinance"]) == "yfinance"


def test_resolve_prefers_hybrid_over_yfinance():
    # Tier-independent: the hybrid outranks plain yfinance.
    assert a.resolve_auto_source(
        candidates=["Auto", "yfinance", "yfinance+alpaca"]) == "yfinance+alpaca"


def test_resolve_alpaca_paid_is_best(monkeypatch):
    monkeypatch.setattr("tradinglab.data.alpaca_source.is_live_capable", lambda: True)
    assert a.resolve_auto_source(
        candidates=["Auto", "yfinance", "alpaca"]) == "alpaca"


def test_resolve_alpaca_free_loses_to_yfinance(monkeypatch):
    monkeypatch.setattr("tradinglab.data.alpaca_source.is_live_capable", lambda: False)
    assert a.resolve_auto_source(
        candidates=["Auto", "yfinance", "alpaca"]) == "yfinance"


# ---------------------------------------------------------------------------
# fetch_auto_data — dispatch through DATA_SOURCES to the resolved best
# ---------------------------------------------------------------------------


def test_fetch_delegates_to_resolved_best(monkeypatch):
    calls: list[tuple[str, str]] = []

    def _fake(ticker, interval):
        calls.append((ticker, interval))
        return [_candle()]

    monkeypatch.setitem(DATA_SOURCES, "faketest", _fake)
    monkeypatch.setattr(a, "resolve_auto_source", lambda: "faketest")
    out = a.fetch_auto_data("AAPL", "5m")
    assert out is not None and len(out) == 1
    assert calls == [("AAPL", "5m")]


def test_fetch_none_when_best_unregistered(monkeypatch):
    monkeypatch.setattr(a, "resolve_auto_source", lambda: "nonexistent_source")
    assert a.fetch_auto_data("AAPL", "5m") is None


def test_fetch_swallows_delegate_error(monkeypatch):
    def _boom(ticker, interval):
        raise RuntimeError("network down")

    monkeypatch.setitem(DATA_SOURCES, "faketest", _boom)
    monkeypatch.setattr(a, "resolve_auto_source", lambda: "faketest")
    assert a.fetch_auto_data("AAPL", "5m") is None


def test_fetch_guards_against_self_dispatch(monkeypatch):
    # A degenerate resolve returning "Auto" must NOT recurse; falls back.
    monkeypatch.setattr(a, "resolve_auto_source", lambda: a.AUTO_SOURCE_NAME)
    stub_calls: list[str] = []
    monkeypatch.setitem(
        DATA_SOURCES, "yfinance",
        lambda t, i: stub_calls.append("yf") or [_candle()])
    out = a.fetch_auto_data("AAPL", "5m")
    assert out is not None
    assert stub_calls == ["yf"]     # dispatched to the yfinance fallback


# ---------------------------------------------------------------------------
# Registration + default wiring
# ---------------------------------------------------------------------------


def test_auto_is_registered_and_visible_but_not_first():
    from tradinglab.data import user_visible_sources

    assert "Auto" in DATA_SOURCES
    visible = user_visible_sources()
    assert visible[0] == "yfinance"     # yfinance stays the first-visible source
    assert "Auto" in visible


def test_builtin_startup_default_is_auto():
    from tradinglab.constants import BUILTIN_STARTUP_DEFAULTS

    assert BUILTIN_STARTUP_DEFAULTS["source"] == a.AUTO_SOURCE_NAME


def test_resolve_source_env_pin_overrides_auto(monkeypatch):
    from tradinglab.gui.app_state import AppState

    monkeypatch.setenv("TRADINGLAB_STARTUP_SOURCE", "yfinance")
    assert AppState._resolve_source({"source": "Auto"}) == "yfinance"
    # Cleared → the persisted "Auto" default is honoured.
    monkeypatch.delenv("TRADINGLAB_STARTUP_SOURCE", raising=False)
    assert AppState._resolve_source({"source": "Auto"}) == "Auto"


def test_resolve_source_env_pin_ignores_internal_or_unregistered(monkeypatch):
    from tradinglab.gui.app_state import AppState

    # An internal or unregistered env value is ignored (falls through).
    monkeypatch.setenv("TRADINGLAB_STARTUP_SOURCE", "synthetic")
    assert AppState._resolve_source({"source": "Auto"}) == "Auto"
    monkeypatch.setenv("TRADINGLAB_STARTUP_SOURCE", "no_such_source")
    assert AppState._resolve_source({"source": "Auto"}) == "Auto"
