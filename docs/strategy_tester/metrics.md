# Strategy Tester — Metrics glossary

Plain-English definitions of every statistic the Strategy Tester
computes. If you've read a trading-blog metric and want to know
*exactly* how this tool counts it, you're in the right place.

> All formulas mirror what's in `src/tradinglab/strategy_tester/report.py`.
> If math here disagrees with the code, the code wins — please file
> an issue.

---

## Trade outcome buckets

A **trade** is one round-trip: an entry fill followed by a
position-flat exit (the exit may be one or many legs). The tester
classifies each closed trade as:

- **Win** — `post.pnl > 0` (gross P&L positive)
- **Loss** — `post.pnl < 0`
- **Breakeven** — `post.pnl == 0` (rare; reported separately so
  the win/loss buckets are unambiguous)

`trade_count = win_count + loss_count + breakeven_count`.

---

## Win rate

```
win_rate = wins / trade_count
```

**95% confidence interval — Wilson score interval.** The Wilson
interval is preferred over the naive `mean ± 1.96·SE` because it
behaves correctly at the boundaries (a 10-of-10 sample gets a CI
of roughly `[0.72, 1.00]`, not the nonsensical `[1.00, 1.00]`).

We use the closed-form, scipy-free formula — see
[`wilson_score_ci`](../../src/tradinglab/strategy_tester/report.py).

---

## Expectancy

The **average dollar P&L per trade**:

```
expectancy = win_rate · avg_win  +  loss_rate · avg_loss
           = total_pnl_gross / trade_count        (equivalent)
```

This is the *discretionary-trader convention* (signed dollars per
trade). It is **not** the "R-multiple expectancy" that some texts
use — see R-multiple below.

**95% confidence interval — Bootstrap (B = 10 000 samples).** We
sample `trade_count` rows with replacement from the actual trade
list 10 000 times, compute each resample's expectancy, then take
the 2.5th and 97.5th percentiles. RNG is seeded with `rng_seed=1337`
so identical inputs produce identical CIs across runs and machines.

---

## Profit factor

```
profit_factor = sum(winning P&L) / |sum(losing P&L)|
```

- A profit factor of `2.0` means winners returned twice the dollar
  amount the losers gave back.
- **No losers** → `inf`. The report clamps to `1e9` for display
  (rendered as `∞`).
- **No winners** → `0`.

**95% confidence interval — Bootstrap (B = 10 000 samples).** Same
construction as expectancy, with the seed offset by `+1` to
decorrelate from the expectancy bootstrap. Degenerate resamples
(no losses, infinite PF) are mapped to `1e9` before the percentile
arithmetic.

---

## Equity curve + max drawdown

Each fill is applied in timestamp order to a running equity counter
that starts at `starting_cash × n_symbols` (because each symbol has
its own independent sandbox).

**Max drawdown** is the largest peak-to-trough loss the running
equity ever experienced:

```
max_drawdown      = min_i (equity_i - max_{j ≤ i} equity_j)      (dollars)
max_drawdown_pct  = max_drawdown / starting_total                 (fraction)
```

A `-0.12` `max_drawdown_pct` means the running equity dipped
12% below the highest prior peak at some point.

---

## Sharpe ratio (daily, annualised)

We resample the equity curve to **daily** granularity (UTC), compute
day-over-day returns, then:

```
sharpe = (mean(daily_returns) / stddev(daily_returns, ddof=1)) · sqrt(252)
```

Notes:
- Annualised by `sqrt(252)` — the standard US-equities convention.
- Uses **daily-equity** returns, **not** per-trade returns. A
  per-trade Sharpe over-estimates risk-adjusted return for
  intermittent strategies because it ignores the days the strategy
  wasn't in a trade.
- `ddof=1` for the unbiased sample stddev.

---

## Sortino ratio (daily, annualised)

Same setup as Sharpe, but the denominator uses **downside deviation**:

```
sortino = (mean(daily_returns) /
           stddev(negative_daily_returns_only, ddof=1)) · sqrt(252)
```

