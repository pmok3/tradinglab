# core/key_bar.py — Spec

## Purpose
Detect/characterize **key bars** (wide-range / igniting / elephant bars, RDT canonical bar #1 of the 1-2-3). Pure NumPy. Consumed by the scanner (9 builtin fields) and the chart's *View → Highlight Key Bars* toggle (renders via `hollow_indices=set(KeyBarArrays.signed.nonzero())`).

## Public API
- Sentinels: `KEY_BAR_NONE = 0`, `KEY_BAR_BULL = 1`, `KEY_BAR_BEAR = -1`, `KEY_BAR_UNKNOWN = -128` (int8 min — can't collide with a real direction).
- Thresholds (module-level constants, no per-scan params): `TR_THRESHOLD = 1.0`, `RVOL_THRESHOLD = 1.1`, `BODY_RATIO_THRESHOLD = 0.69`, `LOOKBACK_BARS_NON_INTRADAY = 20`.
- `@dataclass(frozen=True) KeyBarArrays`:
  - `signed: int8[N]` — direction (`-128` warmup/NaN).
  - `bars_since_bull / bars_since_bear: int64[N]` — `-1` until first match.
  - `last_bull_high / last_bull_low / last_bear_high / last_bear_low: float64[N]` — NaN until first match.
- `compute_key_bar_arrays(candles: List[Candle]) -> KeyBarArrays`.
- `compute_key_bar_arrays_np(bars) -> KeyBarArrays` — for the scanner/fields layer holding a `BarsNp`. Non-intraday is pure NumPy; intraday reconstructs a candle list locally via `_bars_np_to_candles` so the same `ATR(mode="tod")` / `RVOL(mode="time_of_day")` baselines apply. Both entry points share `_kb_kernel` → byte-identical arrays for the same bars. Scanner caches result process-globally keyed on `id(BarsNp)` in `scanner/fields.py::_kb_for`.

## Qualification rule (canonical, locked)
A bar `i` is a key bar iff **all** of:
1. `tr[i] > 1.0 × baseline_tr[i]`.
2. `rvol[i] > 1.1`.
3. `|close[i] - open[i]| / (high[i] - low[i]) > 0.69` (strict `>`, not `>=`).

Direction: `close > open` → `+1`; `close < open` → `-1`; `close == open` → `0` (body-ratio fails anyway).

## Baselines (interval-aware)

| Interval | baseline TR | baseline volume |
|---|---|---|
| Intraday | `ATR(mode="tod", length=20).compute(candles)["atr"]` | `RVOL(mode="time_of_day", length=20, aggregator="mean", session_filter="regular_only").compute(candles)["rvol"]` (already a ratio; threshold `> 1.1`) |
| Daily / weekly / monthly | inline rolling 20-bar mean of TR over `tr[i-19:i+1]` | `vol[i] / mean(vol[i-20:i])` |

Reusing chart indicators is deliberate: a chart-overlay ATR ToD / RVOL ToD agrees exactly with values driving a scanner match.

### Asymmetry (intentional)
Range uses **TR** (captures prior-close gap); body-ratio denominator uses **(high − low)** (no gap). Matches how traders eyeball bars.

## Dependencies
Internal (all lazy-imported): `..indicators.atr.ATR`, `..indicators.rvol.RVOL`, `..indicators.sessions.is_intraday`, `..indicators.wilder.true_range`, `..models.Candle`. External: `numpy`.

## Invariants
- `len(signed) == len(bars_since_bull) == ... == len(candles)`.
- `signed[i] != 0` iff all three thresholds pass (and `signed[i] != -128`).
- `bars_since_bull[i] == 0` iff `signed[i] == KEY_BAR_BULL`.
- Never both bull and bear simultaneously.

## Notes
- Off-hours bars (outside `regular_only` session) get NaN RVOL → treated as warmup (`-128`), never classified as key bars.
- No caching here; scanner caches by `id(BarsNp)`, chart recomputes per `_draw_slice` (cheap).
