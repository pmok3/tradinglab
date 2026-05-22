# Simple Moving Average (SMA)

SMA is the average closing price over the last N bars. It smooths noise and gives you a clean read on trend direction, pullbacks, and dynamic support/resistance.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| length | 20 | 1-2000 | Number of bars used in the rolling average. Higher = smoother, slower. |

## Reading the Indicator

A rising SMA means trend pressure is up; a falling SMA means trend pressure is down. Price above the SMA usually signals bullish control, while price below it suggests weakness.

## When to Use

Use SMA when you want a simple trend filter, a pullback reference, or a higher-timeframe bias line. It works well for swing structure and for spotting dynamic support/resistance.

## ⚠️ When NOT to Use

Avoid leaning on SMA alone in choppy, range-bound markets. Price can whip above and below it repeatedly and generate low-quality crosses.

## Common Setups

- Price crosses above/below the SMA for trend confirmation.
- Fast SMA crossing a slow SMA for a trend-change signal.
- Pullback into a rising SMA, then continuation on reclaim.

## Tips

For intraday trading, shorter SMAs react faster but whipsaw more. Use it with RSI or MACD so you are not taking every cross blindly. A 20 SMA is a good baseline; longer lengths help define the bigger trend.

## References

- John J. Murphy, *Technical Analysis of the Financial Markets*
- Most charting platforms use 20, 50, and 200 as common benchmark lengths.
