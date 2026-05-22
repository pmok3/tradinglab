# Overlap Score Inverted

Overlap Score Inverted, or OSI, measures how much of the current bar is trading in new territory versus overlapping recent bars. It is a regime tool: low values mean recycling and consolidation, high values mean expansion.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| lookback | 10 | 2-200 | Number of prior bars used to judge overlap. |

## Reading the Indicator

OSI runs from 0 to 100. Near 0 means the current bar lives mostly inside recent ranges. Near 100 means the bar is pushing into fresh territory. The built-in reference levels are 20 and 80.

## When to Use

Use OSI for regime classification, breakout filtering, and deciding whether the market is coiling, drifting, chopping, or expanding.

## ⚠️ When NOT to Use

Do not use OSI as a stand-alone entry trigger. It tells you about market condition, not trade location or direction.

## Common Setups

Use OSI with ATR as a quick 2x2 map:

| | Low OSI | High OSI |
|---|---|---|
| **Low ATR** | Tight coil - spring loading | Quiet drift / grind |
| **High ATR** | Volatile chop - stay out | Breakout / momentum |

## Tips

ATR tells you how big the move is. OSI tells you how new the move is. Together they do a great job separating clean expansion from noisy movement.

## References

- TradingLab proprietary indicator source: `src/tradinglab/indicators/overlap_score.py`
- TradingLab overlap/ATR regime framework
