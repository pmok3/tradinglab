"""Unit tests for ``data.prefetch.appglue`` — pure ChartApp-integration helpers.

The flag readers gate the whole feature (default OFF → zero change); the
watchlist partition splits pinned lists into the focused (active sub-tab) tier
and the other-visible tier; ``build_context`` normalizes app state into a
``PrefetchContext``. All pure — no Tk.
"""
from __future__ import annotations

import pytest

from tradinglab.data.prefetch.appglue import (
    build_context,
    partition_watchlists,
    scheduler_enabled,
    scheduler_mode,
)
from tradinglab.data.prefetch.tiers import PrefetchContext


# --------------------------------------------------------------------- flags
def test_scheduler_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRADINGLAB_PREFETCH_SCHEDULER", raising=False)
    assert scheduler_enabled() is False


@pytest.mark.parametrize("val", ["1", "on", "true", "yes", "shadow", "live", "SHADOW"])
def test_scheduler_enabled_values(monkeypatch, val):
    monkeypatch.setenv("TRADINGLAB_PREFETCH_SCHEDULER", val)
    assert scheduler_enabled() is True


@pytest.mark.parametrize("val", ["", "0", "off", "false", "no"])
def test_scheduler_disabled_values(monkeypatch, val):
    monkeypatch.setenv("TRADINGLAB_PREFETCH_SCHEDULER", val)
    assert scheduler_enabled() is False


def test_scheduler_mode_defaults_to_shadow(monkeypatch):
    monkeypatch.setenv("TRADINGLAB_PREFETCH_SCHEDULER", "1")
    assert scheduler_mode() == "shadow"


def test_scheduler_mode_live(monkeypatch):
    monkeypatch.setenv("TRADINGLAB_PREFETCH_SCHEDULER", "live")
    assert scheduler_mode() == "live"


# --------------------------------------------------------- partition_watchlists
def _tickers_of(mapping):
    return lambda name: mapping.get(name, ())


def test_partition_focused_and_other():
    mapping = {"Tech": ("AMD", "NVDA"), "ETFs": ("SPY", "QQQ"), "Mine": ("AMD",)}
    focused, other = partition_watchlists(
        "Tech", ["Tech", "ETFs", "Mine"], _tickers_of(mapping),
    )
    assert focused == ["AMD", "NVDA"]
    # AMD already claimed by focused → deduped out of other
    assert other == ["SPY", "QQQ"]


def test_partition_focused_not_pinned():
    mapping = {"ETFs": ("SPY",)}
    focused, other = partition_watchlists("Tech", ["ETFs"], _tickers_of(mapping))
    assert focused == []
    assert other == ["SPY"]


def test_partition_empty():
    focused, other = partition_watchlists("", [], _tickers_of({}))
    assert focused == [] and other == []


def test_partition_normalizes_and_dedupes():
    mapping = {"A": (" amd ", "amd", "nvda"), "B": ("AMD", "intc")}
    focused, other = partition_watchlists("A", ["A", "B"], _tickers_of(mapping))
    assert focused == ["AMD", "NVDA"]
    assert other == ["INTC"]


# --------------------------------------------------------------- build_context
def test_build_context_normalizes():
    ctx = build_context(
        source="alpaca", active_symbol=" amd ", active_interval="5m",
        compare_symbol="spy", focused_watchlist=["nvda"],
        other_watchlists=["msft"], universe=["tsla"],
    )
    assert isinstance(ctx, PrefetchContext)
    assert ctx.active_symbol == "AMD"
    assert ctx.compare_symbol == "SPY"
    assert ctx.focused_watchlist == ("NVDA",)
    assert ctx.other_watchlists == ("MSFT",)
    assert ctx.universe == ("TSLA",)


def test_build_context_blank_compare():
    ctx = build_context(source="a", active_symbol="AMD", active_interval="1d",
                        compare_symbol="")
    assert ctx.compare_symbol == ""
    assert ctx.focused_watchlist == () and ctx.universe == ()
