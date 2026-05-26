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

## FieldRef.symbol cross-symbol (Phase 1+2)

If `ref.symbol` is non-empty AND differs from `ctx.symbol`,
`evaluate_field_at` builds a sibling sub-context via
`_sub_context_for_symbol_at_ts(ctx, ref.symbol)` — the canonical
**bar-time-snapped** cross-ticker resolution path:

1. Pulls `BarsView` for `(ref.symbol, ctx.interval)` from
   `ctx.bars_registry`. Missing registry OR missing view → `None`.
2. Snaps the sub-context's `current_index` to the largest dependency
   bar whose timestamp `≤ ctx.bars.timestamps[ctx.current_index]`.
   No such bar (dependency starts AFTER the active bar's ts — e.g.
   the dependency hasn't IPO'd yet) → `None`.

The non-snapping variant `_sub_context_for_symbol(ctx, symbol)`
returns a sub-context pointing at the dependency's last-available
bar; reserved for callers that want "most recent dependency value"
semantics rather than time-aligned.

### Precedence (symbol-first, then interval)

`evaluate_field_at` applies swaps in this order:

1. **Symbol swap** if `ref.symbol` set and differs from `ctx.symbol`.
   After the swap, `index` is replaced with the snapped sub-context's
   `current_index` (its time-aligned position in the dependency buffer).
2. **Interval swap** on the (possibly already symbol-swapped) sub-context
   if `ref.interval` set and differs.
3. Resolve the field on the final sub-context.

So a `FieldRef.indicator("ema", params={"length": 20}, symbol="SPY",
interval="1d")` on an AAPL 5m context resolves to "SPY at 1d at the
1d bar with timestamp ≤ AAPL's current 5m bar ts".

`_strip_symbol(ref)` / `_strip_interval(ref)` produce copies with
the respective slot cleared — used internally to prevent infinite
re-swaps in the recursive resolution path.

### Bar-time-snap edge cases

- **Dependency halt (gap):** active ts falls between two dep bars →
  the most-recent dep bar at-or-before is used (no None).
- **Pre-IPO:** active ts < dep's first bar ts → `None` (Kleene
  propagates; the comparison evaluates to None and the Group
  combinators handle it per the standard truth tables).
- **Intraday extended-hours:** Phase 1+2 makes no assumption about
  session filtering — caller decides what bars land in the registry.

## What we *don't* do here

- Threading — `runner.py`.
- Persistence — `storage.py`.
- Tk — `gui/scanner_tab.py`.
- New-row edge detection — `runner.MatchHistory`.

## See also

- [model](model.spec.md), [fields](fields.spec.md), [runner](runner.spec.md).
