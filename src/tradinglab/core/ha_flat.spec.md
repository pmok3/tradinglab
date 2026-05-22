# `core/ha_flat`

Pure-NumPy detection of **direction-aware Heikin-Ashi flat-edge patterns** (bull-flat-bottom and bear-flat-top).

## Purpose
A "marubozu-style" HA bar — body opening at the bar's extreme in the trend direction — is a strong-momentum signal. Narrower than the direction-agnostic builtins (`ha_flat_top`, `ha_flat_bottom`) which fire on either edge regardless of colour:

| Pattern              | Bar colour              | Flat edge                       |
| -------------------- | ----------------------- | ------------------------------- |
| Bull-flat-bottom     | bull (`HA_close > HA_open`) | `HA_low == HA_open` (no lower wick) |
| Bear-flat-top        | bear (`HA_close < HA_open`) | `HA_high == HA_open` (no upper wick) |

Dojis (`HA_close == HA_open`) never qualify.

Powers two surfaces:
1. **Chart overlay** — View → "Highlight Flat HA Candles" feeds qualifying indices into `rendering.draw_candlesticks` via the `flat_overlay` parameter (dict carrying bull/bear index sets + hatch colours + patterns). Renderer layers a cross-hatched `PolyCollection` on top of the normal body. Hatch line colour is theme-aware: `rendering.darker_shade` in light mode, `rendering.brighter_shade` in dark mode.
2. **Scanner / entries / exits builtins** — `ha_flat_bottom_bull`, `ha_flat_top_bear`, `ha_flat_strong` (signed) in `scanner/fields.py` reuse this compute so chart and scanner cannot disagree.

## Public API
```python
compute_ha_flat_arrays(candles)         # convenience wrapper over Candle list
compute_ha_flat_arrays_np(o, h, l, c)   # pure NumPy entry point

@dataclass(frozen=True)
class HAFlatArrays:
    bull_flat_bottom: np.ndarray  # bool, (N,)
    bear_flat_top:    np.ndarray  # bool, (N,)
    signed:           np.ndarray  # int8, (N,)

HA_FLAT_NONE    = 0
HA_FLAT_BULL    = 1
HA_FLAT_BEAR    = -1
HA_FLAT_UNKNOWN = -128   # NaN-input bar (warm-up / gap)
```

## Algorithm
1. Apply standard HA recurrence (`heikin_ashi.ha_arrays`). NaN bars propagate to NaN HA; recurrence re-seeds across NaN runs so a single gap doesn't poison the suffix.
2. Non-NaN bars: classify with strict-greater bull/bear (excludes doji) AND tolerant flat-edge test `|HA_low - HA_open| <= eps` (or `|HA_high - HA_open| <= eps`).
3. `eps = max(1e-9, |price| * 1e-9)` — **same formula** as `scanner.fields._ha_flat_eps`. Identical tolerance ⇒ chart and scanner agree per-bar.
4. NaN-input bars produce `False / False / -128`.

## Determinism & threading
Deterministic, idempotent, no global state. Pure NumPy → safe from any thread. The downstream cache (`scanner.fields._ha_flat_cache`, a `BarsKeyedCache[HAFlatArrays]`) handles its own locking; direct callers don't coordinate.
