# Relative Strength Index (RSI)

RSI is Wilder's 1978 momentum oscillator that measures the speed of recent gains versus losses on a 0-100 scale. It is best used as a momentum context tool, not as an automatic buy/sell trigger.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| length | 14 | 2-2000 | Lookback window used to smooth gains and losses. Lower = faster, noisier readings. |

## Reading the Indicator

Above 70 is often called overbought and below 30 oversold, but context matters. In strong uptrends RSI can stay elevated; in downtrends it can stay depressed. Centerline 50 helps judge whether momentum is mostly bullish or bearish.

## When to Use

Use RSI for momentum confirmation, divergence, and pullback timing. It is especially useful when price is near VWAP, support, or resistance and you want extra evidence.

## ⚠️ When NOT to Use

Do not treat overbought as an automatic short signal or oversold as an automatic buy signal in strong trends. Trending markets can pin RSI for longer than you expect.

## Common Setups

- Bullish or bearish divergence versus price.
- Oversold bounce back above 30.
- Trend pullback that holds 40-50 in an uptrend.

## Tips

RSI 7 is popular for scalping, 14 is the standard default, and 21 is calmer for swing trading. Common mistake: shorting RSI 80 in a strong trend just because it looks stretched. Pair it with VWAP for intraday location.

## References

- J. Welles Wilder Jr., *New Concepts in Technical Trading Systems* (1978)
- Standard thresholds: 70/30, with 50 as a useful momentum divider.
