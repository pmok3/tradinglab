# `indicators/rrvol.py` — Relative-Relative Volume vs SPY

Single `RRVOL` factory (replacing three legacy classes) with z-score
support, mirroring unified `RVOL`. RRVOL = stock's RVOL ÷ SPY's RVOL
of the same flavor. Surfaces idiosyncratic activity vs market-wide
volume regime.

## Public API

`RRVOL` — `kind_id="rrvol"`. Same param shape as `RVOL`:

| Param | Type | Default | Notes |
|---|---|---|---|
| `mode` | choice | `simple` | `simple` / `cumulative` / `time_of_day` |
| `length` | int ≥ 1 | 20 | bars for `simple`; days for `cum` / `tod` |
| `aggregator` | choice | `mean` | mean / median |
| `session_filter` | choice | `regular_only` | regular / +pre / extended |
| `denominator_includes_current` | bool | False | only meaningful for `simple` |
| `z_score` | bool | False | rolling z of the **rrvol ratio**, window=`length` |
| `threshold_warn` | float | 2.0 | reference dash; ignored when `z_score=True`. Cosmetic-only |
| `threshold_extreme` | float | 5.0 | reference dash; ignored when `z_score=True`. Cosmetic-only |

`TRIGGER_RELEVANT_PARAMS` mirrors `RVOL` exactly. Threshold params are
pruned from entries / exits / scanner forms by
`scanner.fields._build_indicator_specs`.

### Output

Single output key `"rvol"` (matches RVOL). Raw ratio when
`z_score=False`; rolling z when `True`.

## Algorithm

1. Compute `RVOL(mode=…)` on the primary bars (numerator).
2. Resolve reference: `core.reference_data.get_reference_bars(source,
   "SPY", interval)`. If absent, schedule a background fetch and
   return all-NaN; the on-arrival callback fires a re-render.
3. Compute the same `RVOL(mode=…)` on SPY bars.
4. For each primary timestamp, look up the matching SPY index via a
   `dict[np.datetime64, int]`. Unmatched bars → NaN.
5. Divide numerator by denominator. `denom == 0.0` → emit `0.0` (no
   inf — matches RVOL's zero-denom convention).
6. If `z_score=True`, compute rolling sample-stddev z of the ratio
   series with window=`length`.

### Edge cases

| Scenario | Output |
|---|---|
| Primary == SPY | flat 1.0 wherever primary RVOL is finite |
| SPY not yet cached | all-NaN; provider scheduled; rerender on arrival |
| Primary timestamp without SPY match | NaN |
| IPO / insufficient lookback on either leg | NaN |
| SPY RVOL = 0 at matched bar | 0.0 |

### Z-score
Applied to the **ratio** series (not the legs). Same NaN policy and
length validation as RVOL (`length >= 2` enforced when `z_score=True`).

## Pane sharing

`pane_group_for(params)` returns `"rvol_z"` when `z_score=True`, else
`"rvol"`. RRVOL co-renders with RVOL on the same pane by default.

## Interval gating

`RRVOL.is_available_for(interval, params)` matches RVOL: `cumulative`
and `time_of_day` are intraday-only; `simple` is universal.

## Migration from legacy kind_ids

| legacy `kind_id` | migrated to | added params |
|---|---|---|
| `rrvol_simple` | `rrvol` | `{"mode": "simple"}` |
| `rrvol_cum` | `rrvol` | `{"mode": "cumulative"}` |
| `rrvol_tod` | `rrvol` | `{"mode": "time_of_day"}` |

Runs in `IndicatorConfig.from_dict` and `FieldRef.from_dict`. RRVOL
never had z-score variants before unification, so no z-score legacy
ids to migrate.
