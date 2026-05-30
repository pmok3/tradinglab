# Chandelier Stops Math (`core/chandelier_math.py`)

Pure compute (no matplotlib / Tk / main-thread coupling). Thread-safe.

Shared by `indicators.chandelier.ChandelierStops` (always-on overlay) and `exits.spec` chandelier evaluator (in-trade exit rule). Math is identical — only the lifecycle differs.

## Formula
```
long_stop[i]  = highest_high(window) − multiplier × atr[i]
short_stop[i] = lowest_low(window)   + multiplier × atr[i]
```
`window` is a backward-looking slice of width `lookback`, optionally anchored at `entry_idx` (Camp-B rolling-high seeded at the entry bar, expanding forward, capped at `lookback`).

ATR uses Wilder's TR smoothed by the selected kernel (RMA / SMA / EMA / WMA, default RMA — Wilder's original and canonical match for LeBeau 1995).

## Public API
- `compute_atr(highs, lows, closes, atr_period, ma_type) → ndarray` — wrapper around `true_range` + `apply_ma`.
- `rolling_highest_high_since(highs, lookback, anchor_idx=None) → ndarray` — per-bar rolling max, Camp-B anchored when `anchor_idx` given. NaN outside valid region.
- `rolling_lowest_low_since(lows, lookback, anchor_idx=None) → ndarray` — mirror.
- `compute_chandelier_long(highs, atr_values, lookback, multiplier, *, anchor_idx=None, ratchet_prev=None) → (stops, final_ratchet)` — ratchet always ON; never descends. `ratchet_prev` seeds the running max for chunked invocations.
- `compute_chandelier_short(lows, atr_values, lookback, multiplier, *, anchor_idx=None, ratchet_prev=None) → (stops, final_ratchet)` — ratchet always ON; never rises.

## Warm-up
NaN until both the rolling window AND the ATR kernel are warm. No SMA-of-TR proxy — explicit NaN gap teaches the user ATR needs warm-up.

## Anchor modes
- **`anchor_idx is None`** — indicator mode. Classic backward-looking `lookback` window. NaN until index `lookback - 1`. Long and short emitted side-by-side.
- **`anchor_idx is not None`** — exit-rule mode (Camp B). Window starts empty at `anchor_idx`, expands forward, capped at `lookback`. NaN before `anchor_idx`; at `anchor_idx` itself the window is `[highs[anchor_idx]]`.

## Determinism
Pure function on `np.ndarray`; no state, no IO, no locks. Identical inputs → identical outputs (window ops are sums-of-max, no reorder).

## Performance
The ratchet helpers (`_ratchet_long` / `_ratchet_short`) are loop-free:
the NaN-aware running max/min is a cumulative `np.maximum.accumulate` /
`np.minimum.accumulate` over the finite-compressed series, with
`ratchet_prev` applied as a constant floor/ceiling. Output is
bit-equivalent to the former per-bar loop (NaN entries pass through
untouched; the input array is never mutated). Pinned by
`tests/unit/indicators/test_chandelier_ratchet_vectorized.py`. This is
the dominant cost in `ChandelierStops.compute_arr` for the indicator
(`anchor_idx is None`) path.
