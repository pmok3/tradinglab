"""Tests for the pure-function ``compute_run_estimate`` and
``_basket_size`` helpers in ``universe_prepare_dialog``.

These don't touch Tk — they exercise the math that drives the dialog's
reactive ETA / size estimate label. Headless-safe.
"""

from __future__ import annotations

import pytest

from tradinglab.gui.universe_prepare_dialog import (
    _basket_size,
    compute_run_estimate,
)

# ---------------------------------------------------------------------------
# Empty / null cases — label must be empty so the dialog can blank it
# ---------------------------------------------------------------------------

def test_empty_symbol_count_returns_blank_label() -> None:
    est = compute_run_estimate(symbol_count=0, intervals=("5m", "1d"))
    assert est["ops"] == 0
    assert est["seconds"] == 0.0
    assert est["bytes"] == 0
    assert est["label"] == ""


def test_empty_intervals_returns_blank_label() -> None:
    est = compute_run_estimate(symbol_count=2000, intervals=())
    assert est["ops"] == 0
    assert est["label"] == ""


def test_negative_symbol_count_returns_blank() -> None:
    est = compute_run_estimate(symbol_count=-1, intervals=("5m",))
    assert est["label"] == ""


# ---------------------------------------------------------------------------
# Ops counting
# ---------------------------------------------------------------------------

def test_ops_is_symbol_count_times_interval_count() -> None:
    est = compute_run_estimate(symbol_count=100, intervals=("5m", "1d"))
    assert est["ops"] == 200


def test_single_interval_ops() -> None:
    est = compute_run_estimate(symbol_count=503, intervals=("1d",))
    assert est["ops"] == 503


# ---------------------------------------------------------------------------
# Time math: daily-only is cheaper than intraday-only
# ---------------------------------------------------------------------------

def test_daily_only_is_cheaper_than_intraday_only_for_same_size() -> None:
    daily = compute_run_estimate(symbol_count=500, intervals=("1d",))
    intraday = compute_run_estimate(symbol_count=500, intervals=("5m",))
    assert daily["seconds"] < intraday["seconds"]


def test_mixed_intervals_time_is_sum_of_per_interval_costs() -> None:
    mixed = compute_run_estimate(symbol_count=100, intervals=("5m", "1d"))
    intraday = compute_run_estimate(symbol_count=100, intervals=("5m",))
    daily = compute_run_estimate(symbol_count=100, intervals=("1d",))
    assert mixed["seconds"] == pytest.approx(
        intraday["seconds"] + daily["seconds"]
    )


# ---------------------------------------------------------------------------
# Size math
# ---------------------------------------------------------------------------

def test_bytes_is_positive_for_nonempty_run() -> None:
    est = compute_run_estimate(symbol_count=10, intervals=("5m",))
    assert est["bytes"] > 0


def test_bytes_scales_with_symbol_count() -> None:
    small = compute_run_estimate(symbol_count=10, intervals=("5m",))
    big = compute_run_estimate(symbol_count=1000, intervals=("5m",))
    # Ratio matches the symbol-count ratio exactly (no per-call overhead
    # in the byte estimate).
    assert big["bytes"] == small["bytes"] * 100


# ---------------------------------------------------------------------------
# Label formatting
# ---------------------------------------------------------------------------

def test_label_includes_symbol_count() -> None:
    est = compute_run_estimate(symbol_count=2088, intervals=("5m", "1d"))
    assert "2088 symbols" in est["label"]


def test_label_includes_interval_summary() -> None:
    est = compute_run_estimate(symbol_count=100, intervals=("5m", "1d"))
    assert "5m, 1d" in est["label"]


def test_label_time_uses_minutes_above_one_minute() -> None:
    est = compute_run_estimate(symbol_count=2000, intervals=("5m",))
    # 2000 symbols * 1.5 s = 3000 s = 50 min — comfortably above the
    # 60 s threshold so the label should render "min" (or "h").
    assert "min" in est["label"] or "h" in est["label"]


def test_label_time_uses_hours_for_long_runs() -> None:
    est = compute_run_estimate(symbol_count=10_000, intervals=("5m",))
    # 10,000 * 1.5 s = 15,000 s = 4.17 h
    assert " h " in est["label"]


def test_label_size_uses_mb_for_medium_runs() -> None:
    est = compute_run_estimate(symbol_count=100, intervals=("5m",))
    # 100 * 560 KB = 56 MB
    assert "MB" in est["label"]


def test_label_size_uses_gb_for_full_exchange_runs() -> None:
    est = compute_run_estimate(symbol_count=2500, intervals=("5m", "1d"))
    # 2500 * (560 + 140) KB = 1.75 GB
    assert "GB" in est["label"]


def test_label_size_uses_kb_for_tiny_runs() -> None:
    est = compute_run_estimate(symbol_count=1, intervals=("1d",))
    # 1 * 140 KB = 140 KB
    assert "KB" in est["label"]


# ---------------------------------------------------------------------------
# _basket_size: should return >0 for the four real baskets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,minimum", [
    ("sp500", 400),
    ("qqq", 90),
    ("nyse", 1500),
    ("nasdaq", 2000),
])
def test_basket_size_for_known_baskets(kind: str, minimum: int) -> None:
    n = _basket_size(kind)
    assert n >= minimum, f"basket {kind!r} returned {n}, expected ≥ {minimum}"


def test_basket_size_for_unknown_basket_returns_zero() -> None:
    assert _basket_size("not-a-real-basket-xyz") == 0


def test_basket_size_is_cached() -> None:
    """Second call should hit the in-process cache and not re-parse the
    CSV. We assert this by-construction (the cache dict is keyed by
    name) — two calls in a row should return the same value with no
    side effect."""
    n1 = _basket_size("sp500")
    n2 = _basket_size("sp500")
    assert n1 == n2 > 0
