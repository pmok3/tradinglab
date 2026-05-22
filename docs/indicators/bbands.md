# Bollinger Bands

Bollinger Bands are a volatility envelope built around a moving average. They plot three lines: a middle band, plus upper and lower bands that expand and contract as volatility changes.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| length | 20 | 2-2000 | Lookback for the middle band. |
| num_std | 2.0 | 0.1-10.0 | Distance of the outer bands from the middle band in standard deviations. |
| std_length | 20 | 2-2000 | Lookback used for volatility calculation. |
| ma_type | SMA | SMA, EMA, WMA, RMA | Moving-average type used for the middle band. |

## Reading the Indicator

A squeeze means volatility has contracted and expansion may be next. When price walks the upper or lower band, that often signals a strong trend, not an immediate reversal. Band touches alone are not trade signals.

## When to Use

Use Bollinger Bands for volatility context, squeeze setups, and mean-reversion ideas when the market is clearly rotating between extremes.

## ⚠️ When NOT to Use

Avoid forcing mean reversion in low-volatility drift or assuming every band tag must reverse. Trends can ride a band much longer than expected.

## Common Setups

- Squeeze breakout after band contraction.
- Mean reversion from a band touch back toward the middle band.
- Trend continuation when price keeps accepting outside the middle band.

## Tips

Bollinger Bands pair well with Keltner Channels for BB/KC squeeze logic. If bands are flat and price is drifting, be patient; the better move is often to wait for expansion.

## References

- John Bollinger, Bollinger Bands research from the 1980s
- Common baseline: 20-period average with 2 standard deviations
