# `strategy_tester/warmup.py`

Compute the minimum warmup-bar requirement for every indicator referenced
by an EntryStrategy + ExitStrategy pair, so the runner can pre-load enough
historical bars before `start_date` for **every** referenced indicator to
be fully hydrated by Day 1 of the active backtest period.

## Surface

- `DEFAULT_WARMUP_BARS: int = 100` — fallback for unknown indicator
  `kind_id` and for indicators whose factory or compute path raises.
- `WARMUP_SAFETY_MULTIPLIER: float = 1.5` — applied to the raw max bar
  count inside `required_warmup_bars`.
- `warmup_bars_for_kind(kind_id, params) -> int` — resolves a single
  `(kind_id, params)` pair through the generalized flow described below.
  Cached per-process by `(kind_id, frozen_params)`.
- `required_warmup_bars(entry, exit) -> int` — walks the condition trees
  on `entry.trigger.condition` and `exit.legs[*].triggers[*].condition`
  (when `kind=INDICATOR`); also handles `kind=CHANDELIER` exit triggers
  by reading their colocated `chandelier_lookback` / `chandelier_atr_period`
  fields. Returns `max(bar_count) × WARMUP_SAFETY_MULTIPLIER` rounded up.
  Returns `0` when no indicator-style triggers are present.
- `required_warmup_bars_by_symbol(entry, exit) -> dict[str, int]` —
  same walk as `required_warmup_bars` but **grouped by symbol**. The
  empty-string key `""` is the active symbol (every legacy ref);
  non-empty keys are cross-ticker dependencies pinned via
  `FieldRef.symbol`. Each value is the per-symbol max bar count ×
  `WARMUP_SAFETY_MULTIPLIER`. Returns `{}` when no indicator-style
  triggers are present. The runner consumes this mapping directly:
  `""` controls the active symbol's warmup gate and each non-empty key
  controls that dependency symbol's companion-fetch window.
- `collect_referenced_indicator_kinds(entry, exit) -> list[tuple[str, str, dict]]`
  — the shared walker behind both `required_warmup_bars` and
  `required_warmup_bars_by_symbol`: returns every `(symbol, kind_id, params)`
  triple referenced by the entry INDICATOR-trigger condition tree, every
  enabled exit-leg INDICATOR trigger, and each CHANDELIER exit trigger.
  Also consumed by `strategy_tester.interval_compat` so the warmup sizer and
  the intraday-interval guard walk the identical surface. SCANNER_ALERT
  entries are not walked (their scan lives on disk).
- `_walk_field_kinds(node) -> list[tuple[str, str, dict]]` — internal
  tree walker. Emits `(symbol, kind_id, params)` triples; the leading
  `symbol` slot is `""` for active-symbol refs and the pinned ticker
  for cross-symbol refs.
- `bars_to_calendar_days(bars, interval) -> int` — converts bars at
  the given interval to a calendar-day window for the fetch range:
  - intraday (`1m`/`5m`/`15m`/`30m`/`1h`): `ceil(bars / bars_per_RTH_day) × 1.5`
  - `1d`: `ceil(bars × 1.5)`
  - `1w`: `bars × 7`
  Returns `≥ 1` whenever `bars > 0`.

## Resolution order (the generalized flow)

`warmup_bars_for_kind` is **not** a hardcoded per-indicator table. It
resolves each `(kind_id, params)` pair through three steps:

1. **Explicit opt-in.** Look up the factory via
   `indicators.base.factory_by_kind_id(kind_id)` and instantiate it as
   `factory(**params)`. If the resulting instance exposes
   `warmup_bars` as an `int` attribute OR a no-arg callable returning
   an int, return that value. Indicators that know their exact
   convergence (e.g. Wilder's RSI needs `4 × length` for IIR
   convergence, not just `length + 1`) declare this attribute so
   empirical first-finite detection doesn't under-count them.
2. **Empirical detection.** Otherwise, run `compute_arr` (or
   `compute(candles)`) on a deterministic 500-bar synthetic OHLCV
   series and return `max(first_finite_index across output series) + 1`.
   Handles every built-in indicator and any user plugin uniformly — no
   table edits required when a new indicator ships.
3. **Fallback.** Unknown `kind_id` (factory lookup miss), factory
   `__init__` raises, compute raises, or every output series is
   all-NaN → `DEFAULT_WARMUP_BARS` (100).

## Indicators that opt in via `warmup_bars`

Most indicators (SMA, EMA, WMA, RMA, DEMA, TEMA, HMA, VWMA, Bollinger,
Keltner, VWAP, AVWAP, RVOL, RRVOL, SMI, LRSI, …) do **not** declare
`warmup_bars`; empirical first-finite detection produces the right
answer. The Wilder-smoothed / chained-MA family **does** opt in
because their first-emit index is much earlier than full convergence:

| kind_id      | declared `warmup_bars`                | rationale                                                       |
| ------------ | -------------------------------------- | --------------------------------------------------------------- |
| `rsi`        | `4 × length`                          | Wilder IIR convergence (drift continues after first emit).      |
| `atr`        | `4 × length` (RMA) else `length`      | Same Wilder story for RMA kernel; SMA/EMA/WMA settle in `length`.|
| `adx`        | `4 × length`                          | Wilder smoothing chained twice (DI then ADX).                   |
| `macd`       | `max(fast, slow) + signal`            | Signal MA chains on top of the macd line; both must seed.       |
| `chandelier` | `max(lookback, 4 × atr_period)` (RMA) | HH window + Wilder-ATR; matches LeBeau's spec.                  |

## Contract

- Pure / side-effect-free apart from the per-process memo. No disk
  reads, no Tk imports.
- Walks the **deeply-nested** condition tree (Group of Groups of Conditions);
  every indicator FieldRef on either side of any comparison is counted.
- `SCANNER_ALERT` entry triggers do **not** resolve the referenced
  scan here (would require disk I/O); the runner is responsible for
  loading the scan if it wants per-scan precision. When no INDICATOR /
  CHANDELIER triggers exist, no warmup is requested (`0` / `{}`).
- Multiple indicators → **max**, not sum. The longest single warmup
  is the binding constraint; layering EMAs doesn't compound.
- Cache key is `(kind_id, frozen_params)` where `frozen_params` sorts
  the params dict items and tuple-ifies container values. Identical
  references during one Run only pay the empirical-compute cost once.

## Examples

```python
from tradinglab.strategy_tester.warmup import warmup_bars_for_kind
warmup_bars_for_kind("ema", {"length": 8})                                       # 8   (empirical)
warmup_bars_for_kind("sma", {"length": 50})                                      # 50  (empirical)
warmup_bars_for_kind("bbands", {"length": 20})                                   # 20  (empirical)
warmup_bars_for_kind("vwap", {})                                                 # 1   (empirical)
warmup_bars_for_kind("rsi", {"length": 14})                                      # 56  (explicit, 4×14)
warmup_bars_for_kind("atr", {"length": 14})                                      # 56  (explicit, RMA default)
warmup_bars_for_kind("macd", {"fast_length": 12, "slow_length": 26, "signal_length": 9})  # 35  (explicit)
warmup_bars_for_kind("chandelier", {"lookback": 22, "atr_period": 22})           # 88  (explicit, max(22, 4×22))
warmup_bars_for_kind("some_user_plugin", {})                                     # empirical detection
warmup_bars_for_kind("does_not_exist", {})                                       # 100 (DEFAULT_WARMUP_BARS)
```
