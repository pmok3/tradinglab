# indicators/keltner.py — Spec

## Purpose
Keltner Channels — three concurrent overlay lines (`middle` / `upper`
/ `lower`) drawn on the price axis around a moving-average centerline.
Volatility envelope parameterised on bar range / ATR. Two formulations
share the same output schema (`method` choice param).

## Public API
- `class KeltnerChannels` — `kind_id="keltner"`, `kind_version=1`,
  `overlay=True`. Constructor
  `(length=20, multiplier=2.0, atr_length=10, ma_type=<sentinel>,
  atr_ma_type="RMA", method="atr")`.
  - `ma_type` accepts the sentinel `"__keltner_default__"` (the
    default), which resolves at hydration to `EMA` for
    `method="atr"`, `SMA` for `method="original"`. Explicit values
    bypass the sentinel.
- `params_schema`:
  - `length: int` (default 20, min 2, max 2000) — centerline MA
    window. Also the range-MA window in `method="original"`.
  - `multiplier: float` (default 2.0, min 0.1, max 20.0).
  - `atr_length: int` (default 10, min 2, max 2000) — ATR window.
    Modern method only; inert in original mode but stored verbatim
    for round-tripping.
  - `ma_type: choice` (default `"EMA"`, choices `SMA | EMA | WMA |
    RMA`) — centerline kernel.
  - `atr_ma_type: choice` (default `"RMA"`, choices `SMA | EMA | WMA
    | RMA`) — ATR kernel. Modern method only.
  - `method: choice` (default `"atr"`, choices `atr | original`).
- `default_style`: orange (`#ff7f0e`) for all three keys; `middle`
  width 1.2, `upper` / `lower` 1.0. Per-MA color palette
  (`SMA→#1f77b4`, `EMA→#ff7f0e`, `WMA→#9467bd`, `RMA→#17becf`) defined
  for future per-instance default swapping.
- `compute(candles) -> {"middle", "upper", "lower"}`. All three
  outputs are length `len(candles)`.
- `name`: compact label. Tags appear only when a parameter differs
  from its (method-appropriate) default. Examples: `KC(20,2)`,
  `KC(20,2,EMA/SMA)`, `KC(20,2,σ=14)`, `KC-Orig(20,2)`.

## Dependencies
- Internal: `..models.Candle`, `..core.bars.Bars`,
  `.base.LineStyle`, `.base.ParamDef`, `.ma_kernels.MA_TYPES`,
  `.ma_kernels.apply_ma`, `.wilder.true_range`.
- External: `numpy`.

## Methods

### `method="atr"` (modern, default)
TradingView / TA-Lib convention.

```
middle = ma(close, length)                       # ma_type kernel
tr     = true_range(high, low, close)
atr    = ma(tr, atr_length)                      # atr_ma_type kernel
upper  = middle + multiplier * atr
lower  = middle - multiplier * atr
```

Default ATR kernel is Wilder's RMA (canonical TradingView default).

### `method="original"` (Chester Keltner, 1960)
Predates TR; uses bar range plus a typical-price centerline.

```
typical = (high + low + close) / 3
middle  = ma(typical, length)                    # ma_type kernel
band    = ma(high - low, length)                 # same ma_type kernel
upper   = middle + multiplier * band
lower   = middle - multiplier * band
```

`atr_length` and `atr_ma_type` are inert in this mode.

## Design Decisions
- **Single class with `method` discriminator** (mirrors Bollinger /
  RVOL / RRVOL / ATR). User can swap formulations without losing
  per-output style.
- **Method-aware centerline default via sentinel.** Conventional
  default differs by method; `params_schema` still publishes `"EMA"`
  so the dialog shows a concrete choice. Explicit values always win.
- **Centerline / ATR delegated to `apply_ma` and `wilder.true_range`** —
  single source of truth for TR arithmetic and kernel semantics
  (shared with ATR, ADX, Bollinger).
- **`overlay=True`, no reference levels** — the channel IS the
  reference.

## Invariants
- `upper >= middle >= lower` at every defined position when
  `multiplier > 0`.
- All three outputs have length `len(candles)`.
- `method="atr"`: indices `[0, max(length, atr_length+1) - 1)` are NaN
  in `upper` / `lower`. `middle` may be defined earlier when
  `length < atr_length + 1`.
- `method="original"`: indices `[0, length - 1)` are NaN in all three.
