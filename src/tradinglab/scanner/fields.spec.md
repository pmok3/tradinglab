# scanner/fields.py — spec

## Purpose

Catalog of scannable fields. Single source of truth consumed by:

- the **engine** for validation and dispatch,
- the **block-editor GUI** for combobox population,
- the **fields registry test suite** for shape enforcement.

## Two field kinds

### Built-in scalars

Declared inline. Cheap, schema-stable values computed directly from
OHLCV NumPy arrays. v1 set:

| id                | label              | description                                 |
| ----------------- | ------------------ | ------------------------------------------- |
| `close`           | Close              | Closing price                               |
| `open`            | Open               | Opening price                               |
| `high`            | High               | Bar high                                    |
| `low`             | Low                | Bar low                                     |
| `volume`          | Volume             | Bar volume                                  |
| `pct_change`      | % Change           | Percent change vs prior close               |
| `gap_pct`         | Gap %              | Open vs prior-close gap, percent            |
| `hod`             | High of Day        | Highest high so far today                   |
| `lod`             | Low of Day         | Lowest low so far today                     |
| `time_of_day`     | Time of Day (min)  | Minutes since midnight UTC                  |
| `bars_since_open` | Bars Since Open    | Bars since first regular-session bar today  |
| `ha_open`         | HA Open            | Heikin-Ashi open                            |
| `ha_high`         | HA High            | Heikin-Ashi high                            |
| `ha_low`          | HA Low             | Heikin-Ashi low                             |
| `ha_close`        | HA Close           | Heikin-Ashi close                           |
| `ha_color`        | HA Color (+1/-1)   | +1 if HA bullish (close≥open), -1 bearish   |
| `ha_flat_top`     | HA Flat-Top        | 1 iff HA bar has no upper wick (direction-agnostic) |
| `ha_flat_bottom`  | HA Flat-Bottom     | 1 iff HA bar has no lower wick (direction-agnostic) |
| `ha_flat_bottom_bull` | HA Flat-Bottom (Bull) | 1 iff bull HA bar (`HA_close > HA_open`) has no lower wick |
| `ha_flat_top_bear`    | HA Flat-Top (Bear)    | 1 iff bear HA bar (`HA_close < HA_open`) has no upper wick |
| `ha_flat_strong`      | HA Flat (signed)      | +1 bull-flat-bottom / -1 bear-flat-top / 0 neither; None during warm-up |
| `ha_streak`       | HA Streak (signed) | Signed run of same-color HA bars (+N / -N)  |
| `ha_flat_top_streak`    | HA Flat-Top Streak    | Consecutive flat-top bars (bear continuation)  |
| `ha_flat_bottom_streak` | HA Flat-Bottom Streak | Consecutive flat-bottom bars (bull continuation) |
| `key_bar`                  | Key Bar (signed)            | +1 bull / -1 bear / 0 not-key-bar; None during warmup |
| `key_bar_bull`             | Key Bar (Bull)              | 1 iff this bar is a bull key bar                  |
| `key_bar_bear`             | Key Bar (Bear)              | 1 iff this bar is a bear key bar                  |
| `bars_since_bull_key_bar`  | Bars Since Bull Key Bar     | Bars elapsed since most recent bull key bar       |
| `bars_since_bear_key_bar`  | Bars Since Bear Key Bar     | Bars elapsed since most recent bear key bar       |
| `last_bull_key_bar_high`   | Last Bull Key Bar High      | High of most recent bull key bar                  |
| `last_bull_key_bar_low`    | Last Bull Key Bar Low       | Low  of most recent bull key bar                  |
| `last_bear_key_bar_high`   | Last Bear Key Bar High      | High of most recent bear key bar                  |
| `last_bear_key_bar_low`    | Last Bear Key Bar Low       | Low  of most recent bear key bar                  |

`hod`/`lod` are **prefix-restricted** to bars `[0..i]` (no look-ahead).

### Safe-scalar helpers

To keep the ~30 single-bar builtins free of repeated OOB / NaN /
sentinel boilerplate, `fields.py` exposes two internal helpers near
the top of the module:

- `_scalar_at(arr, i, *, sentinel=None, sentinel_predicate=None)` —
  the canonical "give me `arr[i]` as a Python float, or `None` if I
  shouldn't trust it" guard. Returns `None` for OOB indices,
  non-finite values (NaN / ±inf via `np.isfinite`), literal sentinel
  matches (`sentinel=`), or predicate-matching sentinels
  (`sentinel_predicate=`). Pass at most one of `sentinel` /
  `sentinel_predicate`; pass neither for a plain float-finite check.
- `_two_finite(a, b, i)` — returns `(float(a[i]), float(b[i]))` when
  both arrays have a finite value at `i`, else `None`. Used by HA
  builtins that need an open/close or open/high pair.

Back-compat thin wrappers preserved so existing call sites keep
working:

- `_at(arr, i)` → `_scalar_at(arr, i)` (no sentinel; plain finite
  check).
