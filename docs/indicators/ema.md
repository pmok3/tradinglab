# Exponential Moving Average (EMA)

EMA is a moving average that gives more weight to recent prices. That makes it faster than SMA and more useful when you want trend shifts and pullbacks to show up sooner.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| length | 20 | 1-2000 | Number of bars used in the average. Lower values react faster; higher values smooth more. |

## Reading the Indicator

A rising EMA shows momentum and trend alignment. Price holding above a rising EMA often acts like dynamic support; in downtrends, the EMA can act like dynamic resistance.

## When to Use

Use EMA when you need a faster trend guide than SMA, especially for intraday continuation trades, pullback entries, and momentum names.

## ⚠️ When NOT to Use

Do not over-trust EMA in sideways chop. Its faster response is helpful in trends, but in ranges it can bait you into repeated false crosses.

## Common Setups

- Price reclaim of the EMA after a pullback.
- Fast EMA crossing above a slow EMA for bullish momentum.
- Using the 9/20 or 20/50 EMA stack to define trend structure.

## Tips

Intraday traders often use EMA because it hugs price better than SMA. Let EMA tell you the path of least resistance, then use RSI or MACD for timing. If price keeps rejecting the EMA, respect the trend until that behavior changes.

## References

- Gerald Appel popularized EMA-based momentum work through MACD.
- Common benchmark lengths include 9, 20, 50, and 200.
