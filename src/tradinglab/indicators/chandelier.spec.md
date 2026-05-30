# indicators/chandelier.py — Spec

## Purpose
Always-on chart-study indicator that overlays Chandelier Stops (Chuck LeBeau, 1995) on the price pane. Companion to the in-trade exit rule that lives in `exits/spec.py` and shares the same math via `core/chandelier_math.py`.

## Public API
- `class ChandelierStops` — Indicator factory, registered in `indicators.__init__` under display name `"Chandelier Stops"`, kind_id `"chandelier"`.
- Construct as `ChandelierStops(lookback=22, atr_period=22, multiplier=3.0, ma_type="RMA")`.
- `compute_arr(bars: Bars) → Dict[str, np.ndarray]` — returns `{"long_stop", "short_stop"}`.
- `compute(candles: List[Candle]) → Dict[str, np.ndarray]` — inherited from `BaseIndicator` as the candle-list convenience shim.
- `warmup_bars` property returns `max(lookback, 4×atr_period)` for RMA and `max(lookback, atr_period)` for non-RMA kernels.

## Formula
* `long_stop[i]  = highest_high(lookback)  − multiplier × ATR[i]` (ratcheted up only)
* `short_stop[i] = lowest_low(lookback)    + multiplier × ATR[i]` (ratcheted down only)

ATR uses `core.chandelier_math.compute_atr`, which shares True Range and kernel semantics with the vectorized indicator stack.

## Parameters (locked design)
| Param | Default | Range | Notes |
|---|---|---|---|
| `lookback` | 22 | 1..500 | Highest-high / lowest-low window |
| `atr_period` | 22 | 2..500 | ATR smoothing period (decoupled from lookback per LeBeau original) |
| `multiplier` | 3.0 | 0.5..8.0 | ATR multiple |
| `ma_type` | `"RMA"` | {RMA, SMA, EMA, WMA} | Wilder's RMA is the canonical kernel |

## Render
* `overlay = True` — drawn on the price pane (a stop level must be in price units).
* `output_kinds = {"long_stop": "stair_line", "short_stop": "stair_line"}` — uses the b72 `stair_line` render path with `drawstyle="steps-post"`. Stair-step display makes the discrete ratchet events visually unmistakable, which the trader memo flagged as the single biggest skill-development affordance.
* Default colors: dark green for the long line, dark red for the short line. Theme-aware shading via the existing per-output style override surface.
* **Both lines are drawn simultaneously** when no position is open — per the user's design decision so traders can study where stops would sit on either side before entering.

## Ratcheting
Always ON, no toggle. The long line never descends; the short line never rises. This is the defining trait of a chandelier and is intentionally not surfaced as an exposed parameter.

## Warm-up
NaN until both:
* The rolling lookback window has `lookback` valid bars.
* The ATR kernel has accumulated `atr_period` valid TR samples (TR[0] is NaN because there is no prior close).

No SMA-of-TR placeholder during warm-up — the explicit NaN gap teaches the user that ATR needs warm-up.

## Display name
* `CHAND(22,22,3)` — all defaults.
* `CHAND(20,14,3)` — decoupled lookback / atr_period.
* `CHAND(22,22,3,SMA)` — non-default kernel.

## Testing
* `tests/unit/test_chandelier_indicator.py` — wiring, validation, both outputs rendered, stair_line dispatch.
* `tests/unit/test_chandelier_math.py` — pure-math invariants (rolling extremum, ratchet preserved on flat moves, NaN warm-up).

