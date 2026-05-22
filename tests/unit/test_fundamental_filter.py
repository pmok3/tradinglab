"""Unit tests for ``tradinglab.preload.fundamental_filter``.

Pure-function suite — no Tk, no network, no executor.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

import pytest

from tradinglab.models import Candle
from tradinglab.preload.fundamental_filter import (
    FundamentalFilter,
    filter_symbols,
    is_filter_active,
    passes_fundamental_filter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _daily_bars(
    *,
    n: int,
    close: float,
    volume: float,
    start: datetime = datetime(2024, 1, 2, tzinfo=timezone.utc),
) -> list[Candle]:
    """Build ``n`` consecutive daily Candle objects with constant close + volume."""
    out: list[Candle] = []
    t = start
    for _ in range(n):
        out.append(Candle(
            date=t,
            open=close, high=close, low=close, close=close,
            volume=int(volume),
        ))
        t = t + timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# FundamentalFilter dataclass defaults
# ---------------------------------------------------------------------------


def test_default_filter_is_inactive() -> None:
    spec = FundamentalFilter()
    assert spec.min_avg_volume_millions is None
    assert spec.min_close is None
    assert spec.max_close is None
    assert spec.lookback_days == 20
    assert is_filter_active(spec) is False


def test_filter_is_active_when_any_criterion_set() -> None:
    assert is_filter_active(FundamentalFilter(min_close=10.0)) is True
    assert is_filter_active(FundamentalFilter(max_close=500.0)) is True
    assert is_filter_active(FundamentalFilter(min_avg_volume_millions=1.0)) is True
    # lookback_days alone does NOT make a filter active.
    assert is_filter_active(FundamentalFilter(lookback_days=50)) is False


# ---------------------------------------------------------------------------
# passes_fundamental_filter — empty / missing input
# ---------------------------------------------------------------------------


def test_empty_bars_with_inactive_filter_pass() -> None:
    """A symbol with no bars passes a filter that imposes no constraints."""
    assert passes_fundamental_filter([], FundamentalFilter()) is True


def test_empty_bars_with_active_filter_fail() -> None:
    spec = FundamentalFilter(min_close=10.0)
    assert passes_fundamental_filter([], spec) is False


def test_none_bars_treated_as_empty_via_iteration() -> None:
    """The helper coerces falsy input via ``if not daily_bars``."""
    spec = FundamentalFilter(min_close=10.0)
    # An empty tuple should also fail.
    assert passes_fundamental_filter((), spec) is False


# ---------------------------------------------------------------------------
# Min/max close
# ---------------------------------------------------------------------------


def test_min_close_pass() -> None:
    bars = _daily_bars(n=30, close=100.0, volume=5_000_000)
    spec = FundamentalFilter(min_close=80.0)
    assert passes_fundamental_filter(bars, spec) is True


def test_min_close_fail() -> None:
    bars = _daily_bars(n=30, close=50.0, volume=5_000_000)
    spec = FundamentalFilter(min_close=80.0)
    assert passes_fundamental_filter(bars, spec) is False


def test_min_close_uses_last_bar_not_mean() -> None:
    """Last bar is the deciding close, not a smoothed average."""
    bars = _daily_bars(n=29, close=200.0, volume=5_000_000) + _daily_bars(
        n=1, close=50.0, volume=5_000_000,
    )
    spec = FundamentalFilter(min_close=100.0)
    assert passes_fundamental_filter(bars, spec) is False


def test_max_close_pass() -> None:
    bars = _daily_bars(n=30, close=50.0, volume=5_000_000)
    spec = FundamentalFilter(max_close=80.0)
    assert passes_fundamental_filter(bars, spec) is True


def test_max_close_fail() -> None:
    bars = _daily_bars(n=30, close=200.0, volume=5_000_000)
    spec = FundamentalFilter(max_close=80.0)
    assert passes_fundamental_filter(bars, spec) is False


def test_price_range_band() -> None:
    """min_close + max_close together form a band; the close must lie within."""
    spec = FundamentalFilter(min_close=80.0, max_close=120.0)
    assert passes_fundamental_filter(_daily_bars(n=30, close=100.0, volume=5_000_000), spec) is True
    assert passes_fundamental_filter(_daily_bars(n=30, close=50.0, volume=5_000_000), spec) is False
    assert passes_fundamental_filter(_daily_bars(n=30, close=150.0, volume=5_000_000), spec) is False


def test_boundary_inclusive_on_both_sides() -> None:
    """Exactly hitting the boundary passes (greater-than-or-equal / less-than-or-equal)."""
    bars = _daily_bars(n=30, close=80.0, volume=5_000_000)
    assert passes_fundamental_filter(bars, FundamentalFilter(min_close=80.0)) is True
    assert passes_fundamental_filter(bars, FundamentalFilter(max_close=80.0)) is True


# ---------------------------------------------------------------------------
# Average volume
# ---------------------------------------------------------------------------


def test_avg_volume_pass() -> None:
    bars = _daily_bars(n=30, close=100.0, volume=12_000_000)
    spec = FundamentalFilter(min_avg_volume_millions=10.0)
    assert passes_fundamental_filter(bars, spec) is True


def test_avg_volume_fail() -> None:
    bars = _daily_bars(n=30, close=100.0, volume=5_000_000)
    spec = FundamentalFilter(min_avg_volume_millions=10.0)
    assert passes_fundamental_filter(bars, spec) is False


def test_avg_volume_boundary_inclusive() -> None:
    bars = _daily_bars(n=20, close=100.0, volume=10_000_000)
    spec = FundamentalFilter(min_avg_volume_millions=10.0, lookback_days=20)
    assert passes_fundamental_filter(bars, spec) is True


def test_avg_volume_uses_only_lookback_days() -> None:
    """Bars older than lookback_days do not affect the mean."""
    # First 100 bars are HEAVY volume (would inflate the mean), last 20 are LIGHT.
    heavy = _daily_bars(n=100, close=100.0, volume=100_000_000)
    light_start = heavy[-1].date + timedelta(days=1)
    light = _daily_bars(
        n=20, close=100.0, volume=1_000_000,
        start=light_start,
    )
    bars = heavy + light
    spec = FundamentalFilter(min_avg_volume_millions=10.0, lookback_days=20)
    # Mean of last 20 = 1M, threshold = 10M → fail.
    assert passes_fundamental_filter(bars, spec) is False


def test_insufficient_history_fails_volume_gate() -> None:
    """Fewer bars than lookback_days → under-include, not over-include."""
    bars = _daily_bars(n=5, close=100.0, volume=100_000_000)
    spec = FundamentalFilter(min_avg_volume_millions=1.0, lookback_days=20)
    assert passes_fundamental_filter(bars, spec) is False


def test_insufficient_history_passes_when_volume_filter_inactive() -> None:
    """If volume filter is off, short history is fine for the price filter."""
    bars = _daily_bars(n=2, close=100.0, volume=1_000_000)
    spec = FundamentalFilter(min_close=50.0, lookback_days=20)
    assert passes_fundamental_filter(bars, spec) is True


def test_lookback_days_at_least_one() -> None:
    """``lookback_days = 0`` is normalised to 1."""
    bars = _daily_bars(n=1, close=100.0, volume=10_000_000)
    spec = FundamentalFilter(min_avg_volume_millions=1.0, lookback_days=0)
    assert passes_fundamental_filter(bars, spec) is True


# ---------------------------------------------------------------------------
# Combined criteria (AND semantics)
# ---------------------------------------------------------------------------


def test_combined_filter_all_pass() -> None:
    bars = _daily_bars(n=30, close=100.0, volume=15_000_000)
    spec = FundamentalFilter(
        min_avg_volume_millions=10.0, min_close=50.0, max_close=200.0, lookback_days=20,
    )
    assert passes_fundamental_filter(bars, spec) is True


def test_combined_filter_one_fails_short_circuits() -> None:
    """Any single failing criterion fails the whole filter (AND semantics)."""
    # Volume passes (15M >= 10M), but price fails (close=50 < min=80).
    bars = _daily_bars(n=30, close=50.0, volume=15_000_000)
    spec = FundamentalFilter(
        min_avg_volume_millions=10.0, min_close=80.0,
    )
    assert passes_fundamental_filter(bars, spec) is False


# ---------------------------------------------------------------------------
# filter_symbols wrapper
# ---------------------------------------------------------------------------


def test_filter_symbols_returns_input_order() -> None:
    bars_by_sym = {
        "AAA": _daily_bars(n=30, close=100.0, volume=15_000_000),
        "BBB": _daily_bars(n=30, close=20.0, volume=15_000_000),  # below min_close
        "CCC": _daily_bars(n=30, close=200.0, volume=15_000_000),
    }
    spec = FundamentalFilter(min_close=50.0)
    out = filter_symbols(["AAA", "BBB", "CCC"], bars_by_sym.get, spec)
    assert out == ["AAA", "CCC"]


def test_filter_symbols_skips_unknown_symbols() -> None:
    """Symbols whose bars_lookup returns None are dropped (no bars = no decision)."""
    bars_by_sym = {"AAA": _daily_bars(n=30, close=100.0, volume=15_000_000)}
    spec = FundamentalFilter(min_close=50.0)
    out = filter_symbols(["AAA", "BBB"], bars_by_sym.get, spec)
    assert out == ["AAA"]


def test_filter_symbols_passthrough_when_filter_inactive() -> None:
    """With no criteria set, every symbol passes — bars_lookup is not even called."""
    def explode(_sym: str):
        raise AssertionError("bars_lookup should not be called when filter is inactive")
    out = filter_symbols(["AAA", "BBB", "CCC"], explode, FundamentalFilter())
    assert out == ["AAA", "BBB", "CCC"]


def test_filter_spec_is_frozen() -> None:
    spec = FundamentalFilter(min_close=10.0)
    with pytest.raises((AttributeError, Exception)):
        spec.min_close = 99.0  # type: ignore[misc]
