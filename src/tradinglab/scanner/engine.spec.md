# scanner/engine.py — spec

## Purpose

Pure-Python tri-valued (Kleene) scan evaluator. Takes a
`ScanDefinition` + a per-symbol bar series and returns
`Optional[bool]`. No Tk, no threads, no I/O — just NumPy + dispatch.

## Public types

- `EvaluationContext(symbol, interval, bars: BarsNp, candles, index,
  memo: IndicatorMemo, bars_registry=None)` — one symbol at one bar.
- `IndicatorMemo` — caches `factory(**params).compute(candles)` keyed
  by `(kind_id, frozenset(params.items()))`. Catches all exceptions,
  records in `errors`, caches the empty dict to prevent retry loops.

## Public API

- `evaluate_field(ref, ctx) -> Optional[float]` — at the current bar.
- `evaluate_field_at(ref, ctx, i) -> Optional[float]` — at bar `i`
  (lookback ops).
- `evaluate_condition(cond, ctx) -> Optional[bool]` — dispatches all
  19 operators on named params.
- `evaluate_group(group, ctx) -> Optional[bool]` — tri-valued AND/OR.
- `evaluate_scan(scan, ctx) -> Optional[bool]` — top-level entry.
- `validate_scan(scan, *, bars_registry=None) -> List[str]` —
  human-readable validation errors. Walks every `Condition`/`FieldRef`
  against the field registry. Cross-interval: when `bars_registry is
  None`, any `Condition.interval` / `FieldRef.interval` differing from
  `scan.primary_interval` is rejected. With a registry, that check is
  skipped (engine resolves alternate intervals on demand).
- `make_context(symbol, interval, bars, candles, index, *, memo=None,
  bars_registry=None) -> EvaluationContext` — context factory; reuses
  or creates an `IndicatorMemo`. Used by `runner.py` and the smoke
  harness.

## Tri-valued semantics

`None` propagates per Kleene logic:

| AND      | True | False | None |
| -------- | ---- | ----- | ---- |
| True     | T    | F     | None |
| False    | F    | F     | F    |
| None     | None | F     | None |

| OR       | True | False | None |
| -------- | ---- | ----- | ---- |
| True     | T    | T     | T    |
| False    | T    | F     | None |
| None     | T    | None  | None |

**Disabled children are skipped, not contributed as None.** Filtered
at the group level (before reduce), so disabled-everything degenerates
to the empty-fold (AND→True, OR→False).

## Operator dispatch

All 19 operators dispatch on named params per
`OPERATOR_PARAM_SCHEMA[op]`:

| op                 | params                      |
| ------------------ | --------------------------- |
| `> < >= <= == !=`  | `right` (field)             |
| `between`          | `low`, `high` (field)       |
| `crosses_above`    | `right` (field), `lookback` (int) |
| `crosses_below`    | `right` (field), `lookback` (int) |
| `is_rising`        | `lookback` (int)            |
| `is_falling`       | `lookback` (int)            |
| `within_pct`       | `target` (field), `tolerance_pct` (float) |
| `new_high_n_bars`  | `bars` (int)                |
| `new_low_n_bars`   | `bars` (int)                |
| `holding_above`    | `reference` (field), `bars` (int) |
| `holding_below`    | `reference` (field), `bars` (int) |
| `inside_bar`       | (none)                      |
| `outside_bar`      | (none)                      |
| `nr7`              | (none)                      |

### Subtle op semantics

- **`crosses_above`**: `prev_l ≤ prev_r AND cur_l > cur_r` (`≤` on prev
  side allows "touch then cross").
- **`is_rising`**: strict monotonic (`<`); flat sequences fail.
- **`within_pct`**: `|left - target| / abs(target) ≤ tolerance_pct/100`.
  Returns `None` (not div-by-zero) when `abs(target) < 1e-12`.
- **`new_high_n_bars`**: `cur > max(prior_n_values)` — excludes self.
- **`holding_above`**: every bar in `[i-bars+1..i]` satisfies `l > r`,
  with reference re-evaluated each bar (matches "VWAP held as support").
- **`nr7`**: `range[i] ≤ min(range[i-6..i-1])` (`≤` for tie tolerance).
- **`inside_bar` / `outside_bar` / `nr7`**: in `_NO_LEFT_OPS`. UI
  hides the left field picker.

## IndicatorMemo

- Keyed by `(kind_id, frozenset(params.items()))`. One `compute()` per
  indicator-config per symbol per tick.
- Failures: catches all exceptions, records in `errors`, caches empty
  dict so a single bad indicator doesn't poll-retry-spam every bar.
- `factory_by_kind_id(kind_id)` returns `(display_name, factory)` — a
  tuple. The memo unpacks both; callers that forget unpack get a
  TypeError at factory-call time.

## FieldRef.interval cross-interval

If `ref.interval is not None and ref.interval != ctx.interval`:

- **With `ctx.bars_registry`**: engine resolves against the `BarsView`
  for `(symbol, ref.interval)`; recursive lookup re-targets to that
  buffer's last bar ("now" semantics). Missing buffer → `None`.
- **Without a registry**: raises `NotImplementedError` (v1 gate).

`Condition.interval` follows the same pattern. Without a registry,
historical silent-`None` is preserved.

## What we *don't* do here

- Threading — `runner.py`.
- Persistence — `storage.py`.
- Tk — `gui/scanner_tab.py`.
- New-row edge detection — `runner.MatchHistory`.

## See also

- [model](model.spec.md), [fields](fields.spec.md), [runner](runner.spec.md).
