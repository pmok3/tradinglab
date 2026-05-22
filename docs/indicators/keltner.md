# Keltner Channels

Keltner Channels are a moving-average envelope built from range or ATR. They give you a price channel that adapts to volatility and is useful for breakout and trend-following work.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| length | 20 | 2-2000 | Centerline lookback. Also used for the original method's range average. |
| multiplier | 2.0 | 0.1-20.0 | Sets channel width. |
| atr_length | 10 | 2-2000 | ATR lookback for the modern `atr` method. |
| ma_type | EMA | SMA, EMA, WMA, RMA | Centerline moving-average type. |
| atr_ma_type | RMA | SMA, EMA, WMA, RMA | ATR smoothing type for the modern method. |
| method | atr | atr, original | Uses the modern ATR-based formula or the original range-based version. |

## Reading the Indicator

Price pushing above the upper band shows upside expansion; below the lower band shows downside expansion. Repeated closes hugging a band usually signal trend strength rather than an automatic fade.

## When to Use

Use Keltner Channels for breakout trading, trend continuation, and volatility framing. They are also useful in Bollinger Band/Keltner squeeze workflows.

## ⚠️ When NOT to Use

Do not fade every outside-band print. Strong trends can ride the channel for longer than expected.

## Common Setups

- `KC(20,2)` for general trend work.
- Bollinger Bands inside Keltner Channels for squeeze scans.
- Original method when you want the classic range-based variant.

## Tips

When Keltner sits inside Bollinger Bands, volatility is often compressed. When price breaks out after that compression, traders often expect expansion.

## References

- Chester W. Keltner, *How to Make Money in Commodities* (1960)
- Modern ATR-based variant widely used in charting platforms
- TradingLab built-in indicator source: `src/tradinglab/indicators/keltner.py`
