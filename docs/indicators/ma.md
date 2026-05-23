# Moving Average (MA)

A moving average smooths the noise out of price so trend, pullbacks, and dynamic support/resistance are easier to read. TradingLab ships one Moving Average indicator with a dropdown for the math (**SMA / EMA / WMA / RMA**) and a separate dropdown for the price field used as the input (**Close / Open / High / Low / HL2 / HLC3 / OHLC4**).

## Parameters

| Parameter | Default | Range / Choices | What it does |
|-----------|---------|-----------------|--------------|
| Type | SMA | SMA, EMA, WMA, RMA | Which averaging math to use (see the comparison table below). |
| Length | 20 | 1–2000 | Number of bars in the rolling window. Higher = smoother, slower. |
| Source | Close | Close, Open, High, Low, HL2, HLC3, OHLC4 | Which field to average. `HL2 = (H+L)/2`, `HLC3 = (H+L+C)/3`, `OHLC4 = (O+H+L+C)/4`. |

The last MA type you picked sticks for the rest of the app session — open Add Indicator again and the dropdown is already on your choice. It resets back to SMA on the next launch.

## Type comparison

| Type | Full name | How it weights bars | Reacts | Best for |
|------|-----------|---------------------|--------|----------|
| **SMA** | Simple Moving Average | All bars equally | Slow | Higher-timeframe bias, clean swing structure, 20/50/200 benchmark lines. |
| **EMA** | Exponential Moving Average | Recent bars more heavily | Faster | Intraday continuation, momentum names, pullback entries (TradingView's default). |
| **WMA** | Weighted Moving Average | Linearly decreasing — newest bar gets weight N, oldest gets 1 | Faster than SMA, smoother than EMA | When you want EMA-like responsiveness without EMA's long memory tail. |
| **RMA** | Running / Wilder Moving Average | Smoother EMA variant (`α = 1/N`) | Slowest of the four | Wilder-style indicators (RSI / ATR foundations); long-horizon smoothing. |

## Reading the indicator

A rising MA means trend pressure is up; a falling MA means trend pressure is down. Price above the MA usually signals bullish control, while price below it suggests weakness. Faster types (EMA / WMA) react sooner — good in trends, more whipsaw-prone in chop. Slower types (SMA / RMA) confirm later but ignore noise better.

## When to use

- **Trend filter**: any MA on a longer length (50 / 100 / 200) defines the higher-timeframe bias.
- **Pullback entry**: faster MAs (9 / 20 EMA on intraday) hugging price give clean dynamic support/resistance.
- **Crosses**: a fast MA crossing a slow MA flags a change in trend regime. Combine with momentum (RSI / MACD) to filter low-quality signals.
- **Non-close sources**: HL2 / HLC3 / OHLC4 sources give a "midpoint" trend line that's less sensitive to single-bar wicks and useful for pivot-style analysis.

## ⚠️ When NOT to use

Avoid leaning on any single MA in choppy, range-bound markets. Price will whip above and below it repeatedly and generate low-quality crosses. Pair MAs with a momentum oscillator or use multiple lengths (fast + slow stack) instead of trading every cross blindly.

## Common setups

- **Pullback continuation**: price retraces into a rising 20 EMA and bounces — long on reclaim.
- **MA stack**: 9 EMA / 20 EMA / 50 SMA aligned in the same direction = trending market; flat or interleaved = chop.
- **MA cross**: 9/20 or 20/50 cross for short-term trend changes.

## Tips

- The `EMA` color is orange and the `SMA` color is blue — same defaults as TradingView and most charting platforms, so muscle memory carries over.
- For intraday names, EMA on Close is the standard.  For longer-horizon swing trades, SMA on Close is the standard reference.
- Switch to HLC3 or OHLC4 source when you want the MA to ignore single-bar wicks — useful around earnings or other event spikes.
- A 20-length MA is a sensible baseline. 50 and 200 are the classic "institutional" benchmarks.

## Migration from legacy SMA / EMA entries

Earlier versions of TradingLab had separate `SMA` and `EMA` indicators in the Add menu. Those were consolidated into this single Moving Average indicator. Existing saved presets, drawings, and chart layouts that used the old indicators are migrated automatically the moment they are loaded — the color, length, and per-interval visibility you customized are all preserved. Old preset JSON files on disk are *not* rewritten; the migration happens in memory on load.

## References

- John J. Murphy, *Technical Analysis of the Financial Markets* — chapter on moving averages covers all four types.
- Gerald Appel popularized EMA-based momentum work through MACD.
- J. Welles Wilder, Jr., *New Concepts in Technical Trading Systems* — defines RMA (Wilder's smoothing) and uses it as the foundation of RSI and ATR.
- Common benchmark lengths: 9, 20, 50, 100, 200.
