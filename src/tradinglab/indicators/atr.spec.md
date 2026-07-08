# indicators/atr.py — Spec

## Purpose
Average True Range as a pane indicator with user-selectable smoothing
kernel and **mode**: classical rolling average (Wilder/SMA/EMA/WMA)
or **time-of-day** baseline (mean TR of the same wall-clock-time bar
across the last N regular sessions). Output is in price units
(`[0, +∞)`); no canonical reference level.

## Public API
- `class ATR(length=-1, ma_type="RMA", mode="rolling",
  session_filter="regular_only", aggregator="mean")` —
  `kind_id="atr"`, `kind_version=2`, `overlay=False`,
  `reference_levels=()`.
  The `length=-1` sentinel triggers a mode-aware default:
  **14** in rolling mode, **20** in tod mode.
- `params_schema`:
  - `length: int` (default sentinel `-1`, min 2, max 2000).
  - `ma_type: choice` (default `"RMA"`, choices `RMA | SMA | EMA |
    WMA`) — rolling mode only; inert in tod.
  - `mode: choice` (default `"rolling"`, choices `rolling | tod`).
  - `session_filter: choice` (default `"regular_only"`, choices
    `regular_only | regular_plus_premarket | extended`) — tod only.
  - `aggregator: choice` (default `"mean"`, choices `mean | median`)
    — tod only.
- `default_style.atr`: light orange `#ffbb78`, width 1.4; non-default
  MA colors use `_palette` tab10 constants.
- `scannable_outputs = (("atr","numeric"),)` — opts the indicator into the scanner registry.
- `compute(candles) -> {"atr": ndarray}`.
- `warmup_bars` property returns `4 * length` for `ma_type="RMA"`,
  otherwise `length`, so the strategy tester hydrates Wilder IIR output
  beyond first finite emit.
- `name`: `ATR(N)` (default `RMA` rolling), `ATR-{KIND}(N)` (other
  rolling kernel), `ATR ToD(N)` (tod regardless of kernel).
- **Incremental protocol (compute #3):** `inc_init(bars)` / `inc_step(state, bars, *, prev_len)` extend ATR O(k) on a closed-bar append (~100× per 1-bar tick on an 11k-bar series). State = `{avg, last_close, seeded}` plus the cached `output`/`len`. `inc_step` continues the Wilder recurrence on the per-bar True Range (computed with the exact `wilder.true_range` where-ordering so it is bit-faithful). **Gated to the default `mode="rolling"` + `ma_type="RMA"`** (`_inc_supported()`); SMA/EMA/WMA rolling and the `tod` mode leave `seeded=False` so `inc_step` raises → the cache does a full recompute. Causal-prefix-exact; appended bars differ by float64 round-off only (~7e-15 over 300 appends). Pinned by `tests/unit/test_incremental_indicators_wilder.py`.

## Modes

### `rolling` (default; back-compat)
`apply_ma(ma_type, true_range, length)`. First defined value lands at
index `length` (TR[0] is NaN).

### `tod` (intraday)
Same-wall-clock baseline:
1. Compute `tr` over the full series.
2. Group bars by regular-session boundary; build per-session map
   `{(hour, minute): tr_value}`, keeping first occurrence on dup keys
   (DST / dup-bar guard).
3. For each bar `i` in session `s` (with `s >= _MIN_WARMUP_SESSIONS`),
   collect TR at the same wall-clock key from sessions `[s - length, s)`.
   If at least `_MIN_WARMUP_SESSIONS` contain that key, emit
   `aggregator(values)`; else NaN. The per-session aggregate is scattered
   onto that session's admitted bars with a single vectorized fancy-index
   assignment (`out[grp[sel]] = agg_per_col[cols[sel]]`, where `sel =
   admit_mask & ready_cols & isfinite`), NOT a Python per-bar loop —
   bit-for-bit identical, pinned by `tests/unit/test_atr_tod.py`.
4. `_MIN_WARMUP_SESSIONS = 5` (parallels `rvol_tod`).

### `tod` (daily / weekly / monthly)
Each daily-class bar IS its own time-of-day, so the baseline
collapses to a plain 20-bar rolling mean of TR
(`_TOD_DAILY_FALLBACK_LENGTH = 20`, independent of `length`).

## Dependencies
- Internal: `..core.bars.Bars`, `.base.BaseIndicator`,
  `._palette`, `.base.LineStyle`, `.base.ParamDef`,
  `.ma_kernels.{MA_TYPES,apply_ma}`, `.wilder.true_range`,
  `.wilder.wilder_smooth_avg`,
  `.sessions.{is_intraday_np,session_filter_mask_np,session_groups_np,tod_key_np}`.
- External: `numpy`.

## Design Decisions
- **Single class, mode-discriminated** — preserves the chart-overlay
  ATR UX without scattering selection logic across the dialog.
- **Length default flips with mode** (14 ↔ 20); `length` is bars in
  rolling, sessions in tod.
- **`kind_version` not bumped (still 2).** Param keys are part of the
  cache hash; old configs rehydrate cleanly through ParamDef defaults.
- **No reference level** — ATR is unit-bearing.
- **TR delegated to `wilder.true_range`** — shared with ADX. Rolling
  `ma_type="RMA"` delegates through `apply_ma` to the vectorized Wilder
  kernel rather than an indicator-local loop.
- **ToD baseline is fully vectorized.** The per-session ToD map is a
  dense `(n_sessions × n_tod_keys)` TR matrix (`np.unique` +
  `return_inverse` for the key columns); the rolling per-column
  aggregate (`np.nanmedian`/mean over the `[s-length, s)` window) is
  computed once per current session and **scattered onto that session's
  admitted bars with one fancy-index assignment**
  (`out[grp[sel]] = agg_per_col[cols[sel]]`) — no per-bar Python loop on
  the tod hot path. Bit-for-bit identical to the prior loop; pinned by
  `tests/unit/test_atr_tod.py`.

## Invariants
- All defined ATR values are `>= 0`.
- Rolling: first defined value at index `length`.
- ToD intraday: first defined value lands in session
  `_MIN_WARMUP_SESSIONS` (zero-indexed) at the earliest, conditional
  on baseline coverage.
- ToD daily fallback: first defined value at index
  `_TOD_DAILY_FALLBACK_LENGTH = 20`.

## Known limitations
- Anchored / time-bracketed ATR not implemented; tod keys only on
  `(hour, minute)`, ignoring session-boundary-relative offsets.
