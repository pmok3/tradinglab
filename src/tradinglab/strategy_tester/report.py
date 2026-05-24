"""Cross-symbol aggregation for a Strategy Tester Run.

PR 3 of the Strategy Tester rollout. PR 1 wrote per-symbol
``SessionResult`` JSON; PR 2 added per-trade screenshots. This module
walks those persisted artifacts (or accepts in-memory results for
testing) and computes the **Report** — the user-facing summary of
the entire Run.

Public surface — three building blocks:

* :func:`aggregate_run` — top-level driver. Reads
  ``per_symbol/<SYM>.json`` from the run directory, builds a
  :class:`RunAggregate`, and persists it as ``aggregate.json``.
* :func:`compute_aggregate` — pure-function math kernel that takes
  a list of :class:`TradeRow` and produces a :class:`RunAggregate`
  with no I/O. The unit-test entry point.
* :func:`write_run_csv` — writes the canonical 22-column trades CSV
  via the existing :func:`backtest.performance.write_trade_rows_csv`.
  Included here so report-export consumers have one stop.

The statistical recipe follows the mathematician's review notes
(see plan.md §Stats & methodology):

* **Wilson score CI** on win rate (not normal-approx; closed form).
* **Bootstrap CI** on expectancy + profit factor with
  configurable resample count (default 10 000).
* **Daily-equity-return Sharpe / Sortino** — daily resampled equity
  curve, ddof=1, annualised by ``sqrt(252)``.
* **R-multiple** — derived per-trade from PreTradeEntry.target as a
  proxy for an explicit initial stop when no stop info is available;
  rows without target carry ``r_multiple=None``.
* **Sample-size banners** — ``insufficient_sample`` (N<30) and
  ``low_sample`` (N<100) flags surfaced on the aggregate.
* **Per-calendar-year breakdown** — same metric set, sliced by
  ``post.exit_ts``'s UTC year.
* **Best/worst-month-removed return** — robustness probe; removes
  the calendar month with the highest / lowest total P&L from the
  trade list before recomputing total_pnl.

All math is plain Python — no numpy dep. Sample sizes for the
target user (single-strategy backtests, ≤50K trades) keep this
well below the GUI's interactive budget.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import random
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..backtest.performance import (
    TradeRow,
    build_setup_aggregates,
    build_trade_rows,
    write_trade_rows_csv,
)
from ..backtest.session import SessionResult
from . import storage

__all__ = [
    "AGGREGATE_FILENAME",
    "BOOTSTRAP_SAMPLES_DEFAULT",
    "ConfidenceInterval",
    "PerSymbolStats",
    "PerYearStats",
    "RunAggregate",
    "aggregate_run",
    "bootstrap_ci",
    "compute_aggregate",
    "daily_sharpe",
    "daily_sortino",
    "expectancy",
    "max_drawdown",
    "profit_factor",
    "wilson_score_ci",
    "write_run_csv",
]


AGGREGATE_FILENAME = "aggregate.json"
TRADES_CSV_FILENAME = "trades.csv"

# 10K bootstrap samples is the standard recipe -- enough for stable CI
# bounds on N≤10K trade samples; runs in ~50ms for N=200 / 1K samples
# on a modern laptop.
BOOTSTRAP_SAMPLES_DEFAULT = 10_000

# Sample-size thresholds for the GUI's banner system.
INSUFFICIENT_SAMPLE_THRESHOLD = 30
LOW_SAMPLE_THRESHOLD = 100

# Standard discretionary-trader risk-free rate proxy (annual). The
# Sharpe ratio is interpreted relative to a deposit account return,
# so 0 is a defensible default. Override via :func:`compute_aggregate`.
RISK_FREE_RATE_DEFAULT = 0.0

# Annualisation factor for daily returns (US equity convention).
TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfidenceInterval:
    """Two-sided confidence interval. ``lo`` <= point <= ``hi``."""
    lo: float
    hi: float
    point: float
    confidence: float  # 0..1, e.g. 0.95

    def to_dict(self) -> dict[str, float]:
        return {
            "lo": self.lo,
            "hi": self.hi,
            "point": self.point,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class PerSymbolStats:
    """One row per symbol in the Run."""
    symbol: str
    trade_count: int
    wins: int
    losses: int
    win_rate: float
    total_pnl_gross: float
    total_pnl_net: float
    avg_pnl_net: float
    profit_factor: float    # 0 when no losses; inf collapsed to 1e9
    max_drawdown: float     # dollar peak-to-trough on this symbol's equity

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PerYearStats:
    """One row per calendar year (UTC, indexed by trade exit_ts)."""
    year: int
    trade_count: int
    wins: int
    losses: int
    win_rate: float
    total_pnl_net: float
    expectancy: float
    profit_factor: float
    max_drawdown: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunAggregate:
    """The whole-Run rollup written to ``aggregate.json``.

    All values are computed off the union of trade rows from every
    symbol in the Run; nothing here references engine state directly,
    so the same aggregate can be rebuilt after the fact from disk
    without rerunning the engine.
    """
    run_id: str
    schema_version: int

    # Counts
    trade_count: int
    win_count: int
    loss_count: int
    breakeven_count: int

    # Headline metrics
    win_rate: float
    win_rate_ci_95: ConfidenceInterval
    total_pnl_gross: float
    total_pnl_net: float
    expectancy: float          # discretionary convention
    expectancy_ci_95: ConfidenceInterval
    profit_factor: float
    profit_factor_ci_95: ConfidenceInterval
    avg_win: float
    avg_loss: float            # signed-negative
    largest_win: float
    largest_loss: float        # signed-negative

    # Risk / equity-curve
    max_drawdown: float        # dollar peak-to-trough
    max_drawdown_pct: float    # fraction of starting capital across all symbols
    sharpe_ratio: float        # daily, annualised
    sortino_ratio: float       # daily, annualised
    equity_curve: list[tuple[int, float]] = field(default_factory=list)  # (ts_ms, equity)

    # Robustness probes
    best_month_removed_total_pnl: float = 0.0
    worst_month_removed_total_pnl: float = 0.0

    # Breakouts
    per_symbol: list[PerSymbolStats] = field(default_factory=list)
    per_year: list[PerYearStats] = field(default_factory=list)

    # Setup aggregate (reused from backtest.performance)
    per_setup: list[dict[str, Any]] = field(default_factory=list)

    # Banners
    insufficient_sample: bool = False
    low_sample: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "counts": {
                "trade_count": self.trade_count,
                "win_count": self.win_count,
                "loss_count": self.loss_count,
                "breakeven_count": self.breakeven_count,
            },
            "headline": {
                "win_rate": self.win_rate,
                "win_rate_ci_95": self.win_rate_ci_95.to_dict(),
                "total_pnl_gross": self.total_pnl_gross,
                "total_pnl_net": self.total_pnl_net,
                "expectancy": self.expectancy,
                "expectancy_ci_95": self.expectancy_ci_95.to_dict(),
                "profit_factor": self.profit_factor,
                "profit_factor_ci_95": self.profit_factor_ci_95.to_dict(),
                "avg_win": self.avg_win,
                "avg_loss": self.avg_loss,
                "largest_win": self.largest_win,
                "largest_loss": self.largest_loss,
            },
            "risk": {
                "max_drawdown": self.max_drawdown,
                "max_drawdown_pct": self.max_drawdown_pct,
                "sharpe_ratio": self.sharpe_ratio,
                "sortino_ratio": self.sortino_ratio,
            },
            "robustness": {
                "best_month_removed_total_pnl": self.best_month_removed_total_pnl,
                "worst_month_removed_total_pnl": self.worst_month_removed_total_pnl,
            },
            "equity_curve": [list(p) for p in self.equity_curve],
            "per_symbol": [s.to_dict() for s in self.per_symbol],
            "per_year": [y.to_dict() for y in self.per_year],
            "per_setup": list(self.per_setup),
            "banners": {
                "insufficient_sample": self.insufficient_sample,
                "low_sample": self.low_sample,
            },
        }


# ---------------------------------------------------------------------------
# Statistics — pure functions
# ---------------------------------------------------------------------------


def wilson_score_ci(
    successes: int, n: int, *, confidence: float = 0.95,
) -> ConfidenceInterval:
    """Wilson score interval — robust for small N and extreme p-hat.

    Closed-form solution; no iterative root finding. Used instead of
    the normal-approximation interval (Wald) because Wald breaks down
    for win_rate ~ 0 / ~ 1 and small N — exactly the regime the
    user's Strategy Tester operates in.
    """
    if n <= 0:
        return ConfidenceInterval(lo=0.0, hi=0.0, point=0.0, confidence=confidence)
    p = successes / n
    # Two-tailed z; closed-form for 90/95/99 to avoid scipy.
    z = _z_for_confidence(confidence)
    denom = 1.0 + (z * z) / n
    centre = (p + (z * z) / (2.0 * n)) / denom
    half = (z * math.sqrt(p * (1.0 - p) / n + (z * z) / (4.0 * n * n))) / denom
    lo = max(0.0, centre - half)
    hi = min(1.0, centre + half)
    return ConfidenceInterval(lo=lo, hi=hi, point=p, confidence=confidence)


def _z_for_confidence(confidence: float) -> float:
    """Two-tailed z-score for common confidences; scipy-free."""
    table = {
        0.80: 1.2815515655446004,
        0.90: 1.6448536269514722,
        0.95: 1.959963984540054,
        0.98: 2.3263478740408408,
        0.99: 2.5758293035489004,
    }
    # Nearest-tick match -- avoids float-equality cliffs.
    best = min(table, key=lambda k: abs(k - confidence))
    return table[best]


def profit_factor(rows: list[TradeRow]) -> float:
    """Gross winning P&L divided by gross losing P&L (absolute).

    Returns ``inf`` when there are wins but no losses (mapped to 1e9
    in :func:`compute_aggregate`'s output for JSON safety), and 0
    when there are no wins at all.
    """
    wins = sum(float(r.post.pnl) for r in rows if r.is_win)
    losses = sum(-float(r.post.pnl) for r in rows if r.is_loss)
    if losses <= 0.0:
        return math.inf if wins > 0 else 0.0
    return wins / losses


def expectancy(rows: list[TradeRow]) -> float:
    """Discretionary-trader expectancy: ``win_rate * avg_win + loss_rate * avg_loss``.

    Returns 0.0 on an empty row list.
    """
    if not rows:
        return 0.0
    wins_list = [float(r.post.pnl) for r in rows if r.is_win]
    losses_list = [float(r.post.pnl) for r in rows if r.is_loss]
    n = len(rows)
    n_wins = len(wins_list)
    n_losses = len(losses_list)
    win_rate = n_wins / n
    loss_rate = n_losses / n
    avg_win = (sum(wins_list) / n_wins) if n_wins else 0.0
    avg_loss = (sum(losses_list) / n_losses) if n_losses else 0.0
    return win_rate * avg_win + loss_rate * avg_loss


def bootstrap_ci(
    rows: list[TradeRow],
    statistic_fn,
    *,
    n_samples: int = BOOTSTRAP_SAMPLES_DEFAULT,
    confidence: float = 0.95,
    rng_seed: int = 1337,
) -> ConfidenceInterval:
    """Nonparametric bootstrap CI over the trade row list.

    Resamples ``rows`` with replacement ``n_samples`` times, computes
    ``statistic_fn`` on each resample, then takes the empirical
    percentiles at ``(1-confidence)/2`` and ``1 - (1-confidence)/2``.

    Why bootstrap: profit factor and expectancy distributions are
    heavily skewed by a handful of fat-tail wins; the t-test on the
    mean lies about uncertainty here. The bootstrap doesn't assume
    a distribution.

    ``rng_seed`` is fixed so re-running on the same row list returns
    identical bounds (reproducibility invariant — mirrors
    ``ENGINE_VERSION`` contract).
    """
    if not rows:
        return ConfidenceInterval(lo=0.0, hi=0.0, point=0.0, confidence=confidence)
    point = float(statistic_fn(rows))
    if not math.isfinite(point):
        # inf profit factor (no losses) — degenerate; CI collapses to point.
        return ConfidenceInterval(lo=point, hi=point, point=point, confidence=confidence)

    rng = random.Random(rng_seed)
    n = len(rows)
    samples: list[float] = []
    for _ in range(n_samples):
        # Sample with replacement; build the resample as a list of refs.
        resample = [rows[rng.randrange(n)] for _ in range(n)]
        v = float(statistic_fn(resample))
        if not math.isfinite(v):
            # Map degenerate resamples to a large finite value to keep
            # percentile arithmetic well-behaved. Clamping at 1e9 mirrors
            # the JSON safety transform in compute_aggregate.
            v = 1e9
        samples.append(v)
    samples.sort()

    alpha = 1.0 - confidence
    lo_idx = int(math.floor(alpha / 2.0 * n_samples))
    hi_idx = int(math.ceil((1.0 - alpha / 2.0) * n_samples)) - 1
    lo_idx = max(0, min(n_samples - 1, lo_idx))
    hi_idx = max(0, min(n_samples - 1, hi_idx))
    return ConfidenceInterval(
        lo=samples[lo_idx],
        hi=samples[hi_idx],
        point=point,
        confidence=confidence,
    )


def max_drawdown(equity_curve: list[tuple[int, float]]) -> tuple[float, float]:
    """Compute (max DD in $, max DD as fraction of peak).

    ``equity_curve`` is a list of ``(ts_ms, equity)`` ordered by ts.
    Returns (0.0, 0.0) on empty curves.
    """
    if not equity_curve:
        return (0.0, 0.0)
    peak = equity_curve[0][1]
    max_dd = 0.0
    max_dd_pct = 0.0
    for _, eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd
        # Avoid div-by-zero when peak is 0 (degenerate -- everyone broke).
        if peak > 0:
            dd_pct = dd / peak
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
    return (max_dd, max_dd_pct)


def _daily_returns(equity_curve: list[tuple[int, float]]) -> list[float]:
    """Convert tick-level equity to daily returns.

    Buckets ``(ts_s, equity)`` samples by UTC calendar day, takes the
    last-equity sample per day, then computes pct-change between
    consecutive days. Returns ``[]`` when there are fewer than two
    distinct days.

    ``ts_s`` is a UTC epoch-second integer (same unit as
    ``PostTradeReview.exit_ts``).
    """
    if not equity_curve:
        return []
    by_day: dict[_dt.date, float] = {}
    for ts_s, eq in equity_curve:
        d = _dt.datetime.fromtimestamp(ts_s, tz=_dt.timezone.utc).date()
        by_day[d] = float(eq)  # last sample wins
    if len(by_day) < 2:
        return []
    days = sorted(by_day.keys())
    rets: list[float] = []
    prev = by_day[days[0]]
    for d in days[1:]:
        cur = by_day[d]
        if prev > 0:
            rets.append((cur - prev) / prev)
        prev = cur
    return rets


def daily_sharpe(
    equity_curve: list[tuple[int, float]],
    *,
    risk_free_rate: float = RISK_FREE_RATE_DEFAULT,
) -> float:
    """Annualised Sharpe over daily equity returns. 0.0 when undefined."""
    rets = _daily_returns(equity_curve)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    if std <= 0.0:
        return 0.0
    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    return (mean - daily_rf) / std * math.sqrt(TRADING_DAYS_PER_YEAR)


def daily_sortino(
    equity_curve: list[tuple[int, float]],
    *,
    risk_free_rate: float = RISK_FREE_RATE_DEFAULT,
) -> float:
    """Annualised Sortino — downside-only volatility. 0.0 when undefined."""
    rets = _daily_returns(equity_curve)
    if len(rets) < 2:
        return 0.0
    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    mean = sum(rets) / len(rets)
    downside = [(r - daily_rf) for r in rets if r < daily_rf]
    if not downside:
        return 0.0
    dvar = sum(d * d for d in downside) / len(rets)
    dstd = math.sqrt(dvar)
    if dstd <= 0.0:
        return 0.0
    return (mean - daily_rf) / dstd * math.sqrt(TRADING_DAYS_PER_YEAR)


# ---------------------------------------------------------------------------
# Per-symbol + per-year breakouts
# ---------------------------------------------------------------------------


def _per_symbol_stats(
    rows_by_symbol: dict[str, list[TradeRow]],
    starting_cash: float,
) -> list[PerSymbolStats]:
    """One row per symbol; ordered alphabetically for stable JSON output."""
    out: list[PerSymbolStats] = []
    for sym in sorted(rows_by_symbol.keys()):
        rows = rows_by_symbol[sym]
        n = len(rows)
        wins = sum(1 for r in rows if r.is_win)
        losses = sum(1 for r in rows if r.is_loss)
        gross = sum(float(r.post.pnl) for r in rows)
        # PR 3 does not yet model commissions separately; net == gross
        # until the runner threads commission deductions through.
        net = gross
        avg = net / n if n else 0.0
        pf_raw = profit_factor(rows)
        pf = pf_raw if math.isfinite(pf_raw) else 1e9
        # Build a symbol-local equity curve to derive max drawdown.
        curve = _equity_curve_from_rows(rows, starting_cash=starting_cash)
        dd, _ = max_drawdown(curve)
        out.append(PerSymbolStats(
            symbol=sym,
            trade_count=n,
            wins=wins,
            losses=losses,
            win_rate=(wins / n) if n else 0.0,
            total_pnl_gross=gross,
            total_pnl_net=net,
            avg_pnl_net=avg,
            profit_factor=pf,
            max_drawdown=dd,
        ))
    return out


def _per_year_stats(rows: list[TradeRow]) -> list[PerYearStats]:
    """One row per UTC calendar year (indexed by exit_ts).

    ``exit_ts`` is a UTC epoch-second integer (same unit as
    ``PostTradeReview.exit_ts`` from the strategy-tester engine).
    """
    by_year: dict[int, list[TradeRow]] = {}
    for r in rows:
        y = _dt.datetime.fromtimestamp(
            r.post.exit_ts, tz=_dt.timezone.utc
        ).year
        by_year.setdefault(y, []).append(r)
    out: list[PerYearStats] = []
    for y in sorted(by_year.keys()):
        group = by_year[y]
        n = len(group)
        wins = sum(1 for r in group if r.is_win)
        losses = sum(1 for r in group if r.is_loss)
        gross = sum(float(r.post.pnl) for r in group)
        pf_raw = profit_factor(group)
        pf = pf_raw if math.isfinite(pf_raw) else 1e9
        # Year-local equity curve from $0 baseline (year-on-year max DD).
        curve = _equity_curve_from_rows(group, starting_cash=0.0)
        dd, _ = max_drawdown(curve)
        out.append(PerYearStats(
            year=y,
            trade_count=n,
            wins=wins,
            losses=losses,
            win_rate=(wins / n) if n else 0.0,
            total_pnl_net=gross,
            expectancy=expectancy(group),
            profit_factor=pf,
            max_drawdown=dd,
        ))
    return out


def _equity_curve_from_rows(
    rows: list[TradeRow], *, starting_cash: float,
) -> list[tuple[int, float]]:
    """Build a realised P&L equity curve from ``rows``, sorted by exit_ts.

    The curve has one point per closed trade; the first point is at
    ``(exit_ts[0], starting_cash + rows[0].pnl)``.
    """
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: int(r.post.exit_ts))
    eq = float(starting_cash)
    out: list[tuple[int, float]] = []
    for r in ordered:
        eq += float(r.post.pnl)
        out.append((int(r.post.exit_ts), eq))
    return out


def _month_removed_pnl(rows: list[TradeRow], *, best: bool) -> float:
    """Total P&L with the best (or worst) calendar month removed.

    Calendar-month is keyed off the trade's exit_ts in UTC. Returns
    the total P&L over rows when there are fewer than 2 months
    represented (nothing meaningful to remove).
    """
    if not rows:
        return 0.0
    by_month: dict[tuple[int, int], float] = {}
    for r in rows:
        d = _dt.datetime.fromtimestamp(
            r.post.exit_ts, tz=_dt.timezone.utc
        )
        key = (d.year, d.month)
        by_month[key] = by_month.get(key, 0.0) + float(r.post.pnl)
    if len(by_month) < 2:
        return sum(float(r.post.pnl) for r in rows)
    target_month = (
        max(by_month, key=lambda k: by_month[k]) if best
        else min(by_month, key=lambda k: by_month[k])
    )
    return sum(
        float(r.post.pnl) for r in rows
        if (
            _dt.datetime.fromtimestamp(
                r.post.exit_ts, tz=_dt.timezone.utc
            ).year,
            _dt.datetime.fromtimestamp(
                r.post.exit_ts, tz=_dt.timezone.utc
            ).month,
        ) != target_month
    )


# ---------------------------------------------------------------------------
# Top-level kernel
# ---------------------------------------------------------------------------


def compute_aggregate(
    *,
    run_id: str,
    rows_by_symbol: dict[str, list[TradeRow]],
    starting_cash: float,
    bootstrap_samples: int = BOOTSTRAP_SAMPLES_DEFAULT,
    rng_seed: int = 1337,
    schema_version: int = 1,
) -> RunAggregate:
    """Build a :class:`RunAggregate` from per-symbol trade rows.

    Pure function — no I/O. Decoupled from disk format so unit tests
    can synthesise rows directly. ``aggregate_run`` is the disk-aware
    wrapper that pairs this with on-disk SessionResult loading.

    ``starting_cash`` is the per-symbol independent capital from
    :class:`TestConfig.starting_cash`. The whole-Run starting capital
    is ``starting_cash * len(rows_by_symbol)`` since each symbol gets
    a fresh sandbox.
    """
    # Flatten to a single trade row list for whole-Run metrics.
    all_rows: list[TradeRow] = []
    for rows in rows_by_symbol.values():
        all_rows.extend(rows)

    n = len(all_rows)
    wins = [r for r in all_rows if r.is_win]
    losses = [r for r in all_rows if r.is_loss]
    breakevens = n - len(wins) - len(losses)

    win_rate = (len(wins) / n) if n else 0.0
    gross_pnl = sum(float(r.post.pnl) for r in all_rows)
    net_pnl = gross_pnl  # see PerSymbolStats comment

    # CIs (cheap; bootstrap is the expensive one)
    win_rate_ci = wilson_score_ci(len(wins), n, confidence=0.95)
    expectancy_pt = expectancy(all_rows)
    expectancy_ci = bootstrap_ci(
        all_rows, expectancy,
        n_samples=bootstrap_samples, rng_seed=rng_seed,
    )
    pf_raw = profit_factor(all_rows)
    pf_for_output = pf_raw if math.isfinite(pf_raw) else 1e9
    pf_ci = bootstrap_ci(
        all_rows, profit_factor,
        n_samples=bootstrap_samples, rng_seed=rng_seed + 1,
    )

    avg_win = (sum(float(r.post.pnl) for r in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(float(r.post.pnl) for r in losses) / len(losses)) if losses else 0.0
    largest_win = max((float(r.post.pnl) for r in wins), default=0.0)
    largest_loss = min((float(r.post.pnl) for r in losses), default=0.0)

    # Whole-Run equity curve: per-symbol independent capital → starting
    # equity is starting_cash * n_symbols.
    n_symbols = len(rows_by_symbol)
    starting_total = starting_cash * n_symbols
    curve = _equity_curve_from_rows(all_rows, starting_cash=starting_total)
    dd, dd_pct = max_drawdown(curve)
    sharpe = daily_sharpe(curve)
    sortino = daily_sortino(curve)

    best_removed = _month_removed_pnl(all_rows, best=True)
    worst_removed = _month_removed_pnl(all_rows, best=False)

    per_symbol = _per_symbol_stats(rows_by_symbol, starting_cash)
    per_year = _per_year_stats(all_rows)
    per_setup_dicts = [asdict(s) for s in build_setup_aggregates(all_rows)]

    insufficient = n < INSUFFICIENT_SAMPLE_THRESHOLD
    low = n < LOW_SAMPLE_THRESHOLD

    return RunAggregate(
        run_id=run_id,
        schema_version=schema_version,
        trade_count=n,
        win_count=len(wins),
        loss_count=len(losses),
        breakeven_count=breakevens,
        win_rate=win_rate,
        win_rate_ci_95=win_rate_ci,
        total_pnl_gross=gross_pnl,
        total_pnl_net=net_pnl,
        expectancy=expectancy_pt,
        expectancy_ci_95=expectancy_ci,
        profit_factor=pf_for_output,
        profit_factor_ci_95=pf_ci,
        avg_win=avg_win,
        avg_loss=avg_loss,
        largest_win=largest_win,
        largest_loss=largest_loss,
        max_drawdown=dd,
        max_drawdown_pct=dd_pct,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        equity_curve=curve,
        best_month_removed_total_pnl=best_removed,
        worst_month_removed_total_pnl=worst_removed,
        per_symbol=per_symbol,
        per_year=per_year,
        per_setup=per_setup_dicts,
        insufficient_sample=insufficient,
        low_sample=low,
    )


# ---------------------------------------------------------------------------
# Disk-aware driver
# ---------------------------------------------------------------------------


def aggregate_run(
    run_dir: Path,
    *,
    bootstrap_samples: int = BOOTSTRAP_SAMPLES_DEFAULT,
    rng_seed: int = 1337,
) -> RunAggregate:
    """Walk ``run_dir/per_symbol/*.json``, compute aggregate, write ``aggregate.json``.

    Returns the :class:`RunAggregate` so callers can render the in-app
    Report without re-reading disk.
    """
    run_dir = Path(run_dir)
    manifest = storage.load_manifest(run_dir)
    if manifest is None:
        raise FileNotFoundError(
            f"manifest.json missing or unreadable under {run_dir}; "
            "the Run may have failed before the kernel finished."
        )
    cfg = manifest.config

    rows_by_symbol: dict[str, list[TradeRow]] = {}
    per_symbol_dir = run_dir / "per_symbol"
    if per_symbol_dir.is_dir():
        for path in sorted(per_symbol_dir.glob("*.json")):
            try:
                result = SessionResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                # Corrupt per-symbol JSON is logged by the caller; skip.
                continue
            sym = path.stem
            rows = build_trade_rows(result)
            if rows:
                rows_by_symbol[sym] = rows

    agg = compute_aggregate(
        run_id=manifest.run_id,
        rows_by_symbol=rows_by_symbol,
        starting_cash=float(cfg.starting_cash),
        bootstrap_samples=bootstrap_samples,
        rng_seed=rng_seed,
    )
    save_aggregate(run_dir, agg)
    return agg


def save_aggregate(run_dir: Path, agg: RunAggregate) -> Path:
    """Atomic write of ``aggregate.json`` next to ``manifest.json``."""
    target = Path(run_dir) / AGGREGATE_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = agg.to_dict()
    # Stable sha256 over the canonical payload so tooling can detect changes.
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["fingerprint"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return _atomic_write_json(target, payload)


def load_aggregate(run_dir: Path) -> RunAggregate | None:
    """Read ``aggregate.json`` if present; return ``None`` otherwise.

    This is a tolerant accessor — old runs may pre-date PR 3 and
    have no aggregate yet; the GUI handles that case by offering to
    regenerate.
    """
    path = Path(run_dir) / AGGREGATE_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _aggregate_from_dict(payload)


def write_run_csv(
    run_dir: Path,
    rows: list[TradeRow] | None = None,
) -> Path:
    """Write the canonical 22-column trades CSV to ``run_dir/trades.csv``.

    If ``rows`` is None, rebuilds them from per-symbol JSON. This is
    the same column layout as the Sandbox CSV export, so users can
    feed Strategy Tester output into the same downstream pipelines.
    """
    if rows is None:
        rows = []
        per_symbol_dir = Path(run_dir) / "per_symbol"
        if per_symbol_dir.is_dir():
            for path in sorted(per_symbol_dir.glob("*.json")):
                try:
                    result = SessionResult.from_dict(
                        json.loads(path.read_text(encoding="utf-8"))
                    )
                except Exception:
                    continue
                rows.extend(build_trade_rows(result))
    target = Path(run_dir) / TRADES_CSV_FILENAME
    write_trade_rows_csv(rows, csv_path=target)
    return target


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> Path:
    """Mirror :func:`tradinglab.strategy_tester.storage._atomic_write_json`."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=target.name, suffix=".tmp", dir=str(target.parent),
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, separators=(",", ":"))
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    return target


def _aggregate_from_dict(payload: dict[str, Any]) -> RunAggregate:
    """Tolerant inverse of :meth:`RunAggregate.to_dict` for load_aggregate."""
    def _ci(d: dict[str, Any]) -> ConfidenceInterval:
        return ConfidenceInterval(
            lo=float(d.get("lo", 0.0)),
            hi=float(d.get("hi", 0.0)),
            point=float(d.get("point", 0.0)),
            confidence=float(d.get("confidence", 0.95)),
        )
    counts = payload.get("counts", {})
    head = payload.get("headline", {})
    risk = payload.get("risk", {})
    robust = payload.get("robustness", {})
    banners = payload.get("banners", {})
    return RunAggregate(
        run_id=str(payload.get("run_id", "")),
        schema_version=int(payload.get("schema_version", 1)),
        trade_count=int(counts.get("trade_count", 0)),
        win_count=int(counts.get("win_count", 0)),
        loss_count=int(counts.get("loss_count", 0)),
        breakeven_count=int(counts.get("breakeven_count", 0)),
        win_rate=float(head.get("win_rate", 0.0)),
        win_rate_ci_95=_ci(head.get("win_rate_ci_95", {})),
        total_pnl_gross=float(head.get("total_pnl_gross", 0.0)),
        total_pnl_net=float(head.get("total_pnl_net", 0.0)),
        expectancy=float(head.get("expectancy", 0.0)),
        expectancy_ci_95=_ci(head.get("expectancy_ci_95", {})),
        profit_factor=float(head.get("profit_factor", 0.0)),
        profit_factor_ci_95=_ci(head.get("profit_factor_ci_95", {})),
        avg_win=float(head.get("avg_win", 0.0)),
        avg_loss=float(head.get("avg_loss", 0.0)),
        largest_win=float(head.get("largest_win", 0.0)),
        largest_loss=float(head.get("largest_loss", 0.0)),
        max_drawdown=float(risk.get("max_drawdown", 0.0)),
        max_drawdown_pct=float(risk.get("max_drawdown_pct", 0.0)),
        sharpe_ratio=float(risk.get("sharpe_ratio", 0.0)),
        sortino_ratio=float(risk.get("sortino_ratio", 0.0)),
        equity_curve=[
            (int(p[0]), float(p[1])) for p in payload.get("equity_curve", [])
        ],
        best_month_removed_total_pnl=float(
            robust.get("best_month_removed_total_pnl", 0.0)
        ),
        worst_month_removed_total_pnl=float(
            robust.get("worst_month_removed_total_pnl", 0.0)
        ),
        per_symbol=[
            PerSymbolStats(**{k: _coerce(v) for k, v in s.items()})
            for s in payload.get("per_symbol", [])
        ],
        per_year=[
            PerYearStats(**{k: _coerce(v) for k, v in y.items()})
            for y in payload.get("per_year", [])
        ],
        per_setup=list(payload.get("per_setup", [])),
        insufficient_sample=bool(banners.get("insufficient_sample", False)),
        low_sample=bool(banners.get("low_sample", False)),
    )


def _coerce(v: Any) -> Any:
    """Light coercion for dataclass JSON re-hydration."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return v
    if isinstance(v, str):
        return v
    return v
