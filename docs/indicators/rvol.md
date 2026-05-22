# Relative Volume

Relative Volume, or RVOL, compares current volume to a recent baseline. It helps answer a simple question fast: is this move attracting unusual participation or not?

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| mode | simple | simple, cumulative, time_of_day | Chooses a rolling bar baseline, session-cumulative baseline, or same-time-of-day baseline. |
| length | 20 | 1-500 | Lookback bars in `simple`, or lookback sessions in `cumulative` and `time_of_day`. |
| aggregator | mean | mean, median | Baseline average. Median is more robust to outlier days. |
| session_filter | regular_only | regular_only, regular_plus_premarket, extended | Which bars count in the baseline. |
| denominator_includes_current | False | True, False | Lets the current bar be included in the `simple` denominator. |
| z_score | False | True, False | Shows RVOL as a rolling z-score instead of a raw ratio. |
| threshold_warn | 2.0 | 0.1-100.0 | Warning reference level for raw RVOL. |
| threshold_extreme | 5.0 | 0.1-100.0 | Extreme reference level for raw RVOL. |

## Reading the Indicator

Raw RVOL centers around 1.0. Around 1.5 or higher usually means unusual activity; 2.0+ often means serious attention. If `z_score=True`, read 0 as normal and +2 as a strong relative-volume push.

## When to Use

Use RVOL to confirm breakouts, opening drives, news moves, and trend continuation. It is also useful for avoiding low-participation setups.

## ⚠️ When NOT to Use

Do not use RVOL as a stand-alone directional signal. High RVOL confirms participation, not whether price should go up or down.

## Common Setups

- `simple, 20` for general chart work.
- `time_of_day, 20` for intraday traders comparing the 10:15 bar to normal 10:15 activity.
- RVOL spike plus price breakout for confirmation.

## Tips

A big RVOL burst without price progress can hint at absorption. `time_of_day` and `cumulative` modes are intraday-only.

## References

- TradingLab built-in indicator source: `src/tradinglab/indicators/rvol.py`
- Common discretionary trading use of relative volume and volume z-scores
