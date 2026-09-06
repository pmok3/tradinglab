# indicators/ichimoku.py — Ichimoku Cloud

## Purpose

Provide the traditional five-component Ichimoku visual study without exposing
ambiguous displaced values to scanner or strategy automation.

## Public API

- `IchimokuCloud(conversion_period=9, base_period=26,
  span_b_period=52, displacement=26)`
- `compute_arr(bars) -> dict[str, np.ndarray]`
- Stable `kind_id = "ichimoku"`; chart overlay; no scannable outputs.

All four parameters are positive integers capped at the shared engineering
safety limit of 500. Periods must satisfy
`conversion_period <= base_period <= span_b_period`.

## Numerical contract

For source bar `t`, each midpoint is
`(rolling_max(high, period) + rolling_min(low, period)) / 2` over an inclusive,
backward-looking window:

- `tenkan`: conversion-period midpoint.
- `kijun`: base-period midpoint.
- `senkou_span_a_raw`: `(tenkan + kijun) / 2`.
- `senkou_span_b_raw`: Span-B-period midpoint.
- `chikou_source`: current close.

Outputs are causal, input-length arrays. A window containing any non-finite
high or low is `NaN`; warm-up is `period - 1` samples. `warmup_bars` is the
configured Span-B period.

## Plot contract

Computation never shifts arrays. `output_plot_specs(params)` assigns `+D` to
both raw Senkou outputs and `-D` to Chikou; Tenkan/Kijun remain at source X.
The renderer maps displacement through observed non-gap bars and creates
abstract future slots beyond the final candle. This prevents `np.roll`
wraparound and keeps every numerical output safe for non-chart consumers.

`fill_specs` declares one independently hideable `cloud` between the two
Senkou outputs. Its bull/bear colors are resolved from the live semantic
palette and its fixed low opacity is not a compute parameter.
`semantic_output_colors` assigns the same live bull/bear roles to the two
Senkou outlines unless the user explicitly chooses custom line colors.

## Display

The compact readout is `Ichi(C,B,S,D)`, shows Tenkan and Kijun values, and
compares the displayed spans as `Cloud ↑`, `Cloud ↓`, or `Cloud =`. Span and
Chikou values remain available through cursor hover. Chikou is hidden by
default; the other four lines and cloud fill are visible.

## Symbol and session behavior

The indicator consumes exactly the bars supplied by the chart, including
extended-hours bars when Pre/Post is enabled, and updates provisionally on a
forming bar. It is unavailable for quotient ratios because their high/low
envelope is approximate. Exact scaled symbols remain supported.

## Testing

Unit tests pin formulas, warm-up boundaries, strict NaN propagation,
validation, causal prefixes, plot offsets, ratio availability, and metadata.
Render tests cover observed-bar gap displacement, future projection, cloud
crossings, visibility, cleanup, autoscale, and cache-preserving style changes.

## Future work

Scanner fields, signals, alerts, expression-DSL functions, multi-timeframe
clouds, and Strategy Tester overlays are intentionally outside v1.
