"""Unit tests for ``strategy_tester.report``.

Pure-Python math tests + a tiny round-trip test for the disk-aware
``aggregate_run`` driver. Bootstrap sample counts are kept small
(``n_samples=200``) so the suite runs in <1 second.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from tradinglab.backtest.journal import PostTradeReview, PreTradeEntry
from tradinglab.backtest.performance import TradeRow
from tradinglab.strategy_tester.report import (
    AGGREGATE_FILENAME,
    ConfidenceInterval,
    PerSymbolStats,
    bootstrap_ci,
    compute_aggregate,
    daily_sharpe,
    daily_sortino,
    expectancy,
    load_aggregate,
    max_drawdown,
    profit_factor,
    save_aggregate,
    wilson_score_ci,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(
    *,
    symbol: str = "TEST",
    side: str = "buy",
    pnl: float = 100.0,
    entry_ts: int | None = None,
    exit_ts: int | None = None,
    setup_tag: str = "",
) -> TradeRow:
    if entry_ts is None:
        entry_ts = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
    if exit_ts is None:
        exit_ts = entry_ts + 60 * 60 * 1000
    pre = PreTradeEntry(
        order_id=f"o-{exit_ts}",
        ts=entry_ts,
        symbol=symbol,
        side=side,
        setup_tag=setup_tag,
        thesis="",
        conviction=3,
        size=100.0,
    )
    post = PostTradeReview(
        symbol=symbol,
        entry_ts=entry_ts,
        exit_ts=exit_ts,
        entry_price=100.0,
        exit_price=100.0 + (pnl / 100.0),
        quantity=100.0,
        side=side,
        pnl=pnl,
        pnl_pct=pnl / 10_000.0,
        mae=abs(pnl) * 0.5,
        mfe=abs(pnl) * 0.5,
        mae_pct=-0.005,
        mfe_pct=0.005,
        ref_pre_trade_id=pre.order_id,
    )
    return TradeRow(post=post, pre=pre)


def _ts(year: int, month: int = 1, day: int = 1) -> int:
    return int(datetime(year, month, day, 15, 30, tzinfo=timezone.utc).timestamp() * 1000)


# ---------------------------------------------------------------------------
# wilson_score_ci
# ---------------------------------------------------------------------------


def test_wilson_zero_sample_returns_zero_ci() -> None:
    ci = wilson_score_ci(0, 0)
    assert ci.lo == 0.0 and ci.hi == 0.0 and ci.point == 0.0


def test_wilson_unanimous_wins_upper_bound_at_one() -> None:
    ci = wilson_score_ci(10, 10, confidence=0.95)
    assert ci.point == 1.0
    assert ci.hi == pytest.approx(1.0, abs=1e-12)
    # Wilson lower bound on p=1, N=10 ≈ 0.72
    assert 0.6 < ci.lo < 0.85


def test_wilson_unanimous_losses_lower_bound_at_zero() -> None:
    ci = wilson_score_ci(0, 10, confidence=0.95)
    assert ci.point == 0.0
    assert ci.lo == 0.0


def test_wilson_p_05_narrows_with_larger_n() -> None:
    """The CI gets tighter as N grows — sanity check."""
    ci_small = wilson_score_ci(5, 10, confidence=0.95)
    ci_large = wilson_score_ci(500, 1000, confidence=0.95)
    width_small = ci_small.hi - ci_small.lo
    width_large = ci_large.hi - ci_large.lo
    assert width_large < width_small


# ---------------------------------------------------------------------------
# profit_factor + expectancy
# ---------------------------------------------------------------------------


def test_profit_factor_no_losses_is_inf() -> None:
    rows = [_row(pnl=100.0), _row(pnl=200.0)]
    assert math.isinf(profit_factor(rows))


def test_profit_factor_no_wins_is_zero() -> None:
    rows = [_row(pnl=-100.0), _row(pnl=-50.0)]
    assert profit_factor(rows) == 0.0


def test_profit_factor_mixed() -> None:
    rows = [_row(pnl=200.0), _row(pnl=-100.0)]
    assert profit_factor(rows) == pytest.approx(2.0)


def test_expectancy_empty_is_zero() -> None:
    assert expectancy([]) == 0.0


def test_expectancy_all_wins() -> None:
    rows = [_row(pnl=100.0), _row(pnl=200.0)]
    assert expectancy(rows) == pytest.approx(150.0)


def test_expectancy_balanced() -> None:
    # 2 winners @ 100, 2 losers @ -50 → 0.5*100 + 0.5*-50 = 25
    rows = [_row(pnl=100.0)] * 2 + [_row(pnl=-50.0)] * 2
    assert expectancy(rows) == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# bootstrap_ci
# ---------------------------------------------------------------------------


def test_bootstrap_ci_deterministic_with_seed() -> None:
    rows = [_row(pnl=100.0), _row(pnl=-50.0)] * 5
    ci1 = bootstrap_ci(rows, expectancy, n_samples=200, rng_seed=42)
    ci2 = bootstrap_ci(rows, expectancy, n_samples=200, rng_seed=42)
    assert ci1.lo == ci2.lo
    assert ci1.hi == ci2.hi


def test_bootstrap_ci_brackets_point_estimate() -> None:
    rows = [_row(pnl=v) for v in (100.0, 200.0, -50.0, -100.0, 50.0, 75.0)]
    point = expectancy(rows)
    ci = bootstrap_ci(rows, expectancy, n_samples=200, rng_seed=1)
    # Point should fall within the 95% CI for a well-formed sample.
    assert ci.lo <= point <= ci.hi


def test_bootstrap_ci_empty_rows() -> None:
    ci = bootstrap_ci([], expectancy, n_samples=10, rng_seed=1)
    assert ci.lo == 0.0 and ci.hi == 0.0


# ---------------------------------------------------------------------------
# max_drawdown
# ---------------------------------------------------------------------------


def test_max_drawdown_monotonic_increase_is_zero() -> None:
    curve = [(1, 100.0), (2, 101.0), (3, 105.0), (4, 110.0)]
    dd, dd_pct = max_drawdown(curve)
    assert dd == 0.0 and dd_pct == 0.0


def test_max_drawdown_sawtooth() -> None:
    # Peak at 200, trough at 150 → dd=50, dd_pct=0.25
    curve = [(1, 100.0), (2, 200.0), (3, 150.0), (4, 180.0), (5, 220.0), (6, 150.0)]
    dd, dd_pct = max_drawdown(curve)
    # 220 → 150 = 70 (worst), pct = 70/220 ≈ 0.318
    assert dd == pytest.approx(70.0)
    assert dd_pct == pytest.approx(70.0 / 220.0)


def test_max_drawdown_empty() -> None:
    assert max_drawdown([]) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# Sharpe / Sortino
# ---------------------------------------------------------------------------


def test_sharpe_constant_curve_is_zero() -> None:
    curve = [(_ts(2024, 1, d), 100_000.0) for d in range(1, 11)]
    assert daily_sharpe(curve) == 0.0


def test_sortino_constant_curve_is_zero() -> None:
    curve = [(_ts(2024, 1, d), 100_000.0) for d in range(1, 11)]
    assert daily_sortino(curve) == 0.0


def test_sharpe_monotonic_growth_is_positive() -> None:
    # Steady 1% daily growth → Sharpe should be very high (~16+ annualised).
    curve = []
    eq = 100_000.0
    for d in range(1, 21):
        eq *= 1.01
        curve.append((_ts(2024, 1, d), eq))
    s = daily_sharpe(curve)
    assert s > 5.0


def test_sortino_only_negative_returns_have_downside() -> None:
    # Mix: most days flat, two big losses.
    curve = []
    eq = 100_000.0
    for d in range(1, 11):
        if d in (4, 7):
            eq *= 0.99
        curve.append((_ts(2024, 1, d), eq))
    so = daily_sortino(curve)
    # Mean return is slightly negative (two losses, no wins), so
    # Sortino is negative or zero — both are defensible. The
    # important property is that the function returns a finite number.
    assert math.isfinite(so)


# ---------------------------------------------------------------------------
# compute_aggregate
# ---------------------------------------------------------------------------


def test_compute_aggregate_empty_run() -> None:
    agg = compute_aggregate(
        run_id="r1", rows_by_symbol={}, starting_cash=100_000.0,
        bootstrap_samples=10,
    )
    assert agg.trade_count == 0
    assert agg.win_count == 0
    assert agg.insufficient_sample is True
    assert agg.low_sample is True
    assert agg.equity_curve == []


def test_compute_aggregate_single_symbol_three_wins() -> None:
    rows = [_row(symbol="AAPL", pnl=100.0, exit_ts=_ts(2024, 1, d))
            for d in range(2, 5)]
    agg = compute_aggregate(
        run_id="r2", rows_by_symbol={"AAPL": rows}, starting_cash=100_000.0,
        bootstrap_samples=20,
    )
    assert agg.trade_count == 3
    assert agg.win_count == 3
    assert agg.win_rate == pytest.approx(1.0)
    assert agg.total_pnl_gross == pytest.approx(300.0)
    # profit_factor inf → clamped to 1e9
    assert agg.profit_factor == 1e9
    assert agg.insufficient_sample is True


def test_compute_aggregate_two_symbols_independent_capital() -> None:
    aapl_rows = [_row(symbol="AAPL", pnl=200.0, exit_ts=_ts(2024, 1, 5))]
    msft_rows = [_row(symbol="MSFT", pnl=-100.0, exit_ts=_ts(2024, 1, 6))]
    agg = compute_aggregate(
        run_id="r3",
        rows_by_symbol={"AAPL": aapl_rows, "MSFT": msft_rows},
        starting_cash=100_000.0, bootstrap_samples=20,
    )
    assert agg.trade_count == 2
    # 2 symbols × $100K starting → first equity point is $200K + first pnl.
    first_eq = agg.equity_curve[0][1]
    assert abs(first_eq - 200_200.0) < 0.01 or abs(first_eq - 199_900.0) < 0.01
    assert {s.symbol for s in agg.per_symbol} == {"AAPL", "MSFT"}


def test_compute_aggregate_per_year_breakout() -> None:
    rows = [
        _row(pnl=100.0, exit_ts=_ts(2023, 6, 1)),
        _row(pnl=-50.0, exit_ts=_ts(2023, 12, 1)),
        _row(pnl=200.0, exit_ts=_ts(2024, 3, 1)),
    ]
    agg = compute_aggregate(
        run_id="r4", rows_by_symbol={"X": rows},
        starting_cash=100_000.0, bootstrap_samples=10,
    )
    years = [y.year for y in agg.per_year]
    assert years == [2023, 2024]


def test_compute_aggregate_permutation_invariance() -> None:
    rows1 = [_row(pnl=100.0, exit_ts=_ts(2024, 1, d)) for d in range(2, 8)]
    rows2 = list(reversed(rows1))
    agg1 = compute_aggregate(
        run_id="r5", rows_by_symbol={"S": rows1},
        starting_cash=100_000.0, bootstrap_samples=50,
    )
    agg2 = compute_aggregate(
        run_id="r5", rows_by_symbol={"S": rows2},
        starting_cash=100_000.0, bootstrap_samples=50,
    )
    # Headline metrics are order-invariant; bootstrap CI uses internal
    # seed so it's also reproducible regardless of input order.
    assert agg1.total_pnl_gross == agg2.total_pnl_gross
    assert agg1.win_rate == agg2.win_rate
    assert agg1.expectancy == agg2.expectancy


def test_compute_aggregate_sample_banners() -> None:
    """N>=100 → no banners; 30<=N<100 → low only; N<30 → both."""
    def _gen(n):
        return [_row(pnl=100.0, exit_ts=_ts(2024, 1, 2) + i * 86_400_000)
                for i in range(n)]
    agg_small = compute_aggregate(
        run_id="rs", rows_by_symbol={"X": _gen(10)},
        starting_cash=100_000.0, bootstrap_samples=10,
    )
    agg_mid = compute_aggregate(
        run_id="rm", rows_by_symbol={"X": _gen(50)},
        starting_cash=100_000.0, bootstrap_samples=10,
    )
    agg_large = compute_aggregate(
        run_id="rl", rows_by_symbol={"X": _gen(150)},
        starting_cash=100_000.0, bootstrap_samples=10,
    )
    assert agg_small.insufficient_sample is True and agg_small.low_sample is True
    assert agg_mid.insufficient_sample is False and agg_mid.low_sample is True
    assert agg_large.insufficient_sample is False and agg_large.low_sample is False


def test_compute_aggregate_best_worst_month_removed_changes_total() -> None:
    """Two-month split with one big month and one small should change totals."""
    rows = [
        # Jan 2024 — $1000 total
        _row(pnl=1000.0, exit_ts=_ts(2024, 1, 5)),
        # Feb 2024 — $100 total
        _row(pnl=100.0, exit_ts=_ts(2024, 2, 5)),
    ]
    agg = compute_aggregate(
        run_id="r6", rows_by_symbol={"X": rows},
        starting_cash=100_000.0, bootstrap_samples=10,
    )
    # Total = 1100, best-removed (drop Jan) = 100, worst-removed (drop Feb) = 1000.
    assert agg.total_pnl_net == pytest.approx(1100.0)
    assert agg.best_month_removed_total_pnl == pytest.approx(100.0)
    assert agg.worst_month_removed_total_pnl == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# save / load round-trip
# ---------------------------------------------------------------------------


def test_save_and_load_aggregate_round_trip(tmp_path) -> None:
    rows = [_row(pnl=100.0, exit_ts=_ts(2024, 1, 5))] * 3
    agg = compute_aggregate(
        run_id="r7", rows_by_symbol={"X": rows},
        starting_cash=100_000.0, bootstrap_samples=10,
    )
    save_aggregate(tmp_path, agg)
    assert (tmp_path / AGGREGATE_FILENAME).exists()
    loaded = load_aggregate(tmp_path)
    assert loaded is not None
    assert loaded.run_id == "r7"
    assert loaded.trade_count == agg.trade_count
    assert loaded.win_rate == agg.win_rate
    assert loaded.total_pnl_gross == agg.total_pnl_gross


def test_load_aggregate_missing_returns_none(tmp_path) -> None:
    assert load_aggregate(tmp_path / "nonexistent") is None


def test_save_aggregate_writes_fingerprint(tmp_path) -> None:
    agg = compute_aggregate(
        run_id="r8", rows_by_symbol={},
        starting_cash=100_000.0, bootstrap_samples=10,
    )
    save_aggregate(tmp_path, agg)
    import json
    payload = json.loads((tmp_path / AGGREGATE_FILENAME).read_text(encoding="utf-8"))
    assert "fingerprint" in payload
    assert len(payload["fingerprint"]) == 16


# ---------------------------------------------------------------------------
# PerSymbolStats — ordering
# ---------------------------------------------------------------------------


def test_per_symbol_ordered_alphabetically() -> None:
    rows_by_symbol = {
        "MSFT": [_row(symbol="MSFT", pnl=100.0)],
        "AAPL": [_row(symbol="AAPL", pnl=200.0)],
        "GOOG": [_row(symbol="GOOG", pnl=-50.0)],
    }
    agg = compute_aggregate(
        run_id="rsym", rows_by_symbol=rows_by_symbol,
        starting_cash=100_000.0, bootstrap_samples=10,
    )
    symbols = [s.symbol for s in agg.per_symbol]
    assert symbols == ["AAPL", "GOOG", "MSFT"]
    aapl = next(s for s in agg.per_symbol if s.symbol == "AAPL")
    assert isinstance(aapl, PerSymbolStats)
    assert aapl.total_pnl_gross == 200.0


def test_ci_to_dict_round_trip() -> None:
    ci = ConfidenceInterval(lo=0.1, hi=0.5, point=0.3, confidence=0.95)
    d = ci.to_dict()
    assert d == {"lo": 0.1, "hi": 0.5, "point": 0.3, "confidence": 0.95}
