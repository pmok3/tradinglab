# Strategy Tester Guide

A 5-minute walkthrough of the Strategy Tester — TradingLab's mechanical
backtester for **paired entry + exit strategies** you've already
saved in the Entries and Exits tabs.

> **What it is.** Pick a saved EntryStrategy + a saved ExitStrategy,
> a universe of symbols, and a date range. The tester replays each
> symbol's bars through a Tk-free engine, records every fill, and
> produces a Report with statistics, per-trade screenshots, an HTML
> view, and a PDF you can email to a collaborator.

> **What it isn't.** This is *not* an optimizer. There is no
> parameter sweep, no walk-forward, no curve-fitting. Discretionary
> tooling first — the tester exists to *validate* your hypothesis,
> not to invent one.

---

## Quick start (5 minutes)

1. **Save the building blocks first.** The Strategy tab does not let
   you author entries / exits in place — go to the **Entries** tab,
   create or pick the entry you want, repeat in **Exits**. The
   Strategy tab's pickers only show saved strategies.

2. **Open the Strategy tab.** It's the last tab in the top notebook,
   right after Exits.

3. **Configure** the run:
   - **Entry strategy** — pick the saved EntryStrategy.
   - **Exit strategy** — pick the saved ExitStrategy.
   - **Universe** — three modes:
     - `Symbols list` — comma- or semicolon-separated tickers
       (e.g. `AAPL, MSFT, NVDA`)
     - `Watchlist` — pick one of your saved watchlists by name
     - `Preset` — `sp500_seed` / `nasdaq100_seed` / `dow30_seed` /
       `megacaps` (shows a yellow **survivorship bias** banner)
   - **Date range** — `YTD / Last 1Y / 3Y / 5Y / 10Y / Max / Custom`.
   - **Interval** — `1d`, `5m`, or `1m`.
   - **Per-trade screenshots** — on by default; uncheck for a fast
     "stats-only" run.
   - **Advanced** (collapsed by default) — slippage in bps (default
     `5`), commission per trade (default `0`), commission per share
     (default `0`). Costs are applied to each fill; the report shows
     **gross AND net** P&L both.
   - **Run label** — optional free-text label to find this Run later.

4. **Click Run.** A daemon worker thread drives the kernel — the
   UI stays responsive. Progress shows up in the status line. Hit
   **Stop** at any time; partial results are preserved.

5. **Read the Report** (right pane):
   - **Headline** — Trades / Win rate (with 95% Wilson CI) /
     Expectancy ($ + 95% bootstrap CI) / Profit factor / Gross + net
     P&L / Max drawdown / Sharpe / Sortino.
   - **Sample-size banner** — yellow box if N<30 (Insufficient) or
     N<100 (Low). Treat the headline as illustrative when banners
     fire.
   - **Per-symbol** tab — one row per ticker.
   - **Per-year** tab — regime-fragility check.

6. **Export**:
   - **Open run folder** — opens the `%LOCALAPPDATA%\TradingLab\
     strategy_tests\<run_id>-<ts>\` directory in Explorer.
   - **Export CSV…** — copies `trades.csv` (22 columns, identical
     to the Sandbox post-mortem CSV format).
   - **Export HTML…** — writes a self-contained `report.html` with
     relative `screenshots/<file>.png` links — zip the run dir for
     a portable report.
   - **Export PDF…** — multi-page PDF (cover + breakouts + equity
     curve + one landscape page per trade screenshot, capped at
     200 pages).

7. **Recent runs sidebar** (bottom of the Configure pane) lists
   your last 50 runs. **Load** to repaint the Report pane against
   a prior run. **Delete…** to free disk.

---

## Output artifacts

Every Run writes to `%LOCALAPPDATA%\TradingLab\strategy_tests\<run_id>-<iso_ts>\`:

```
config.json              # the TestConfig that was run
manifest.json            # status + counters + started/finished_at
per_symbol/<SYM>.json    # one SessionResult per ticker
aggregate.json           # whole-Run rollup (the "Report")
trades.csv               # 22-column flat trade list
screenshots/             # one PNG per trade (if screenshots on)
  <SYM>_<order_id>_post.png
report.html              # written by Export HTML…
report.pdf               # written by Export PDF…
```

`run_id` is a `sha256(canonical_config_json + engine_version +
rng_seed)` truncated to 12 hex — deterministic. Re-running an
identical config produces a **fresh** Run with a new timestamp
(old Runs stay browsable in Recent runs).

---

## Cost model

The default is `5 bps slippage / $0 per trade / $0 per share`.
Slippage is applied to the fill price (worse direction). Both
commission components are subtracted from P&L for the net column.

Use the **Advanced** disclosure to override.

---

## Sample-size banners — what they mean

| N (closed trades) | Banner | What it means |
|---|---|---|
| ≥ 100 | (none) | Confidence intervals are usable. |
| 30–99 | **Low sample** (yellow) | CIs are wide; the headline numbers are still directional but treat them as soft. |
| < 30  | **Insufficient sample** (yellow) | The CIs are too wide to act on. Use the result to decide *what to test next*, not *whether to trade it*. |

The CIs themselves are not approximations:
- Win rate uses **Wilson score CI** (closed form, no scipy).
- Expectancy + profit factor use **10 000-sample bootstrap CIs**
  with a fixed `rng_seed=1337` so identical inputs produce identical
  numbers across machines.

---

## Re-running a stored Run

The Recent runs sidebar at the bottom of the Configure pane shows
your last 50 stored Runs. Pick a row and click **Load** to repaint
the right-side Report pane against that Run's `aggregate.json` — no
re-execution required.

Click **Delete…** to remove a Run from disk (frees both the JSON +
the screenshots folder).

---

## Glossary

For plain-English definitions of every metric the tester computes
(expectancy, profit factor, MAE, MFE, Wilson CI, bootstrap CI,
R-multiple, daily Sharpe, etc.) see
[`docs/strategy_tester/metrics.md`](strategy_tester/metrics.md).

---

## Limitations / non-goals

- **No parameter sweep / optimizer.** This is by design. If you want
  to try a different indicator length, save a new EntryStrategy and
  run it side-by-side; don't tweak the same one.
- **No walk-forward / in-sample-out-of-sample split.**
- **No options / futures / multi-leg.**
- **No borrow-fee / overnight-gap modeling** beyond the bps slippage.
- **Bar-close decision → next-bar-open fill by default.** Setting
  `evaluate_intrabar=True` on the EntryStrategy is honored but
  triggers a yellow warning banner under the entry picker because
  intrabar evaluation is more easily curve-fit.
- **Per-symbol independent capital.** Each symbol gets its own fresh
  `starting_cash` sandbox. There is no shared cash pool.
- **Survivorship bias.** When the universe is a "current S&P 500"-style
  preset, only currently-listed names are included. The tab shows a
  yellow banner reminding you.

---

## Architecture pointers (for contributors)

- Kernel orchestration: `src/tradinglab/strategy_tester/runner.py`
- Math kernel: `src/tradinglab/strategy_tester/report.py`
- HTML / PDF exporters: `src/tradinglab/strategy_tester/export.py`
- On-disk schema: `src/tradinglab/strategy_tester/storage.py`
- GUI tab: `src/tradinglab/gui/strategy_tab.py`
- Headless screenshots: `src/tradinglab/strategy_tester/screenshot.py`
- Tests: `tests/unit/strategy_tester/`, `tests/smoke/test_smoke_strategy.py`
- Spec catalog: `docs/SPEC_INDEX.md`
