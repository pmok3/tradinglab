# `strategy_tester/report.py` — Spec

## Purpose
Whole-Run statistical aggregation: walks per-symbol `SessionResult`
JSON on disk, computes the headline metrics + per-symbol + per-year +
robustness probes, and persists the result as `aggregate.json` next
to `manifest.json`. The Strategy tab's Report view reads this file
(or accepts an in-memory `RunAggregate` from `compute_aggregate`).

## Public API
- `AGGREGATE_FILENAME = "aggregate.json"` — canonical aggregate artifact name in a Run directory.
- `TRADES_CSV_FILENAME = "trades.csv"` — canonical CSV artifact name in a Run directory.
- `BOOTSTRAP_SAMPLES_DEFAULT = 10_000` — default resample count for expectancy / profit-factor confidence intervals.
- `RunAggregate` — frozen dataclass holding the whole-Run rollup
  (trade counts, headline metrics + CIs, risk metrics, equity curve,
  per-symbol / per-year breakouts, banners).
- `PerSymbolStats` / `PerYearStats` — rows in the per-X breakouts.
- `ConfidenceInterval(lo, hi, point, confidence)` — 2-sided CI.
- `aggregate_run(run_dir, *, bootstrap_samples=10_000, rng_seed=1337,
  interval_overrides=None, write_csv=False) -> RunAggregate` — disk-aware driver. Reads
  `manifest.json` + `per_symbol/*.json` → builds aggregate → writes
  `aggregate.json` atomically → returns the aggregate. When
  `write_csv=True`, writes `trades.csv` from the same in-memory
  `TradeRow` list so finalization does not re-read per-symbol JSON.
  `interval_overrides` is a list of single-interval rewrite warnings
  surfaced under the aggregate's `banners.interval_overrides`.
- `compute_aggregate(*, run_id, rows_by_symbol, starting_cash,
  bootstrap_samples=10_000, rng_seed=1337, schema_version=1,
  interval_overrides=None)
  -> RunAggregate` — pure-function math kernel. The unit-test entry
  point.
- `save_aggregate(run_dir, agg) -> Path` /
  `load_aggregate(run_dir) -> RunAggregate | None` — disk round-trip.
- `write_run_csv(run_dir, rows=None) -> Path` — writes the canonical
  22-column trades CSV via
  `backtest.performance.write_trade_rows_csv`. Passing `rows` avoids
  loading `per_symbol/*.json`; omitting it loads rows on demand.
- Stat primitives: `wilson_score_ci`, `bootstrap_ci`,
  `profit_factor`, `expectancy`, `max_drawdown`, `daily_sharpe`,
  `daily_sortino`.

## Statistics recipe (per plan.md §Stats & methodology)
- **Win rate CI**: Wilson score interval (closed form, no scipy
  dependency). Robust for small N and extreme win rates — the regime
  the user's Strategy Tester operates in.
- **Expectancy CI**: 10 000-sample non-parametric bootstrap with
  fixed `rng_seed=1337` so two runs over the same row list return
  identical bounds. Configurable; tests use 200 samples for speed.
- **Profit factor CI**: bootstrap on the same row list (separate
  seed offset by +1 to decorrelate the two bootstraps).
- **Sharpe / Sortino**: computed off daily-resampled equity returns
  (last-equity-of-day per UTC calendar day), ddof=1, annualised by
  √252.
- **Max drawdown**: dollar peak-to-trough on the whole-Run equity
  curve; also exported as fraction of peak.
- **Best/worst-month-removed total P&L**: robustness probe — total
  P&L with the calendar month carrying the most positive (resp.
  negative) cumulative P&L removed from the row list. Less than 2
  months represented → no-op.
- **Per-year stats**: same metric set sliced by `post.exit_ts`'s
  UTC calendar year. Each year-local max drawdown is computed from a
  $0 baseline so year-on-year compounding doesn't dominate the
  number.
- **Sample-size banners**: `insufficient_sample` when N<30,
  `low_sample` when N<100. Both surface on the aggregate; the GUI
  decides whether to render a banner.

## Inf / NaN handling
- Profit factor on a row list with zero losses returns `math.inf`
  internally; `compute_aggregate` clamps the output to `1e9` for
  JSON safety. Bootstrap CIs apply the same clamp to degenerate
  resamples.
- Empty row lists → all metrics 0.0, all CIs `(0,0,0,confidence)`.

