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
| `log_scale` | bool | False | **view-only** (no compute effect): render the pane on a log y-axis. Honored only on the ratio pane (`z_score=False`); a log axis can't show the z-score pane's 0.0 baseline / negatives. Opt-in spike-readability for stacking spiky modes (e.g. ToD) with smooth ones (Cumulative) on one shared scale. See `indicators/render.py` pane-group log handling + `autoscale_pane_y` log-awareness. |

### `TRIGGER_RELEVANT_PARAMS`

Class-level whitelist of params that actually affect compute output:

```
("mode", "length", "aggregator", "session_filter",
 "denominator_includes_current", "z_score")
```

`threshold_warn` / `threshold_extreme` / `log_scale` are excluded — they only
paint axhlines / set the pane y-scale. `scanner.fields._build_indicator_specs`
prunes them from the entries / exits / scanner block-editor forms; the
chart-side Manage Indicators dialog still surfaces the full schema (so the
`log_scale` checkbox is reachable there).

### Output

Single output key `"rvol"`. With `z_score=False` it is the raw rvol
ratio (centred at 1.0); with `z_score=True` it is the rolling z of
that series. The shared output key keeps dialog and persistence
simple — scanner users querying `output_key="rvol"` on a `z_score=True`
config receive z-score values.

### Scanner registration

- `scannable_outputs = (("rvol","numeric"),)` — opts the indicator into the scanner / entries / exits dropdowns via `scanner.fields._indicator_field_specs`.
- `resets_daily = True` — declares the indicator as session-anchored (cumulative & time-of-day modes are intraday-only and reset each regular session). `condition_uses_daily_reset_field` walks this flag for prefix-cache pruning.

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
The window is positional over **admitted bars** (the subset that
passes `session_filter`), not over the full bar array — so under
`session_filter='regular_only'` with extended-hours data present,
the RTH bars at the start of each session use the previous L *RTH*
bars as their window (not the previous L positional bars, most of
which would be pre-market and excluded). This fixes a long-standing
bug where the first L bars of every regular session emitted NaN
after a contiguous block of pre/post-market bars. Audit
`rvol-admitted-rolling`.

Vectorised implementation: rolling mean uses an O(n) cumsum trick;
rolling median uses `np.lib.stride_tricks.sliding_window_view` +
`np.nanmedian` (~17× faster than the legacy Python per-bar loop on
typical ~3500-bar 5-minute frames; mean is ~300× faster). RRVOL
benefits proportionally since it computes RVOL on both legs.

**`time_of_day` / `cumulative` are fully vectorised (compute #5).** Both
build a `(sessions × tod-key)` matrix from the regular-session groups
(`_regular_bar_index` → `np.add.at` for time-of-day volume sums;
segmented `cumsum` + `np.maximum.at` for cumulative last-wins volume),
then aggregate each cell over its prior `length` sessions with a single
NaN-padded `sliding_window_view` (`_rolling_session_denom`, mean via
`np.nanmean` / median via `np.nanmedian`, gated on ≥ `_MIN_WARMUP_SESSIONS`
finite samples). `cumulative` forward-fills the matrix along the sorted
tod-key axis (`_ffill_rows`) to reproduce the old per-session
`bisect_right(keys, k) - 1` step-function lookup. `session_filter` is a
no-op within the regular-only groups, so it is not consulted. Measured
~90× faster than the prior per-bar gather loops on a 120-day 5m history;
bit-equivalent on the median path and within float64 summation-order
round-off on the mean path — pinned by
`tests/unit/test_rvol_vectorized_equiv.py` against inline copies of the
original loops (fuzzed over missing tod-keys, duplicate/DST timestamps,
NaN volumes).

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
- **Vectorised (compute #5):** a length-`L-1` NaN prefix + one
  `sliding_window_view` yields each bar's trailing window; `np.nanmean`
  / `np.nanstd(ddof=1)` over the gated rows reproduce the prior loop's
  `finite.mean()` / `finite.std(ddof=1)` (median path bit-exact, mean/std
  within summation-order ULPs ~1e-15). ~63× faster on 25k bars.


### Session filter
For `simple`, `session_filter` controls which bars contribute to BOTH
numerator and denominator. `cumulative` and `time_of_day` build
regular-session groups and currently do not consult `session_filter`.
Default `regular_only` mirrors VWAP convention.

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

`mode` does NOT split the pane: RVOL Cumulative and RVOL ToD (both
`z_score=False`) share the one `"rvol"` pane intentionally — they are
the same 1.0-centered ratio unit and the high-value read is the gap
between them against shared 1×/2×/5× reference lines. The shared-pane
consumers fit/read/click ALL configs on the pane: y-autoscale unions
every config's lines (`indicators.render.lines_by_pane_axes`), the
hover readout enumerates every config (`_indicator_lines_at`), and each
config's name is its own clickable label (`_render_pane_labels` →
`_pane_indicator_label_hit`). When a spiky mode (ToD) compresses a
smooth one (Cumulative) on a shared linear scale, opt into `log_scale`
(per above) rather than splitting the pane.

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
