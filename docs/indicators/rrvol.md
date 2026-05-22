# RRVOL

RRVOL compares a stock's RVOL to a benchmark symbol's RVOL at the same moment. It helps you find names that are unusually active relative to the chosen benchmark (broad market, sector ETF, peer, etc.), not just active because everything is moving.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| mode | simple | simple, cumulative, time_of_day | Chooses the RVOL flavor used for both the stock and the comparison symbol. |
| length | 20 | 1-500 | Lookback bars in `simple`, or lookback sessions in `cumulative` and `time_of_day`. |
| aggregator | mean | mean, median | Baseline average for both legs. |
| session_filter | regular_only | regular_only, regular_plus_premarket, extended | Which bars count in both calculations. |
| denominator_includes_current | False | True, False | Includes the current bar in the `simple` denominator. |
| z_score | False | True, False | Shows a rolling z-score of the RRVOL ratio. |
| compare_symbol | SPY | any valid ticker | The denominator benchmark. Editable combobox in the dialog with common ETF picks (SPY/QQQ/IWM/DIA + XL\* sector SPDRs); free-typing supported. Validated on Save and Close — invalid syntax pops an error and keeps the dialog open. |
| threshold_warn | 2.0 | 0.1-100.0 | Warning reference level for raw RRVOL. |
| threshold_extreme | 5.0 | 0.1-100.0 | Extreme reference level for raw RRVOL. |

## Reading the Indicator

A value above 1 means the stock is seeing stronger relative volume than the comparison symbol. A value below 1 means the stock is quieter than the benchmark's backdrop. If `z_score=True`, read 0 as normal and +2 as unusually strong relative activity.

## When to Use

Use RRVOL for screening, watchlist ranking, and deciding which names deserve attention on busy market days. Switch `compare_symbol` to a sector ETF (e.g. XLF for banks, XLE for energy) to surface idiosyncratic activity inside a moving sector.

## ⚠️ When NOT to Use

Do not treat RRVOL as a price signal by itself. The comparison symbol must trade alongside the primary on the same calendar — comparing a US single-stock to an overseas ETF will largely emit NaN due to misaligned sessions.

## Common Setups

- Scan for RRVOL above 1.5 with clean price breakouts.
- Use `time_of_day` to find intraday names outperforming the tape's normal rhythm.
- Pair with RVOL to separate market-wide activity from stock-specific activity.
- Switch `compare_symbol` to a sector ETF when the broad market is rotating — a stock pulling 1.5× its own sector's RVOL is doing something different from its peers.

## Tips

If the comparison symbol is wild and your stock still shows RRVOL well above 1, that is often where the real leadership hides.

## References

- TradingLab built-in indicator source: `src/tradinglab/indicators/rrvol.py`
- Benchmark-relative volume comparison as a market-context filter
