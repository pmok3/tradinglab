# indicators/base.py — Spec

## Purpose
Declares the `Indicator` Protocol, the `INDICATORS` display registry, and the typed-parameter schema (`ParamDef`, `LineStyle`) that drives both the auto-generated Add Indicator dialog and persistence-time validation. An indicator transforms an OHLCV series into one or more named line series (e.g. `{"sma": ndarray}`, `{"upper": ..., "middle": ..., "lower": ...}`), all the same length as the input candles, NaN-padded where undefined.

## Public API
- `class Indicator(Protocol)` — class-level: `kind_id: str` (stable persistence id, e.g. `"sma"`), `kind_version: int`, `params_schema: Tuple[ParamDef, ...]`, `default_style: Dict[str, LineStyle]`. Optional class-level (ClassVar) scanner opt-in: `scannable_outputs: Tuple[Tuple[str, str], ...] = ()` — list of `(output_key, dtype)` pairs the scanner / entries / exits / ranking UI should surface. Empty tuple (the default) means "chart-only — invisible to the scanner". `dtype` is `"numeric"` or `"bool"`. `resets_daily: bool = False` — set True for session-anchored indicators (VWAP, RVOL, RRVOL) so condition validators can warn cross-interval mismatches. Instance: `name: str`, `overlay: bool`. Method: `compute(candles) -> Dict[str, np.ndarray]`.
- `class BaseIndicator` — concrete mixin that owns the canonical `compute(candles)` shim plus the `effective_output_keys(params)` classmethod (see Design Decisions). Subclasses implement `compute_arr(bars: Bars)`; the shim builds `Bars.from_candles(candles)` and forwards.
- `BaseIndicator.effective_output_keys(cls, params: dict) -> tuple[str, ...]` — classmethod declaring which output keys this indicator *actually renders* for the given params. The base returns `tuple(cls.default_style.keys())` — every key in the static `default_style` table. Indicators whose param toggles enable/disable specific outputs override this to return only the visible subset in **canonical top-down visual order**: AVWAP returns `("avwap",)` when `bands="off"` and `("upper2", "upper1", "avwap", "lower1", "lower2")` when `bands="both"`; Bollinger always returns `("upper", "middle", "lower")` (top-down visual order on the chart, NOT default_style insertion order). The in-readout overlay legend (`gui/readout_legend.py`) calls this for every indicator config to decide which output rows to render — so an AVWAP with bands disabled now shows ONE row, not five.
- `ParamDef(name, kind, default, min=None, max=None, step=None, choices=(), description="")` — `kind ∈ {"int","float","bool","str","choice"}`. Drives the dialog widget by kind.
- `LineStyle(color="#888888", width=1.2, visible=True)` — per-output-key visual default. Per-instance overrides live on `IndicatorConfig`.
- `IndicatorFactory = Callable[..., Indicator]`.
- `Availability(ok, reason="")` — interval-availability result for factories that gate themselves by interval or params.
- `intraday_only(interval) -> Availability` — shared helper for indicators that only render on intraday intervals.
- `factory_is_available_for(factory, interval, params=None) -> Availability` — resolves two-arg `is_available_for(interval, params)`, legacy one-arg `is_available_for(interval)`, legacy `available_intervals`, or defaults to available.
- `compute_via_bars(indicator, bars) -> Dict[str, np.ndarray]` — render hot-path dispatcher; prefers `indicator.compute_arr(bars)`, falls back to `indicator.compute(bars.candles)` when needed.
- `INDICATORS: Dict[str, IndicatorFactory]` — display registry, insertion-ordered.
- `register_indicator(name, factory)` — idempotent; adds to both `INDICATORS` (visible display registry → menu) and `_BY_KIND_ID` (persistence-side lookup) when the factory exposes a `kind_id`.
- `register_legacy_indicator(name, factory)` — idempotent; adds to `_BY_KIND_ID` ONLY. Used for indicator families that consolidated into a single replacement (e.g. SMA + EMA → MovingAverage): the legacy class stays discoverable for in-memory configs and tests, but is excluded from the Add Indicator menu.
- `factory_by_kind_id(kind_id) -> Optional[(name, factory)]` — stable-id lookup for persistence rehydration.
- `kind_id_for(name) -> Optional[str]`.
- `iter_indicator_factories() -> Iterator[(kind_id, name, factory)]` — registration-ordered walk over `_BY_KIND_ID`. Used by `scanner.fields._indicator_field_specs` to project ClassVar opt-ins into FieldSpecs (replaces the old hand-curated `SCANNABLE_INDICATORS` dict).
- `indicator_scannable_outputs(factory) -> Tuple[Tuple[str, str], ...]` — safe getattr on the factory's `scannable_outputs` ClassVar (empty tuple if missing). Empty tuple means the indicator opted out of the scanner.
- `indicator_resets_daily(factory) -> bool` — safe getattr on the `resets_daily` ClassVar (False default).
- `migrate_kind_id(kind_id, params) -> (kind_id, params)` — applies the
  `_KIND_ID_MIGRATIONS` registry to upgrade legacy persisted configs.
  Current mappings (all additive, user-supplied params win):
  - `"bbands_ema"` → `("bbands", {"ma_type": "EMA"})`
  - `"atr_sma"` → `("atr", {"ma_type": "SMA"})`
  - `"sma"` → `("ma", {"ma_type": "SMA", "source": "Close"})` *(chart-only — scanner FieldRefs keep `id="sma"`)*
  - `"ema"` → `("ma", {"ma_type": "EMA", "source": "Close"})` *(chart-only — scanner FieldRefs keep `id="ema"`)*
  - `"rvol_simple"` / `"rvol_cum"` / `"rvol_tod"` → `("rvol", {"mode": "simple"|"cumulative"|"time_of_day"})`
  - `"rvol_z_simple"` / `"rvol_z_cum"` / `"rvol_z_tod"` → `("rvol", {"mode": "...", "z_score": True})`
  - `"rrvol_*"` variants → `("rrvol", ...)` following the same pattern
  Called from `IndicatorConfig.from_dict` (chart configs, with
  `include_chart_only=True`) and `FieldRef.from_dict` (scanner /
  exits / entries — default `include_chart_only=False`) before the
  unknown-kind check, so a pre-merge stored config seamlessly hydrates
  as the unified replacement with the discriminator param baked in.
  The `include_chart_only` flag gates entries listed in
  `_CHART_ONLY_MIGRATION_KIND_IDS` (currently `"sma"`/`"ema"`): scanner
  surfaces intentionally keep those as separate scannable field ids
  backed by the legacy registry entries.

