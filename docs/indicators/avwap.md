# Anchored VWAP

Anchored VWAP tracks the average traded price from a user-chosen starting point instead of resetting every session. It is one of the most useful event-based reference lines in discretionary trading.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| anchor_ts | blank | ISO timestamp or blank | Anchor point for the calculation. Blank falls back to the first eligible bar until an anchor is picked. |
| price_source | typical | typical, close, ohlc4 | Price input used in the VWAP calculation. |
| bands | off | off, 1σ, 2σ, both | Adds standard-deviation bands around AVWAP. |

## Reading the Indicator

Above a rising AVWAP usually means buyers still control the move from that anchor. Reclaims, holds, and rejects around the line often matter more than simple crosses.

## When to Use

Use AVWAP when you have a meaningful anchor: earnings, IPO date, major gap, breakout candle, capitulation low, or a key swing high/low. It is especially useful for tracking where institutions may be defending inventory.

## ⚠️ When NOT to Use

Do not anchor randomly. AVWAP is only as good as the event or pivot you choose.

## Common Setups

- Anchor to an earnings gap day.
- Anchor to an IPO date or major breakout.
- Anchor to a major low, then buy pullbacks that hold AVWAP.

## Tips

This is an institutional-style tool. If several clean anchors cluster in the same area, that price zone often matters.

## References

- Brian Shannon, anchored VWAP trading framework
- TradingLab built-in indicator source: `src/tradinglab/indicators/avwap.py`
