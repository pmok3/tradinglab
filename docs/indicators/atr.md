# Average True Range

Average True Range, or ATR, measures how much price is moving in raw price units. It is a volatility tool, not a directional signal.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| length | -1 | 2-2000 | Lookback. Auto-resolves to 14 in `rolling` mode or 20 in `tod` mode. |
| ma_type | RMA | SMA, EMA, WMA, RMA | Smoothing kernel for rolling ATR. Ignored in `tod`. |
| mode | rolling | rolling, tod | Uses classic rolling ATR or a time-of-day baseline. |
| session_filter | regular_only | regular_only, regular_plus_premarket, extended | Which bars count in `tod` mode. |
| aggregator | mean | mean, median | How the `tod` baseline is averaged. Median is more outlier-resistant. |

## Reading the Indicator

Higher ATR means bigger bars and wider movement. Rising ATR often shows expansion and participation; falling ATR usually means compression or consolidation.

## When to Use

Use ATR for position sizing, stop placement, target sizing, and judging whether a market is expanding or quiet. `tod` mode is especially useful intraday when you want to compare this bar's range to what is normal for that time of day.

## ⚠️ When NOT to Use

Do not treat ATR as bullish or bearish by itself. ATR can rise in both uptrends and selloffs.

## Common Setups

- ATR(14) to size stops around recent volatility.
- ATR ToD(20) to spot bars that are unusually large for that clock time.
- Pair ATR with trend tools for breakout vs consolidation reads.

## Tips

ATR expansion often travels with trend development. ATR contraction often marks balance, chop, or a market storing energy before a move.

## References

- J. Welles Wilder Jr., *New Concepts in Technical Trading Systems* (1978)
- TradingLab built-in indicator source: `src/tradinglab/indicators/atr.py`