- `_LEGACY_MA_OUTPUT_KEYS: dict[str, str]` — maps a legacy MA `kind_id` (`"sma"` / `"ema"`) to the output-key name the legacy class persisted (`"sma"` / `"ema"`). `IndicatorConfig.from_dict` reads this BEFORE migration so it can remap the user's customised `style[legacy_key]` → `style["ma"]` after the kind_id rewrite — mirrors the `_LEGACY_Z_OUTPUT_KIND_IDS` pattern used by the RVOL family.

## Dependencies
- Internal: `..constants.INTRADAY_INTERVALS`, `..core.bars.Bars`,
  `..models.Candle`.
- External: `inspect`, `numpy`, `typing`.

## Design Decisions
- **`Dict[str, np.ndarray]` return shape** — Bollinger Bands, MACD, Ichimoku all produce multiple synchronous lines. A dict keyed by output name lets the render layer pick which to draw and what color to use.
- **NaN-pad first `period-1` samples** (per-indicator convention) — lets the render layer lay out lines at the same X positions as candles without head/tail special cases.
- **`overlay` as a bool attribute** — UI picks the subplot at layout time; explicit beats inferred.
- **`kind_id` is stable, display name is not** — `INDICATORS["SMA"]` may rename, but `"sma"` round-trips through saved configs forever. `_BY_KIND_ID` index is the persistence-side lookup.
- **`params_schema` is a Tuple[ParamDef]**, classvar — narrow `kind` whitelist keeps the auto-generated dialog simple. Custom indicators that need exotic types expose a `str` field with a documented format and parse internally.
- **`default_style` per output key** (not per indicator) — Bollinger's middle/upper/lower want different defaults; a single per-class color would be wrong.
- **Canonical compute shim.** `BaseIndicator` centralises the `compute(candles)` → `compute_arr(Bars.from_candles(candles))` bridge so built-ins do not hand-roll identical shims.
- **`effective_output_keys(params)` classmethod** (added in the `legend-condensation` sprint). Lets indicators whose param toggles enable/disable specific outputs (AVWAP `bands="off"` ⇒ no band lines) declare to the legend which output keys are visible **and** in what canonical top-down visual order. Default returns `tuple(cls.default_style.keys())`. The legend (`gui/readout_legend.py:_effective_output_keys_for`) ANDs this set with the per-output `cfg.style[key].visible` toggle to decide which output values to show in the row. Without this hook the legend rendered every key in `default_style` regardless of whether the line was drawn — so AVWAP with bands disabled showed 4 NaN band rows.
- **`legend_label(display_name, params) -> str | None` classmethod** (added in the `avwap-anchor-only-label` sprint). Indicator-class hook for overriding the consolidated readout-legend row prefix. Default returns `None` meaning "use `format_indicator_label`'s generic `params_schema` walker"; overriding returns a custom prefix string used verbatim. Use this for indicators where the generic walker produces a noisy label — currently only AVWAP (whose `price_source` + `bands` are rendering knobs, not important details — only the anchor matters). New indicators should leave this alone unless the same noisy-label problem applies.
- **No Tk / matplotlib imports** — fully headless so backtesters can use the layer directly.

## Invariants
- `compute(candles)` returns a dict whose every `ndarray` has length `len(candles)`.
- Undefined positions are `np.nan`.
- `INDICATORS` and `_BY_KIND_ID` persist registrations across repeat package imports.
- `register_indicator(name, factory)` is idempotent and keeps both indexes consistent.
- `BaseIndicator.compute(candles)` is pure boilerplate; indicator-specific math lives in `compute_arr(bars)`.

## Testing
- `tests/smoke/test_smoke_full.py:check_d39_indicators_phase1` — registry wiring, kind_id round-trip, schema declarations, NaN-padding, value spot-checks for SMA/EMA/RSI/BB.

## Known limitations
- No `migrate(params, from_version)` hook yet — `kind_version` is reserved for it. Today unrecognized versions are loaded as-is and a status-log warning fires.
- `params_schema` doesn't support nested/structured parameters (lists, dataclasses). Keep `kind="str"` with custom parsing as the escape hatch.
