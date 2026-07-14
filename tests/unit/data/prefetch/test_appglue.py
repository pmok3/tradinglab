"""Unit tests for ``data.prefetch.appglue`` — pure ChartApp-integration helpers.

The flag readers gate the whole feature (default OFF → zero change); the
watchlist partition splits pinned lists into the focused (active sub-tab) tier
and the other-visible tier; ``build_context`` normalizes app state into a
``PrefetchContext``. All pure — no Tk.
"""
from __future__ import annotations

import pytest

from tradinglab.data.prefetch.appglue import (
    bucket_registry_for_mode,
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


# ------------------------------------------------------ bucket_registry_for_mode
def test_live_mode_shares_the_global_registry():
    from tradinglab.data.prefetch import global_bucket_registry
    assert bucket_registry_for_mode("live") is global_bucket_registry()


def test_shadow_mode_gets_a_separate_unlimited_registry():
    from tradinglab.data.prefetch import global_bucket_registry
    reg = bucket_registry_for_mode("shadow")
    assert reg is not global_bucket_registry()
    # Every source is unlimited so dry-run planning never rate-stalls.
    assert reg.bucket_for("alpaca").rate_per_min > 1_000.0
    assert reg.bucket_for("mystery").rate_per_min > 1_000.0


def test_shadow_driver_does_not_consume_global_tokens():
    """The whole point of shadow: observe the plan with ZERO real-token spend.

    Regression guard for the principal-SWE Must-fix — before the fix,
    ``_build_prefetch_driver`` handed the scheduler the process-wide
    ``global_bucket_registry`` in shadow mode too, so a shadow ``pump`` spent
    real Alpaca tokens (``next_dispatch`` → ``bucket.try_acquire``) and could
    throttle the live/foreground fetch it is meant to passively measure.
    """
    from tradinglab.data.prefetch import (
        PrefetchDriver,
        PrefetchScheduler,
        SourceBucketRegistry,
        global_bucket_registry,
        set_global_bucket_registry,
        standard_tiers,
    )

    frozen = [1000.0]
    prev = global_bucket_registry()
    try:
        set_global_bucket_registry(SourceBucketRegistry(clock=lambda: frozen[0]))
        gbucket = global_bucket_registry().bucket_for("alpaca")
        # Force a capacity-1 bucket (starts full → exactly 1 token, no refill
        # under the frozen clock): any shadow consumption is immediately visible.
        gbucket.configure(10.0, burst=1.0)

        sched = PrefetchScheduler(
            standard_tiers(),
            buckets=bucket_registry_for_mode("shadow"),
            supports_range=lambda s: s == "alpaca",
        )
        driver = PrefetchDriver(sched, submit=lambda job: None, shadow=True)
        driver.set_context(build_context(
            source="alpaca", active_symbol="AMD", active_interval="1d",
            compare_symbol="SPY",
        ))
        driver.pump()

        # Full plan observed (active + compare, dual interval = 4 band-0 jobs),
        # NOT rate-stalled after a single token — proof shadow used the unlimited
        # registry, not the capacity-1 global bucket.
        assert len(driver.shadow_log) >= 4
        # The single real global token is still there — shadow spent nothing.
        assert gbucket.try_acquire(1) is True
        assert gbucket.try_acquire(1) is False  # and there was exactly one
    finally:
        set_global_bucket_registry(prev)


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
