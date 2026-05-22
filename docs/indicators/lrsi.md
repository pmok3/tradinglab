# Laguerre RSI

Laguerre RSI is John Ehlers' smoother, faster-reacting take on RSI. It keeps the familiar 0-100 oscillator feel while often cutting down on the whipsaws you get from classic RSI.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| gamma | 0.5 | 0.0-0.999 | Laguerre damping factor. Lower is faster; higher is smoother. |
| oversold | 15 | 0-100 | Lower reference level. |
| overbought | 85 | 0-100 | Upper reference level. Must stay above `oversold`. |
| show_reference_lines | True | True, False | Shows or hides the overbought/oversold guides. |

## Reading the Indicator

Read it much like RSI: turns up from the lower zone can support long ideas, and turns down from the upper zone can support short ideas. The default 15/85 bands are more extreme than classic RSI's 30/70.

## When to Use

Laguerre RSI is especially useful for swing trades and pullback entries when you want a cleaner oscillator with less chop.

## ⚠️ When NOT to Use

Do not force it into a classic RSI "length" mindset. This indicator is driven by `gamma`, so it will not map neatly to RSI(14), RSI(21), and so on.

## Common Setups

- `gamma=0.5` for a balanced default.
- Lower gamma for faster entries.
- Higher gamma when you want fewer false turns.

## Tips

Laguerre RSI often shines in trending swing markets where plain RSI gets too twitchy. Think of `gamma` as your speed knob.

## References

- John F. Ehlers, "Time Warp - Without Space Travel" (2002)
- TradingLab built-in indicator source: `src/tradinglab/indicators/lrsi.py`
