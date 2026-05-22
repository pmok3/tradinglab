# Moving Average Convergence Divergence (MACD)

MACD, created by Gerald Appel in 1979, is a momentum indicator built from moving averages. It helps you read trend acceleration and deceleration through three parts: MACD line, signal line, and histogram.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| fast_length | 12 | 2-2000 | Short moving-average lookback. |
| slow_length | 26 | 2-2000 | Long moving-average lookback; should stay above fast_length. |
| signal_length | 9 | 2-2000 | Smoothing length for the signal line. |
| ma_type | EMA | SMA, EMA, WMA, RMA | Moving-average kernel used for MACD and signal calculations. |
| source | close | close, hl2, hlc3, ohlc4 | Price input used for the calculation. |

## Reading the Indicator

Bullish signal-line crossovers can show momentum turning up; bearish crossovers can show momentum fading. A growing histogram means momentum is expanding. Crossing the zero line suggests the fast average has moved above or below the slow average.

## When to Use

Use MACD in trending markets to confirm momentum, spot pullback recoveries, and judge whether a move is strengthening or losing steam.

## ⚠️ When NOT to Use

MACD struggles in range-bound chop where repeated crossovers can fire late and fail fast.

## Common Setups

- Signal-line crossover after a pullback.
- Histogram turning before price fully breaks out.
- Zero-line cross confirming a trend shift.

## Tips

EMA is the classic kernel. Use MACD with RSI when you want both momentum direction and momentum condition. If MACD is crossing constantly around zero, market structure is probably too messy.

## References

- Gerald Appel, MACD work from the late 1970s
- Standard baseline: 12/26/9 EMA MACD
