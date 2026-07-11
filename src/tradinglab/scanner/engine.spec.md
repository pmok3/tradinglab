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
  (lookback ops). For an **expression** operand (`ref.kind ==
  "expression"`), resolved *before* the symbol/interval swap by walking
  `ref.terms`: each operand leaf is resolved via a recursive
  `evaluate_field_at(leaf, ctx, i)` (so custom-indicator / cross-symbol /
  cross-interval leaves all work) and the infix stack is folded to a
  scalar by `model.evaluate_expression`. `None` propagates if any operand
  is `None`.
- `evaluate_condition(cond, ctx) -> Optional[bool]` — dispatches all
  19 operators on named params.
- `evaluate_group(group, ctx) -> Optional[bool]` — tri-valued AND/OR.
- `evaluate_group_vec(group, ctx) -> Optional[tuple[np.ndarray, np.ndarray]]`
  — **all-bars vectorized** evaluation. Returns `(is_true, is_false)`
  boolean masks of length `len(ctx.bars)` encoding the per-bar tri-valued
  result (True where `is_true`, False where `is_false`, None where
  neither), or `None` when the tree is outside the vectorizable subset so
  the caller falls back to the per-bar `evaluate_group` loop. **Safety
  contract:** `None` is returned (fall back) for any within-last
  quantifier, cross-interval `Condition`, cross-symbol/cross-interval
  `FieldRef`, non-column builtin field, or operator outside
  `_VEC_SUPPORTED_OPS` — so the supported subset is bit-equivalent to the
  scalar path and the remainder stays on it. Used by the Conditions-mode
  custom-indicator `compute_arr` (compute #2; ~3 orders of magnitude faster
  than the per-bar walk on a 25k-bar series). Pinned by
  `tests/unit/scanner/test_evaluate_group_vec.py`.
  - `_VEC_SUPPORTED_OPS`: `> < >= <= == != between crosses_above
    crosses_below within_pct`. Windowed ops (`is_rising` / `new_high_n` /
    `holding_*` / `inside_bar` / `nr7` / …) fall back.
  - `_VEC_SIMPLE_BUILTINS`: `close open high low volume` (pure column
    reads). Literals → constant column; indicators → the memo array (free).
  - Helpers: `_field_array_vec` (ref → length-n float column, NaN where the
    scalar returns None), `_op_masks_vec` (leaf operator masks),
    `_condition_masks_vec`, `_group_masks_vec` (Kleene AND = `&` of trues /
    `|` of falses; OR = `|` of trues / `&` of falses), `_shift_vec`.
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
at the group level before reduction. A group with no enabled children
returns `None` (same as an empty group).

## Operator dispatch

All 19 operators dispatch through the **registry** in
[`scanner/operators.py`](operators.spec.md) — a `dict[str, OpHandler]`
keyed by operator id, with each handler bundling
`evaluate(cond, ctx, i) -> bool | None` plus an `is_transition` flag
read by the forming-bar guard.

`_evaluate_condition_at` is now a tiny dispatcher:

```python
handler = OPERATOR_EVALUATORS.get(cond.op)
if handler is None:
    LOG.error(...); return None
if handler.is_transition and _in_lookback_walk \
        and index == ctx.current_index and ctx.is_forming:
    return None  # forming-bar skip, ops with is_transition=True only
return handler.evaluate(cond, ctx, index)
```

The named-params schema continues to live in
`model.OPERATOR_PARAM_SCHEMA`; the registry-completeness test in
`tests/scanner/test_operators_registry.py` pins that every op declared
there has a matching `OpHandler`, and vice versa.

`_TRANSITION_OPS` is preserved as a back-compat re-export at the top of
this module, derived from `{op for op, h in OPERATOR_EVALUATORS.items()
if h.is_transition}` — out-of-tree code that imported it from `engine`
keeps working.

The named params per op:

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
