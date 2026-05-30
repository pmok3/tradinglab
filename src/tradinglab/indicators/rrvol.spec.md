# indicators/rrvol.py — Spec

Single `RRVOL` factory (replacing three legacy classes) with z-score
support, mirroring unified `RVOL`. RRVOL = stock's RVOL ÷ comparison
symbol's RVOL of the same flavor. Surfaces idiosyncratic activity vs
the chosen benchmark's volume regime (broad market by default; any
ticker the data source can resolve via the `compare_symbol` param).

## Public API

`RRVOL` — `kind_id="rrvol"`. Same param shape as `RVOL` plus
`compare_symbol`:

| Param | Type | Default | Notes |
|---|---|---|---|
| `mode` | choice | `simple` | `simple` / `cumulative` / `time_of_day` |
| `length` | int ≥ 1 | 20 | bars for `simple`; days for `cum` / `tod` |
| `aggregator` | choice | `mean` | mean / median |
| `session_filter` | choice | `regular_only` | regular / +pre / extended |
| `denominator_includes_current` | bool | False | only meaningful for `simple` |
| `z_score` | bool | False | rolling z of the **rrvol ratio**, window=`length` |
| `compare_symbol` | str (open-choice) | `SPY` | denominator ticker. Editable combobox in the dialog seeded with SPY/QQQ/IWM/DIA + XL\* sector SPDRs (see `COMPARE_SYMBOL_SUGGESTIONS`); free-typing supported. Validated on Save and Close via `validate_compare_symbol`. Audit `rrvol-compare-symbol`. |
| `threshold_warn` | float | 2.0 | reference dash; ignored when `z_score=True`. Cosmetic-only |
| `threshold_extreme` | float | 5.0 | reference dash; ignored when `z_score=True`. Cosmetic-only |

`TRIGGER_RELEVANT_PARAMS` mirrors `RVOL` plus `compare_symbol`
(switching benchmarks changes the denominator entirely, so triggers
must invalidate). Threshold params are pruned from entries / exits /
scanner forms by `scanner.fields._build_indicator_specs`.

### Output

Single output key `"rvol"` (matches RVOL). Raw ratio when
`z_score=False`; rolling z when `True`.

### Scanner registration

- `scannable_outputs = (("rvol","numeric"),)` — opts the indicator into the scanner / entries / exits dropdowns. Note the output key is `"rvol"` (same as RVOL, intentional).
- `resets_daily = True` — declares the indicator as session-anchored so `condition_uses_daily_reset_field` evicts cached prefixes at session boundaries.

## Algorithm

1. Compute `RVOL(mode=…)` on the primary bars (numerator).
2. Resolve `compare_symbol` (uppercased): `core.reference_data.get_reference_bars(source, compare_symbol, interval)`. If absent, schedule a background fetch and return all-NaN; the ChartApp on-arrival callback clears `IndicatorCache` and fires an indicator redraw so the pane repaints when the comparison bars land.
3. Compute the same `RVOL(mode=…)` on the comparison bars.
4. For each primary timestamp, look up the matching comparison index via a
   sorted-`searchsorted` vectorized join. Unmatched bars → NaN.
5. Divide numerator by denominator. `denom == 0.0` → emit `0.0` (no
   inf — matches RVOL's zero-denom convention).
6. If `z_score=True`, compute rolling sample-stddev z of the ratio
   series with window=`length`.

### Edge cases

| Scenario | Output |
|---|---|
| Primary == compare_symbol | flat 1.0 wherever primary RVOL is finite when `z_score=False`; all-NaN under `z_score=True` because the constant ratio has zero stddev |
| Comparison not yet cached | all-NaN; provider scheduled; rerender on arrival |
| Primary timestamp without comparison match | NaN |
| IPO / insufficient lookback on either leg | NaN |
| Comparison RVOL = 0 at matched bar | 0.0 |
| Legacy persisted config (no `compare_symbol`) | defaults to `"SPY"` |

### Z-score

Applied to the **ratio** series (not the legs). Same NaN policy and
length validation as RVOL (`length >= 2` enforced when `z_score=True`).

## Compare-symbol validation (`validate_compare_symbol`)

Module-level helper called by the indicator dialog's Save-and-Close
validator (see `gui/indicator_dialog.py`'s `_collect_save_close_errors`).
Returns `(ok: bool, error_msg: str)`. Regex gate `^[A-Z][A-Z0-9.\-]{0,6}$`
— intentionally permissive; we cannot verify the ticker resolves to
bars without a network call, so we only catch obvious typos. The
actual data-availability check happens implicitly: if the symbol
doesn't resolve, the fetcher fails, RRVOL stays all-NaN, and the
existing fallback (logger warning, blank pane) kicks in. Audit
`rrvol-compare-symbol`.

## Display name

`self.name` = `f"RRVOL{mode_short}{suffix}({length}){cmp_suffix}"` where
`cmp_suffix = " vs {compare_symbol}"` when ≠ "SPY", else empty. Keeps
the legend tidy for the default case while being self-documenting on
benchmark switches.

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
ids to migrate. The `compare_symbol` param defaults to `"SPY"` so
configs persisted before the parameter existed round-trip cleanly.