## Reproducibility invariant
- `compute_aggregate(rows_a) == compute_aggregate(rows_b)` for any
  permutation of the same row list (bootstrap is invariant under
  permutation thanks to `random.Random(seed)` and the fact that the
  bootstrap statistic doesn't care about row order).
- The aggregate's JSON serialisation includes a `fingerprint` =
  sha256 of the canonical-JSON-sorted-keys output, truncated to 16
  hex. Downstream tooling can detect "aggregate is stale" by
  comparing fingerprints.

## Aggregate JSON schema (v1)
```json
{
  "run_id": "abc123def456",
  "schema_version": 1,
  "counts": {"trade_count": 87, "win_count": 51, "loss_count": 33, "breakeven_count": 3},
  "headline": {
    "win_rate": 0.586,
    "win_rate_ci_95": {"lo": 0.478, "hi": 0.687, "point": 0.586, "confidence": 0.95},
    "total_pnl_gross": 12345.67,
    "total_pnl_net": 12345.67,
    "expectancy": 141.9, "expectancy_ci_95": {...},
    "profit_factor": 2.13, "profit_factor_ci_95": {...},
    "avg_win": 312.4, "avg_loss": -187.2,
    "largest_win": 1842.0, "largest_loss": -812.5
  },
  "risk": {"max_drawdown": 4321.0, "max_drawdown_pct": 0.043, "sharpe_ratio": 1.42, "sortino_ratio": 1.89},
  "robustness": {"best_month_removed_total_pnl": 9876.5, "worst_month_removed_total_pnl": 14210.1},
  "equity_curve": [[1704096000, 100000.0], ...],
  "per_symbol": [{"symbol": "AAPL", "trade_count": ...}, ...],
  "per_year": [{"year": 2024, "trade_count": ...}, ...],
  "per_setup": [...],
  "banners": {"insufficient_sample": false, "low_sample": false, "interval_overrides": []},
  "fingerprint": "deadbeefcafe1234"
}
```
`equity_curve` timestamps are UTC epoch seconds, matching
`PostTradeReview.entry_ts` / `exit_ts` and `SessionResult.equity_curve`.

## Disk loading / finalization performance
- `aggregate_run` and `write_run_csv(rows=None)` share a single
  per-symbol row-loading helper. Runs with fewer than 4 symbol JSON
  files load serially to avoid thread overhead; larger runs load
  `per_symbol/*.json` on a `ThreadPoolExecutor` capped at 32 workers,
  preserving sorted filename order when building `rows_by_symbol`.
- The helper skips corrupt per-symbol JSON or symbols with no closed
  trades, matching the previous tolerant aggregation behavior.

## Integration with `runner.run`
- The runner calls `report.aggregate_run(run_dir, write_csv=True)`
  after the symbol loop completes, on both DONE and CANCELLED status.
  That single pass writes both `aggregate.json` and `trades.csv`.
  Failures are logged but do not alter the Run status — the Run is
  judged on `SessionResult` JSON correctness; the aggregate and CSV are
  derived artifacts.

## Testing
- `tests/unit/strategy_tester/test_report.py` —
  - Wilson CI edge cases: N=0, N=1, p=0, p=1, p=0.5.
  - Bootstrap CI determinism: same rng_seed produces identical
    bounds across two runs.
  - Profit factor: with zero losses → ∞ → clamped to 1e9 in the
    aggregate; with zero wins → 0.0.
  - Expectancy on N=0, all wins, all losses.
  - Max drawdown on monotonically increasing / decreasing /
    sawtooth curves.
  - Daily Sharpe / Sortino on a constant-return curve → 0.0 (no
    variance).
  - Best/worst-month-removed: one-month → no-op, multi-month →
    removes the right one.
  - Per-year breakout slices by UTC year boundary.
  - Per-symbol breakout: 3-symbol case orders alphabetically.
  - `compute_aggregate` permutation invariance.
  - `save_aggregate` / `load_aggregate` round-trip.
  - `aggregate_run(run_dir)` end-to-end against PR-1 test fixtures.
  - `aggregate_run(write_csv=True)` writes `trades.csv` without a
    second per-symbol JSON parse pass; row-loader worker sizing is
    pinned for small and large runs.

## See also
- [evaluator](evaluator.spec.md) — produces the SessionResult that
  feeds the aggregator.
- [runner](runner.spec.md) — invokes `aggregate_run` after the
  symbol loop.
- [screenshot](screenshot.spec.md) — sibling per-trade artifact.
- `backtest/performance.spec.md` — reused `TradeRow` /
  `build_trade_rows` / `build_setup_aggregates` /
  `write_trade_rows_csv` primitives.
