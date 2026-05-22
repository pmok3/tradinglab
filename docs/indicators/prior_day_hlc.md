# Prior Day H/L/C

Prior Day H/L/C plots yesterday's regular-session high, low, and close across today's intraday chart. These are core reference levels for breakout, rejection, and gap analysis.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| show_high | True | True, False | Shows or hides the prior day high line. |
| show_low | True | True, False | Shows or hides the prior day low line. |
| show_close | True | True, False | Shows or hides the prior day close line. |

## Reading the Indicator

PDH and PDL define yesterday's range. PDC defines the gap relationship: trading above it means today's session is holding above yesterday's close, and trading below it means the market is giving that back.

## When to Use

Use this on basically every intraday chart. These levels are simple, widely watched, and often frame the entire day.

## ⚠️ When NOT to Use

Do not expect it to work on daily or higher timeframes. It is designed for intraday charts and needs prior regular-session data loaded.

## Common Setups

- Opening drive through PDH or PDL.
- Gap-and-go or gap-fill logic around PDC.
- Reclaim/reject tests of yesterday's close after the open.

## Tips

The first 15 minutes often produce fake breaks of PDH or PDL before the real move shows up. Let the open settle when possible.

## References

- TradingLab custom indicator source: `src/tradinglab/indicators/prior_day.py`
- Common discretionary use of prior-day levels in intraday trading
