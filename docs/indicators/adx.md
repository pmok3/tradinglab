# Average Directional Index

Average Directional Index, or ADX, measures trend strength on a 0-100 scale. It tells you whether a market is trending, but not which direction the trend is going.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| length | 14 | 2-2000 | Wilder smoothing period for +DI, -DI, and ADX. |

## Reading the Indicator

As a rule of thumb, ADX above 25 suggests a trending market, while ADX below 20 suggests a range. Rising ADX from a low base often means trend conditions are starting to build.

## When to Use

Use ADX to decide whether to trade trend setups or range setups. It works well as a filter for breakouts, pullbacks, and momentum trades.

## ⚠️ When NOT to Use

ADX does not tell you direction. Use +DI and -DI for that: +DI above -DI favors bulls, and -DI above +DI favors bears.

## Common Setups

- Only take breakout trades when ADX is rising.
- Fade support/resistance more confidently when ADX is under 20.
- Combine with moving averages or price structure for direction.

## Tips

A flat, low ADX can warn you that follow-through may be weak. An ADX line curling up from low levels is often the first sign that the market is leaving balance.

## References

- J. Welles Wilder Jr., *New Concepts in Technical Trading Systems* (1978)
- TradingLab built-in indicator source: `src/tradinglab/indicators/adx.py`
