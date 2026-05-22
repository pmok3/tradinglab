# Stochastic Momentum Index

The Stochastic Momentum Index, or SMI, is a centered oscillator that tracks where price sits relative to the midpoint of its recent range. It is smoother than a raw stochastic, which makes crossovers easier to read.

## Parameters

| Parameter | Default | Range | What it does |
|-----------|---------|-------|--------------|
| length | 14 | 2-2000 | Recent high/low lookback. |
| smooth1 | 3 | 1-200 | First smoothing pass. |
| smooth2 | 3 | 1-200 | Second smoothing pass. |
| signal_length | 3 | 1-200 | Signal-line smoothing. |

## Reading the Indicator

Watch SMI/signal crossovers, moves through the zero line, and divergence versus price. Levels around +40 and -40 often act like overbought and oversold zones.

## When to Use

Use SMI for swing timing, pullback entries, and spotting momentum turns without as much noise as a classic stochastic.

## ⚠️ When NOT to Use

Do not assume every overbought or oversold reading means an immediate reversal. In strong trends, oscillators can stay pinned longer than expected.

## Common Setups

- 14,3,3,3 for general swing trading.
- Signal crossover in the direction of the higher-timeframe trend.
- Divergence near a key support or resistance level.

## Tips

If you like stochastic logic but want fewer fake wiggles, SMI is a good upgrade. It usually reacts more smoothly while still catching real turns.

## References

- William Blau, *Momentum, Direction, and Divergence* (1993)
- TradingLab built-in indicator source: `src/tradinglab/indicators/smi.py`
