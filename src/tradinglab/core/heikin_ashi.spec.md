# `core/heikin_ashi`

Pure-NumPy Heikin-Ashi transform.

## Purpose
HA smooths candles to emphasize trend continuation. Used in two opt-in places:
1. **Chart display** — View → Heikin-Ashi Candles substitutes HA OHLC at the candle-glyph draw site only. Indicators, volume, hover, autoscale, and data-table screenshots stay on real OHLC.
2. **Scanner builtin fields** — `ha_*` builtins in `scanner/fields.py` expose HA values so users can author conditions like *"5 consecutive flat-bottom HA candles"*. Indicators in scanner conditions still compute on real OHLC; users opt in to HA-derived signals via these fields.

## Public API
```python
ha_arrays(open_, high, low, close) -> (ha_o, ha_h, ha_l, ha_c)
```
All four inputs are 1-D NumPy arrays of equal length (`ValueError` otherwise); outputs are float64 same length.

```python
heikin_ashi_candles(candles: List[Candle]) -> List[Candle]
```
Wrapper used by `rendering.py` when the toggle is on. Extracts OHLC, runs `ha_arrays`, returns a parallel `List[Candle]` with non-gap candles carrying HA OHLC while `date`, `volume`, `session`, `is_gap` are preserved. Gap candles pass through untouched.

## Formula
```
HA_Close[i] = (O[i] + H[i] + L[i] + C[i]) / 4
HA_Open[0]  = (O[0] + C[0]) / 2                      # seed
HA_Open[i]  = (HA_Open[i-1] + HA_Close[i-1]) / 2     # i >= 1
HA_High[i]  = max(H[i], HA_Open[i], HA_Close[i])
HA_Low[i]   = min(L[i], HA_Open[i], HA_Close[i])
```

## Design
- **Sequential loop, not vectorized.** `HA_Open[i]` is recursive on `HA_Open[i-1]`. Python loop acceptable: single-symbol prefixes (typically <2000 bars), cached per `id(bars)` at higher layers.
- **NaN re-seed across gaps.** If `HA_Open[i-1]` or `HA_Close[i-1]` is NaN, `HA_Open[i]` is re-seeded from `(O[i] + C[i]) / 2` so a single gap doesn't poison the suffix.
- **Canonical TradingView / ThinkOrSwim definition.** No look-ahead, no smoothing param.
- **Empty input permitted** — returns four length-0 arrays.

## See also
- `rendering.py` — candle draw site.
- `scanner/fields.py` — `ha_open` / `ha_high` / `ha_low` / `ha_close` / `ha_color` / `ha_flat_top` / `ha_flat_bottom` / `ha_streak` / `ha_flat_top_streak` / `ha_flat_bottom_streak` builtins.
