# Chandelier Stops

Chandelier Stops are ATR-based trailing stop lines plotted directly on price. They are built for trade management: one line trails long positions, and the other trails short positions.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| lookback | 22 | 1-500 | Window for the highest high and lowest low. |
| atr_period | 22 | 2-500 | ATR smoothing period. |
| multiplier | 3.0 | 0.5-8.0 | ATR multiple used to place the stop. |
| ma_type | RMA | SMA, EMA, WMA, RMA | ATR smoothing kernel. |

## Reading the Indicator

If price is above the long stop, the long trend is still intact. If price is below the short stop, the short trend is still intact. The lines move like stairs because they ratchet only in the trade's favor.

## When to Use

Use Chandelier Stops for exit management, trend trade protection, and staying in winners longer than a fixed target might allow.

## ⚠️ When NOT to Use

Do not use it as a stand-alone entry trigger. It can also feel too wide for very short-term scalps or too noisy in messy, range-bound tape.

## Common Setups

- `22,22,3` for classic swing-trade trailing stops.
- Tighter settings like `14,14,2.5` for faster-moving trades.
- Trail below higher lows in a long and let the stop confirm the trend.

## Tips

The ratchet is the whole point: the long stop never moves down, and the short stop never moves up. If the staircase starts flattening out, momentum may be fading.

## References

- Chuck LeBeau, Chandelier Exit framework (1995)
- TradingLab built-in indicator source: `src/tradinglab/indicators/chandelier.py`
