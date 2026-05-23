# indicators/moving_averages.py — Spec

## Purpose
Three overlay-indicator classes that share the same `ma_kernels.apply_ma`
dispatcher:

1. `MovingAverage` — the registered, user-facing menu entry
   (`"Moving Average"`). Dropdown picks SMA / EMA / WMA / RMA and the
   source field (Close / Open / High / Low / HL2 / HLC3 / OHLC4).
2. `SMA` — legacy single-type class kept for direct imports
   (`kind_id="sma"`). No longer registered; `factory_by_kind_id("sma")`
   returns `None`. Persisted configs with `kind_id="sma"` migrate to
   `kind_id="ma"` with `ma_type="SMA"` via
   `indicators.base.migrate_kind_id`.
3. `EMA` — same story (`kind_id="ema"` → `ma` + `ma_type="EMA"`).

## Public API

### `MovingAverage`
- `kind_id="ma"`, `kind_version=1`, `overlay=True`,
  `name = f"{ma_type}({length})"` (Close source — implicit) or
  `f"{ma_type}({length},{source})"` (non-Close source).
- `compute(candles) -> {"ma": ndarray}` — single output line. NaN
  warmup at the start of the array matches each kernel's convention
  (SMA / WMA: first `length-1` NaN; EMA / RMA: kernel-specific).
- `params_schema`:
  - `ma_type: choice` — `SMA / EMA / WMA / RMA`, default `"SMA"`,
    label `"Type"`.
  - `length: int` — default 20, min 1, max 2000, label `"Length"`.
  - `source: choice` — `Close / Open / High / Low / HL2 / HLC3 /
    OHLC4`, default `"Close"`, label `"Source"`.
- `default_style = {"ma": LineStyle(color="#1f77b4", width=1.4)}` —
  class-level default; per-instance `__init__` overrides the color
  based on `ma_type` (SMA blue, EMA orange, WMA green, RMA grey).
  The `style_overrides` attribute is what the render layer reads
  when seeding a fresh `IndicatorConfig`; user-overridden colors in
  persisted configs always win over both defaults.
- Constructor signature: `MovingAverage(length=20, ma_type="SMA",
  source="Close")`. Raises `ValueError` on `length < 1`, unknown
  `ma_type`, or unknown `source`.

### Incremental fast-path
- `inc_init(bars)` / `inc_step(state, bars, *, prev_len=)` are
  supported on `MovingAverage` **only for `ma_type in {"SMA","EMA"}`
  AND `source == "Close"`**. WMA / RMA / non-Close source raise
  `ValueError` from `inc_init` / `inc_step` so `IndicatorCache` falls
  through to a full recompute (still microseconds at chart-window
  sizes).
- Legacy `SMA` / `EMA` classes retain their own `inc_init` /
  `inc_step` (output keys `"sma"` / `"ema"`) so existing tests and
  third-party imports keep working.

### Legacy `SMA` and `EMA`
- Same surface as before — see git history. Kept for back-compat
  with direct imports (`from tradinglab.indicators import SMA, EMA`)
  and the legacy incremental-protocol tests in `tests/unit/`.
- NOT registered as menu entries. The unified `MovingAverage` is the
  only one users see.

## Dependencies
- Internal: `..models.Candle`, `..core.bars.Bars`,
  `.ma_kernels.apply_ma` (the dispatcher), `.base.LineStyle` /
  `.base.ParamDef`.
- External: `numpy`.

## Design Decisions
- **One menu entry, not four.** Trader feedback: SMA / EMA / WMA /
  RMA share enough mental model that picking from a dropdown is
  faster than scanning four near-identical entries in the Add
  Indicator submenu.
- **Type-prefixed legend label.** `SMA(20)` / `EMA(9)` — never
  `MA(20, SMA)`. The type IS the identity for traders; the legend
  should reflect that.
- **Source dropdown but no anchor / offset.** The trader agent
  argued for keeping the surface small; HL2 / HLC3 / OHLC4 are the
  high-value additions, and `Close` is the universal default. No
  `offset` / `displace` parameter — that's a chart-overlay shift,
  not a moving-average property.
- **Per-session memory of last-used `ma_type`** on the dialog (class
  attribute on `IndicatorDialog`). Persists across re-opens within
  a single app session, NOT to disk. Resets to `"SMA"` on next
  launch (the default schema value).
- **Migration is at hydration time only.** Preset JSONs on disk are
  not rewritten. `IndicatorConfig.from_dict` walks
  `_KIND_ID_MIGRATIONS` and remaps the persisted style output key
  (`style["sma"]` / `style["ema"]` → `style["ma"]`) so user-customised
  colours / widths / visibility survive.
- **Incremental fast path only on Close + SMA/EMA.** That's the
  combination the chart's per-tick redraw exercises most (the
  pre-consolidation classes only supported `Close`). WMA / RMA are
  rare enough that the O(N) full recompute is acceptable.

## Invariants
- `MovingAverage(n, ma_type="SMA").compute(cs)["ma"]` is equivalent
  to the legacy `SMA(n).compute(cs)["sma"]` (modulo output key).
- `MovingAverage(n, ma_type="EMA").compute(cs)["ma"]` is equivalent
  to the legacy `EMA(n).compute(cs)["ema"]`.
- All four `ma_type` values produce output of length `len(cs)` with
  per-kernel NaN warmup.
- `MovingAverage._normalize_source` is case-insensitive but
  domain-strict — raises `ValueError` outside `SOURCE_TYPES`.
- `MovingAverage(length=L, ma_type=T, source="Close")` matches
  `apply_ma(T, bars.close, L)` exactly (no kernel drift).
- The legacy `SMA` / `EMA` classes are NOT in `INDICATORS` (the
  registry); only `MovingAverage` (display name `"Moving Average"`)
  is.

