# indicators/base.py — Spec

## Purpose
Declares the `Indicator` Protocol, the `INDICATORS` display registry, and the typed-parameter schema (`ParamDef`, `LineStyle`) that drives both the auto-generated Add Indicator dialog and persistence-time validation. An indicator transforms an OHLCV series into one or more named line series (e.g. `{"sma": ndarray}`, `{"upper": ..., "middle": ..., "lower": ...}`), all the same length as the input candles, NaN-padded where undefined.

## Public API
- `class Indicator(Protocol)` — class-level: `kind_id: str` (stable persistence id, e.g. `"sma"`), `kind_version: int`, `params_schema: Tuple[ParamDef, ...]`, `default_style: Dict[str, LineStyle]`. Instance: `name: str`, `overlay: bool`. Method: `compute(candles) -> Dict[str, np.ndarray]`.
- `ParamDef(name, kind, default, min=None, max=None, step=None, choices=(), description="")` — `kind ∈ {"int","float","bool","str","choice"}`. Drives the dialog widget by kind.
- `LineStyle(color="#888888", width=1.2, visible=True)` — per-output-key visual default. Per-instance overrides live on `IndicatorConfig`.
- `IndicatorFactory = Callable[..., Indicator]`.
- `INDICATORS: Dict[str, IndicatorFactory]` — display registry, insertion-ordered.
- `register_indicator(name, factory)` — idempotent; also indexes by `factory.kind_id` when present.
- `factory_by_kind_id(kind_id) -> Optional[(name, factory)]` — stable-id lookup for persistence rehydration.
- `kind_id_for(name) -> Optional[str]`.
- `migrate_kind_id(kind_id, params) -> (kind_id, params)` — applies the
  `_KIND_ID_MIGRATIONS` registry to upgrade legacy persisted configs.
  Current mappings (all additive, user-supplied params win):
  - `"bbands_ema"` → `("bbands", {"ma_type": "EMA"})`
  - `"atr_sma"` → `("atr", {"ma_type": "SMA"})`
  - `"rvol_simple"` / `"rvol_cum"` / `"rvol_tod"` → `("rvol", {"mode": "simple"|"cumulative"|"time_of_day"})`
  - `"rvol_z_simple"` / `"rvol_z_cum"` / `"rvol_z_tod"` → `("rvol", {"mode": "...", "z_score": True})`
  - `"rrvol_*"` variants → `("rrvol", ...)` following the same pattern
  Called from `IndicatorConfig.from_dict` (chart configs) and
  `FieldRef.from_dict` (scanner / exits / entries) before the
  unknown-kind check, so a pre-merge stored config seamlessly hydrates
  as the unified replacement with the discriminator param baked in.

## Dependencies
- Internal: `..models.Candle`.
- External: `numpy`, `typing`.

## Design Decisions
- **`Dict[str, np.ndarray]` return shape** — Bollinger Bands, MACD, Ichimoku all produce multiple synchronous lines. A dict keyed by output name lets the render layer pick which to draw and what color to use.
- **NaN-pad first `period-1` samples** (per-indicator convention) — lets the render layer lay out lines at the same X positions as candles without head/tail special cases.
- **`overlay` as a bool attribute** — UI picks the subplot at layout time; explicit beats inferred.
- **`kind_id` is stable, display name is not** — `INDICATORS["SMA"]` may rename, but `"sma"` round-trips through saved configs forever. `_BY_KIND_ID` index is the persistence-side lookup.
- **`params_schema` is a Tuple[ParamDef]**, classvar — narrow `kind` whitelist keeps the auto-generated dialog simple. Custom indicators that need exotic types expose a `str` field with a documented format and parse internally.
- **`default_style` per output key** (not per indicator) — Bollinger's middle/upper/lower want different defaults; a single per-class color would be wrong.
- **No Tk / matplotlib imports** — fully headless so backtesters can use the layer directly.

## Invariants
- `compute(candles)` returns a dict whose every `ndarray` has length `len(candles)`.
- Undefined positions are `np.nan`.
- `INDICATORS` and `_BY_KIND_ID` persist registrations across repeat package imports.
- `register_indicator(name, factory)` is idempotent and keeps both indexes consistent.

## Testing
- `tests/smoke/test_smoke_full.py:check_d39_indicators_phase1` — registry wiring, kind_id round-trip, schema declarations, NaN-padding, value spot-checks for SMA/EMA/RSI/BB.

## Known limitations
- No `migrate(params, from_version)` hook yet — `kind_version` is reserved for it. Today unrecognized versions are loaded as-is and a status-log warning fires.
- `params_schema` doesn't support nested/structured parameters (lists, dataclasses). Keep `kind="str"` with custom parsing as the escape hatch.