- `_kb_at_int8(arr, i)` → `_scalar_at(arr, i, sentinel=-128)` —
  decodes the `KEY_BAR_UNKNOWN = -128` sentinel used by the int8
  signed key-bar array as tri-valued `None`.
- `_kb_at_int64(arr, i)` → `_scalar_at(arr, i,
  sentinel_predicate=lambda v: v < 0)` — decodes the "no bull/bear
  key bar yet" sentinel (`-1`) used by the int64 `bars_since_*`
  arrays as tri-valued `None`.

The helpers are private (leading underscore) but the *names* are
stable — they're called from sibling builtins module-internally and
relied upon by the colocated tests in `tests/scanner/`.

### Session-day cache

`hod`, `lod`, `time_of_day`, and `bars_since_open` all need session-day
derivations from `b.timestamps` / `b.session`. Computing
`b.timestamps.astype("datetime64[D]")` fresh per call was O(N) per bar,
making scanner evaluation O(N²) over a strategy_tester Run. The
`_days_for(b)` helper caches one `datetime64[D]` array per `BarsNp` on
the same `BarsKeyedCache` LRU pattern (max 64 entries, `id(bars)` +
length key, identity-recycle guard) used by the HA / key-bar clusters.

`_session_day_arrays_for(b)` builds a second cached bundle in one O(N)
pass:

- prefix high-of-day, skipping non-finite highs,
- prefix low-of-day, skipping non-finite lows,
- UTC minutes since midnight,
- bars since first `session == "regular"` bar of the same calendar day
  (premarket bars before the first regular print remain `0.0`).

The individual builtins read one scalar from that bundle, so repeated
per-bar evaluation no longer builds boolean day masks or scans the
same day prefix. `_today_mask` is retained for tests/back-compat and
still consumes `_days_for(b)`.

### Heikin-Ashi builtins

`ha_*` project `core.heikin_ashi.ha_arrays` over `[0..i]` (same
no-look-ahead rule). Four HA arrays cached on a process-global LRU
keyed by `(id(bars), len)` so multiple `ha_*` fields against the same
`BarsNp` snapshot share one O(n) compute.

Boolean-style fields return `±1.0`/`1.0`/`0.0` (not Python bools) to
fit the float-only field-compute signature. Flat-wick equality uses
price-scaled tolerance (`max(1e-9, |price|·1e-9)`) for FP drift.

The **direction-aware** trio (`ha_flat_bottom_bull`, `ha_flat_top_bear`,
`ha_flat_strong`) narrows the direction-agnostic fields by requiring
bar color to match the trend (strict-greater bull / strict-less bear;
doji never qualify). Shares its compute
(`core.ha_flat.compute_ha_flat_arrays_np`) with the View → Highlight
Flat HA Candles overlay so chart and conditions cannot disagree.
Cached per-`BarsNp` on `BarsKeyedCache[HAFlatArrays]`.

`ha_streak`, `ha_flat_top_streak`, `ha_flat_bottom_streak` walk
backward; stop at the first run-break *or* first NaN bar (gap).

HA fields are **builtins, not indicators** — not in
`SCANNABLE_INDICATORS`. Independent of the View → Heikin-Ashi Candles
display toggle (which is a render-time substitution).

### Key bar builtins

`key_bar*` / `*_key_bar_*` project
`core.key_bar.compute_key_bar_arrays(candles)` over a candle list
reconstructed from `BarsNp`. Cached process-globally keyed on
`id(BarsNp)` plus identity check (guards id-recycling). LRU cap 64.

A bar is a key bar when **all** three clear:

1. `tr > 1.0 × baseline_tr`,
2. `rvol > 1.1`,
3. `|close − open| / (high − low) > 0.69`.

Direction: `close > open` → bull (+1); `close < open` → bear (−1);
equal close/open emits `0`.

Baselines are interval-aware:
- **Intraday**: TR via `ATR(mode="tod", length=20)`; volume via
  `TimeOfDayRVOL(lookback_days=20, aggregator="mean",
  session_filter="regular_only")` (already a ratio; threshold `>1.1`).
- **Daily/weekly/monthly**: rolling 20-bar mean of TR and volume.

**Asymmetry note**: range comparison uses TR (with gap term);
body-ratio denominator uses (H−L) (no gap). Matches how traders
eyeball bars.

`key_bar` returns `None` during warmup; `*_bull`/`*_bear` same, else
`0.0` when not a key bar in that direction. `bars_since_*_key_bar`
returns `None` if no matching key bar seen (avoids spurious `0`
matches).

Shares compute with the chart's *View → Highlight Key Bars* toggle.

### Scanner opt-in via `Indicator.scannable_outputs` ClassVar

Each indicator class declares its own scanner exposure via two
ClassVars on `indicators.base.Indicator` (Protocol):

- `scannable_outputs: Tuple[Tuple[str, str], ...] = ()` — list of
  `(output_key, dtype)` pairs. Empty tuple (the default) means
  "chart-only — invisible to the scanner". `dtype` is `"numeric"` or
  `"bool"`. First entry is the default output key when
  `FieldRef.output_key` is empty.