Returns greater than zero are still included in the *mean*
numerator — only the *denominator* is restricted to losing days.

---

## MAE / MFE

For each trade, two per-bar high-water marks are recorded:

- **MAE — Maximum Adverse Excursion**: the deepest loss the position
  experienced *while still open* (in dollars and %).
- **MFE — Maximum Favorable Excursion**: the largest unrealized
  gain.

These tell you whether trades that ended green spent time underwater
first (high MAE), and whether trades that ended red gave back a
realized winner (high MFE). Surface as dots on the per-trade
screenshots.

---

## R-multiple

```
R = |entry_price − initial_stop_price| · shares
R-multiple = trade_pnl / R
```

**Where it's available.** R requires an explicit initial stop on
the EntryStrategy. ExitStrategies that have an explicit stop leg
satisfy this. Strategies whose exit is purely time-based (e.g.
EOD kill-switch only) carry `r_multiple = None`.

The CSV column shows `n/a` rather than a number for those rows so
you can filter them out cleanly in a spreadsheet.

---

## Per-symbol breakdown

The aggregate carries one `PerSymbolStats` row per ticker, with
the same metric set computed against just that ticker's trades:

- `trade_count`, `wins`, `losses`
- `win_rate`
- `total_pnl_gross` / `total_pnl_net`
- `avg_pnl_net`
- `profit_factor`
- `max_drawdown` (on that symbol's equity, not the cross-symbol curve)

Use it as a quick "is this strategy concentrated in 1-2 names?"
sanity check.

---

## Per-year breakdown

Same metric set sliced by the **UTC calendar year of the exit
timestamp**:

- `year`, `trade_count`, `wins`, `losses`
- `win_rate`
- `total_pnl_net`
- `expectancy`
- `profit_factor`
- `max_drawdown`

This is the **regime-fragility check**. A strategy with a wonderful
overall expectancy but a horrible single year tells you the
aggregate is being carried by a small window — be skeptical.

---

## Best-month-removed / worst-month-removed P&L

A robustness probe: re-aggregate after removing the calendar month
with the highest total P&L (and, separately, the month with the
lowest). If the strategy *depends* on one freak month, this number
will be dramatically smaller than `total_pnl_net`.

These show up as two extra numbers on the cover sheet (HTML/PDF
reports).

---

## Sample-size banners

| N | Banner | Notes |
|---|---|---|
| ≥ 100 | (none) | Statistics are usable. |
| 30–99 | **Low sample** | Bootstrap CIs are wide; trust the direction more than the magnitude. |
| < 30  | **Insufficient sample** | Use the result to decide *what to test next*, not *whether to trade it*. |

These are conservative thresholds — pulled from the mathematician's
review notes for the Strategy Tester design. There's nothing magic
about 30 vs. 50; both are arbitrary cutoffs for "the CI is wider
than the headline number."

---

## What we deliberately *do not* report

- **Per-trade Sharpe.** Inflates by ignoring no-trade days. We
  report daily-equity Sharpe instead.
- **Compound annual growth rate (CAGR).** The tester runs on
  per-symbol independent capital pools, so there is no portfolio
  growth curve to compound. Use the equity curve instead.
- **Total P&L as a percentage of capital deployed.** The starting
  cash is per-symbol; a single ticker that lost $5K against $100K
  notional is `-5%` but the cross-symbol number is meaningless.
  Use `max_drawdown_pct` for risk-sized comparisons.

---

## Where each metric lives in the code

- `wilson_score_ci`, `bootstrap_ci` — `strategy_tester/report.py`
- `expectancy`, `profit_factor` — `strategy_tester/report.py`
- `max_drawdown`, `daily_sharpe`, `daily_sortino` — same file
- MAE / MFE recording — `backtest/journal.py` (`PostTradeReview`)
- R-multiple — `backtest/performance.py:TradeRow`

If you find a discrepancy between this doc and the code, please
open an issue against `pmok3/tradinglab`.
