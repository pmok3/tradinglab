# `scanner/operators.py` — operator dispatch registry

## Purpose

Single source of truth for the **behavioural** half of the scanner's
19 operators. The **schema** half (per-op named params + kinds) stays
in [`scanner/model.py`](model.spec.md) at `OPERATOR_PARAM_SCHEMA`; this
module supplies the matching per-op evaluators and a forming-bar-guard
flag.

Collapses what used to be a ~120-line `if op == ...` chain inside
[`scanner/engine.py`](engine.spec.md) `_evaluate_condition_at` into a
single dict lookup. Mirrors the entry-dispatch pattern shipped for
[`entries/dispatch.py`](../entries/dispatch.spec.md).

## Public API

- `OpHandler` — frozen dataclass bundling `evaluate` +
  `is_transition: bool`.
- `OPERATOR_EVALUATORS: dict[str, OpHandler]` — 19 entries, keyed by
  operator-id string (matches `OPERATOR_PARAM_SCHEMA` exactly).
- `TRANSITION_OPS: frozenset[str]` — derived view of
  `{op for op, h in OPERATOR_EVALUATORS.items() if h.is_transition}`.
  Re-exported by `engine.py` as `_TRANSITION_OPS` for back-compat.
- `register_op(name, handler)` — extension hook for tests + plugins.
  Mutates `OPERATOR_EVALUATORS` in place; tests should save/restore the
  prior handler in `try/finally`.

## Evaluator contract

Every `OpHandler.evaluate` has signature

```python
(cond: Condition, ctx: EvaluationContext, i: int) -> bool | None
```

with **Kleene tri-valued semantics**:

- `True` / `False` — predicate decided at bar `i`.
- `None` — *indeterminate*: any operand is `None` (OOB, NaN, missing
  indicator, missing dependency-symbol bar), or the operator needs
  more history than `i` provides (e.g. `crosses_above` with
  `lookback=1` at `i == 0`).

Handlers never raise on malformed-but-typed input — they return `None`
and let the group combinator propagate it.

## Forming-bar guard

Transition ops (`crosses_above`, `crosses_below`) declare
`is_transition=True`. Inside a look-back walk, on the forming bar of
the active context, the central guard in
`engine._evaluate_condition_at` returns `None` *before* the handler
runs:

```python
if handler.is_transition and _in_lookback_walk \
        and index == ctx.current_index and ctx.is_forming:
    return None
```

This implements the trader-spec'd "transitions on closed bars only"
rule. Comparison operators stay live regardless. Per-op handlers MUST
NOT re-implement the guard themselves — it's centralised.

## Circular-import avoidance

Per-op evaluators need `engine.evaluate_field_at` and
`engine._is_nan_like`. To avoid a cycle (engine imports operators at
top of file), this module exposes two module-level slots:

```python
_evaluate_field_at: Callable[..., float | None] | None = None
_is_nan_like:        Callable[[Any], bool] | None = None
```

which `engine.py` wires at the bottom of its module load:

```python
_operators._evaluate_field_at = evaluate_field_at
_operators._is_nan_like = _is_nan_like
```

Handlers call the small `_ef` / `_nanlike` shims so the wiring is a
single point of indirection. A `test_late_binding_wired_by_engine`
regression test asserts the slots are populated post-import; if engine
ever forgets to wire them, every handler would crash with
`TypeError: 'NoneType' object is not callable` on first use.

## Registered handlers

| op id            | handler             | `is_transition` |
| ---------------- | ------------------- | --------------- |
| `>`              | `_eval_gt`          | False           |
| `<`              | `_eval_lt`          | False           |
| `>=`             | `_eval_ge`          | False           |
| `<=`             | `_eval_le`          | False           |
| `==`             | `_eval_eq`          | False           |
| `!=`             | `_eval_ne`          | False           |
| `between`        | `_eval_between`     | False           |
| `crosses_above`  | `_eval_crosses_above` | **True**      |
| `crosses_below`  | `_eval_crosses_below` | **True**      |
| `is_rising`      | `_eval_is_rising`   | False           |
| `is_falling`     | `_eval_is_falling`  | False           |
| `within_pct`     | `_eval_within_pct`  | False           |
| `new_high_n_bars`| `_eval_new_high_n`  | False           |
| `new_low_n_bars` | `_eval_new_low_n`   | False           |
| `holding_above`  | `_eval_holding_above` | False         |
| `holding_below`  | `_eval_holding_below` | False         |
| `inside_bar`     | `_eval_inside_bar`  | False           |
| `outside_bar`    | `_eval_outside_bar` | False           |
| `nr7`            | `_eval_nr7`         | False           |

## Adding a new operator

1. Add `OP_FOO = "foo"` and its schema entry to `model.py`
   (`OPERATOR_PARAM_SCHEMA`).
2. Write `_eval_foo(cond, ctx, i) -> bool | None` here.
3. Register `OPERATOR_EVALUATORS[OP_FOO] = OpHandler(_eval_foo)`
   (set `is_transition=True` only if the op participates in the
   forming-bar guard).
4. The registry-completeness test in
   `tests/scanner/test_operators_registry.py` will start failing
   until the schema-side entry is also added; that's the
   wire-it-through-both-places forcing function.

## Tests

`tests/scanner/test_operators_registry.py` (35 tests):

- Registry keys ↔ schema keys are equal sets.
- Handler count matches schema count (19).
- Every handler is callable + carries the right metadata types.
- `TRANSITION_OPS` equals `{crosses_above, crosses_below}`; no other
  op is flagged.
- `register_op` round-trips cleanly.
- Forming-bar guard fires only when ALL conditions hold
  (transition op + `_in_lookback_walk=True` + `index ==
  current_index` + `is_forming=True`); each component pinned in
  isolation.
- Per-op behavioural parity for `gt`, `lt`, `between`,
  `crosses_above`, `inside_bar` (selected because they cover the
  three operand shapes: scalar/scalar, scalar/two-bounds, and
  bars-only).
- Unknown-op contract: logs `ERROR` + returns `None`, never raises.
- Late-binding wiring assertion.

## Performance

The dispatch lookup is a single Python dict `get` per
`_evaluate_condition_at` call. On the previous if/elif chain, the
worst-case op (`nr7`, last branch) paid 11 string comparisons; the
common case (`gt`, first branch) paid 1 set membership + 1 string
compare. The dict-get is comparable to the common-case branch and
significantly cheaper than the worst-case branch — the registry is
not on a hot loop that would benefit from a `__slots__`-based
optimisation, but it's not a regression either.
