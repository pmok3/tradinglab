# `indicators/rvol.py` — unified Relative Volume

A single `RVOL` factory replacing six legacy classes (three modes ×
{raw, z-score}). All behaviour discriminated by params. Answers "is
this stock trading at an unusual rate vs its recent normal pace?".

## Public API

`RVOL` — `kind_id="rvol"`. Mode-aware availability:

| `mode` | description | available on |
|---|---|---|
| `simple` (default) | rolling baseline of the previous `length` bars | every interval |
| `cumulative` | session-cumulative volume vs same-time historical cumulative | intraday only |
| `time_of_day` | this bar vs same-wall-clock historical bars | intraday only |

### Params

| Param | Type | Default | Notes |
|---|---|---|---|
| `mode` | choice | `simple` | `simple` / `cumulative` / `time_of_day` |
| `length` | int ≥ 1 | 20 | bars for `simple`; days for `cum` / `tod` |
| `aggregator` | choice | `mean` | `mean` / `median` |
| `session_filter` | choice | `regular_only` | `regular_only` / `regular_plus_premarket` / `extended` |
| `denominator_includes_current` | bool | False | only meaningful for `simple` |
| `z_score` | bool | False | output = rolling sample-stddev z of rvol series, window=`length` |
| `threshold_warn` | float | 2.0 | reference dash; ignored when `z_score=True`. Cosmetic-only |
| `threshold_extreme` | float | 5.0 | reference dash; ignored when `z_score=True`. Cosmetic-only |

### `TRIGGER_RELEVANT_PARAMS`

Class-level whitelist of params that actually affect compute output:

```
("mode", "length", "aggregator", "session_filter",
 "denominator_includes_current", "z_score")
```

`threshold_warn` / `threshold_extreme` are excluded — they only paint
axhlines. `scanner.fields._build_indicator_specs` prunes them from
the entries / exits / scanner block-editor forms; the chart-side
Manage Indicators dialog still surfaces the full schema.

### Output

Single output key `"rvol"`. With `z_score=False` it is the raw rvol
ratio (centred at 1.0); with `z_score=True` it is the rolling z of
that series. The shared output key keeps dialog and persistence
simple — scanner users querying `output_key="rvol"` on a `z_score=True`
config receive z-score values.

## Algorithm

### Time-of-day key
`cumulative` and `time_of_day` key by **HH:MM in exchange-local
wall-clock time** via `sessions.tod_key_np` — correct under half-day
sessions, missing bars, and DST shifts.

### Aggregator
`mean` default; `median` available for robustness against
earnings/news-day outliers in the lookback window.

### Warmup
For `cumulative` and `time_of_day`: NaN until at least
`_MIN_WARMUP_SESSIONS = 5` prior sessions are available (module-level
constant, independent of `length`); from then on, partial values are
emitted until `length` is reached. Truly zero history → NaN.

`simple` requires the full `length` window — no partial-warmup mode.

### Z-score (`z_score=True`)
Rolling sample-stddev z with window=`length`:

```
z[i] = (rvol[i] - mean(window)) / std(window, ddof=1)
```

- NaN underlying values dropped from window stats.
- Zero-stddev window → NaN.
- Window needs ≥ 2 finite samples; else NaN.
- Constructor enforces `length >= 2` when `z_score=True`.
- Window is always **bar count**, even when `length` is otherwise
  in days (`cumulative` / `time_of_day`). Matches legacy z-score
  family behaviour.

### Session filter
`session_filter` controls which bars contribute to BOTH numerator
and denominator. Default `regular_only` mirrors VWAP convention.

### Reference levels
`z_score=False`: `1.0 / threshold_warn / threshold_extreme`.
`z_score=True`: `0.0 / 2.0` (Bellafiore +2σ line). The render layer
dedupes the union of levels across multiple RVOL configs sharing the
same pane.

### Zero-denominator
Both `0/0` and `N>0 / 0` emit `0.0` (not `inf`) — conflates "no
history" with "quiet stock" but avoids distorting the autoscaled
y-axis of the shared pane.

## Pane sharing

`pane_group_for(params)` returns `"rvol_z"` when `z_score` is truthy,
else `"rvol"`. `config.effective_pane_group` prefers this over the
persisted `cfg.pane_group`, so toggling `z_score` triggers an instant
pane reflow on the next render.

## Interval gating

`RVOL.is_available_for(interval, params)` is **params-aware**:
`cumulative` / `time_of_day` are intraday-only; `simple` (or default)
is universal. Enforced in two places:

1. `IndicatorConfig.applies_to(scope, interval)` routes params through
   the factory's availability check — the render layer auto-filters
   configs that shouldn't render at the current interval.
2. The Manage Indicators dialog annotates the entry with
   `(needs intraday)` only when `cumulative` / `time_of_day` is
   selected.

## Migration from legacy kind_ids

`_KIND_ID_MIGRATIONS` in `indicators/base.py`:

| legacy `kind_id` | migrated to | added params |
|---|---|---|
| `rvol_simple` | `rvol` | `{"mode": "simple"}` |
| `rvol_cum` | `rvol` | `{"mode": "cumulative"}` |
| `rvol_tod` | `rvol` | `{"mode": "time_of_day"}` |
| `rvol_z_simple` | `rvol` | `{"mode": "simple", "z_score": True}` |
| `rvol_z_tod` | `rvol` | `{"mode": "time_of_day", "z_score": True}` |
| `rvol_z_cum` | `rvol` | `{"mode": "cumulative", "z_score": True}` |

Runs in `IndicatorConfig.from_dict` (chart configs) and
`FieldRef.from_dict` (scanner / exits / entries). Legacy z-score
configs persisted `style["z"]`; `IndicatorConfig.from_dict` remaps to
`style["rvol"]` so user customisation survives. Same shim in
`FieldRef.from_dict` for `output_key="z"` → `"rvol"`.