- `resets_daily: bool = False` — set True for session-anchored
  indicators (VWAP, RVOL, RRVOL) so `field_ref_resets_daily` /
  `condition_uses_daily_reset_field` flag cross-interval mismatches.

`_indicator_field_specs` walks `iter_indicator_factories()` (the
ordered `_BY_KIND_ID` registry, which sees both `register_indicator`
and `register_legacy_indicator` entries) and projects every factory
whose `scannable_outputs` is non-empty into a `FieldSpec`.

**Fail-closed by design.** New indicator authors must opt-in by
declaring the ClassVar on the class. This kills the footgun where a
categorical/boolean output gets picked in a numeric comparison and
silently returns `None`. It also lets v0.3.0 Custom Indicator Builder
users tick "Expose to scanner" in the dialog and have their indicator
appear in scanner / entries / exits dropdowns without editing any
hand-curated allowlist.

v1 indicators that opt in: sma, ema, rsi, bbands (middle/upper/lower),
atr, adx (adx/+di/-di — pre-existing key inconsistency vs compute's
`plus_di`/`minus_di`, preserved for back-compat), vwap (daily-reset),
avwap, smi (smi/signal), lrsi, rvol (daily-reset), rrvol (daily-reset).

The unified `MovingAverage` (kind_id `"ma"`) intentionally does NOT
opt in — `_CHART_ONLY_MIGRATION_KIND_IDS = {"sma", "ema"}` keeps the
legacy SMA/EMA classes scannable as separate field ids so persisted
scanner / entries / exits FieldRefs don't break.

Unified `rvol`/`rrvol` ids cover all flavours (simple / cumulative /
time_of_day, optional z-score) via the indicator's own `mode` and
`z_score` params. Legacy ids (`rvol_simple`, `rvol_cum`, `rvol_tod`,
`rvol_z_*`, `rrvol_*`) are migrated transparently by
`FieldRef.from_dict`.

#### Back-compat shims

`SCANNABLE_INDICATORS` (dict) and `INDICATORS_RESETTING_DAILY`
(tuple) are kept as **lazy module-level attributes** resolved via
`__getattr__` (PEP 562). Reading either re-walks the registry on each
access, so a custom indicator registered after module import becomes
visible immediately. These names are pre-migration test fixtures
only; new code should call `scannable_indicators()` /
`indicators_resetting_daily()` directly.

## Public API

- `scannable_indicators() -> dict[str, tuple[tuple[str, str], ...]]` —
  registry projection in `_BY_KIND_ID` order. Same shape as the legacy
  `SCANNABLE_INDICATORS` constant. Re-computed on each call so plugin
  indicators registered after import become visible.
- `indicators_resetting_daily() -> tuple[str, ...]` — kind_ids of
  scannable indicators whose `resets_daily` ClassVar is True.
- `all_fields() -> list[FieldSpec]` — full catalog, builtins first.
- `get_field(id, kind="") -> FieldSpec | None` — lookup by stable id.
- `is_scannable(ref) -> bool` — quick truthy check.
- `validate_field_ref(ref) -> None` — raises on unknown id / disallowed
  output key.
- `builtin_compute(field_id) -> Callable | None` — for engine dispatch.
- `field_ref_resets_daily(ref) -> bool` — True when the field resets at
  the start of every regular session (HOD/LOD, time_of_day,
  bars_since_open, cumulative RVOL, session-anchored VWAP, …). Used by
  engine/runner to decide whether a condition's cached prefix needs
  re-evaluation at session boundaries.
- `condition_uses_daily_reset_field(node) -> bool` — recursive walk of
  `Condition`/`Group`; mirrors daily-reset awareness up to the group
  level for conservative prefix-cache pruning.

## What we *don't* do here

- Run indicator `compute()` — the engine does that.
- Cache results across scans — the runner-scope memo does that.
- Decide tri-valued logic — the engine does. Builtin computes return
  `None` only for OOB / NaN / division-by-zero.

## Extension contract

New indicator output:
1. Declare `scannable_outputs: ClassVar[tuple[tuple[str, str], ...]] = ((output_key, "numeric"), ...)` on the indicator class — this is the registration; no further wiring needed.
2. If the indicator is session-anchored (resets at the start of every regular session), also declare `resets_daily: ClassVar[bool] = True`.
3. Register the indicator under `INDICATORS` as usual (`register_indicator(name, IndicatorClass)`).

New builtin scalar:
1. Implement `_b_<name>(bars, i, params) -> Optional[float]`.
2. Append a `FieldSpec` to `_BUILTINS`.

## Cross-interval ownership

`FieldRef.interval` overrides are persisted and structurally accepted
here. `validate_field_ref` does NOT require a matching
`BarsRegistry`; the engine owns that behavioral check and resolves
alternate intervals only when the caller supplies registry context.
