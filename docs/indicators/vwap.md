# Volume-Weighted Average Price (VWAP)

VWAP is the session-anchored average price weighted by volume. Traders use it as an intraday fair-value line to judge whether price is trading with strength above value or weakness below it.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| price_source | typical | typical, close, ohlc4 | Chooses which per-bar price is volume-weighted into VWAP. |

## Reading the Indicator

Above VWAP is generally bullish intraday context; below VWAP is bearish. Reclaims of VWAP often signal buyers regaining control, while repeated rejection at VWAP can confirm seller pressure.

## When to Use

Use VWAP for intraday bias, pullback entries, and judging whether a move is extended from fair value. It is especially useful around the open and during the first two hours, when institutional participation is heaviest.

## ⚠️ When NOT to Use

Do not rely on VWAP on daily charts or higher. Session anchoring makes it an intraday tool; on daily bars it loses meaning.

## Common Setups

- VWAP reclaim long after a morning washout.
- VWAP rejection short in a weak tape.
- Pullback into VWAP during a trend day.

## Tips

VWAP is strongest when combined with prior day high, low, and close. If price is far from VWAP, think about extension risk before chasing. Pair it with RSI for momentum condition or with prior day levels for location.

## References

- VWAP is a standard execution and intraday benchmark used by institutional desks.
- Best interpreted alongside session structure, volume, and prior day levels.
