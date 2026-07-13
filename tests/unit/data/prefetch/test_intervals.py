"""Unit tests for ``data.prefetch.intervals`` — the dual-interval policy.

Contract (design §4 / Decision 15): given the on-screen ("active") interval,
return the ordered set of intervals to warm for a symbol:

* on-screen interval FIRST,
* then the escape-hatch interval — intraday → ``1d`` (Reset-View target),
  daily (``1d``) → ``5m`` (drilldown target),
* then any remaining of ``{5m, 1d}`` not yet present.

``5m`` and ``1d`` are ALWAYS present (they back the two one-click escape
hatches). Deduped, order-preserving.
"""
from __future__ import annotations

import pytest

from tradinglab.data.prefetch.intervals import dual_interval


@pytest.mark.parametrize(
    "active,expected",
    [
        ("5m", ["5m", "1d"]),      # intraday on-screen → reset-view 1d next
        ("1d", ["1d", "5m"]),      # daily on-screen -> drilldown 5m next
        ("1m", ["1m", "1d", "5m"]),   # exotic intraday → active, reset, drilldown
        ("15m", ["15m", "1d", "5m"]),
        ("1h", ["1h", "1d", "5m"]),
        ("1wk", ["1wk", "1d", "5m"]),  # weekly is 'daily-ish' but not "1d"
    ],
)
def test_dual_interval_order(active, expected):
    assert dual_interval(active) == expected


def test_five_and_one_day_always_present():
    for active in ("5m", "1d", "1m", "15m", "1h", "30m", "4h"):
        out = dual_interval(active)
        assert "5m" in out and "1d" in out, active


def test_active_is_always_first():
    for active in ("5m", "1d", "1m", "15m", "1h"):
        assert dual_interval(active)[0] == active


def test_no_duplicates():
    for active in ("5m", "1d", "1m", "15m", "1h"):
        out = dual_interval(active)
        assert len(out) == len(set(out)), active


def test_normalizes_case_and_whitespace():
    assert dual_interval(" 5M ") == ["5m", "1d"]
    assert dual_interval("1D") == ["1d", "5m"]


def test_blank_active_defaults_to_daily_order():
    # A missing / empty active interval (not-yet-realized chart) degrades to
    # the daily default rather than raising.
    assert dual_interval("") == ["1d", "5m"]
    assert dual_interval(None) == ["1d", "5m"]  # type: ignore[arg-type]


def test_returns_new_list_each_call():
    a = dual_interval("5m")
    a.append("zzz")
    assert dual_interval("5m") == ["5m", "1d"]  # not mutated by a prior caller
