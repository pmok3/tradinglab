# strategy_tester/vector_eval.py — Spec

Last updated: 2026-09-23

## Purpose
Optional decision facts for the mechanical evaluator's single bar loop.
This module owns no order submission, sizing, counters, activation, EOD
flattening or exit price rules. Dispatch owns the optional trigger kernels.

## Public API
- `VectorEvalPlan`: source `bars`, `entry_gate: bool[n]`, optional dispatch-owned `entry`
  predicate, and `exits` keyed by `(leg_index, trigger_index)`. Slots are
  unambiguous even when different triggers have identical user-authored IDs.
- `position` / `position_key` cache the evaluator's canonical Position
  adapter while side, quantity, average price and entry timestamp are
  unchanged. Scalar fallback invalidates this cache so mutations in custom
  handlers cannot leak across bars.
- `build_plan(*, n, bars, interval, entry_strategy, exit_strategy,
  et_tod_sec, rth_mask, eval_ctx, normalized_conditions)` prepares the gate
  and requests optional kernels from entry and exit dispatch.

## Decision contract
- The entry time gate uses precomputed ET seconds since midnight and RTH
  membership. `core.timezones.parse_hhmm` validates both boundaries;
  malformed or missing bounds disable the arm window. Bounds are inclusive,
  wrap-around windows are supported, and non-intraday intervals bypass
  both arm-window and market-open gates.
- Enabled, BLOCK, fire-cap, cooldown and sizing checks stay in `_check_entry`.
  SCANNER_ALERT match masks update shared dispatch edge state only when
  those checks allow dispatch. Missing scan inputs do not update state.
- Entry MARKET and supported INDICATOR/SCANNER_ALERT trees have masks.
  Price entries deliberately use scalar dispatch.
- Exit INDICATOR masks come from the scanner vector kernel. Legacy price
  exits cache a scalar target/direction in exit dispatch, using the same
  resolver, current-bar touch comparison and quantity helper as scalar.
  No full-history arrays are rebuilt on position changes: repeated BLOCK
  close/re-entry remains constant work per eligible price check.
  Non-BLOCK entry policies keep price exits scalar.
- MARKET/TIME_OF_DAY/stateful exit rules remain scalar. Both modes share
  `_check_exits`, activation, quantity clamping and first-trigger-wins order.
- Unsupported trees and custom/new handlers fall back per trigger, not via
  another orchestration loop. Kernels check current handler identity again
  when consumed. Missing registry entries retain `UnsupportedTriggerKind`
  at the original scalar dispatch point.

## Dtypes and lifetime
OHLC arrays are float64, timestamps and ET seconds are int64, masks are bool
and positionally aligned with bars. Plans are per-symbol and never global.
Time gates and scanner masks are built once; scalar price targets refresh
when the position reference changes. Source bars allow the evaluator to
defer unused tuple/spec-Bar adapters until scalar dispatch, activation or
sizing needs them. No dtype downcast, rounding or tolerance is added.

## Testing
- `tests/unit/strategy_tester/test_vectorized_agreement.py`: exact complete
  SessionResult comparisons against `use_vectorized=False`, including
  duplicate trigger IDs, invalid cutoffs, custom handlers, STACK, partial
  exits, NaN quantities, cancellation, warmup, EOD and scanner fallback.
- `tests/perf/test_strategy_eval_perf.py`: unchanged min-of-7 interleaved
  speedup gate of at least 1.5x on 4,056 bars.

## See also
- `evaluator.spec.md`
- `entries/dispatch.spec.md`, `exits/dispatch.spec.md`
- `core/timezones.spec.md`, `scanner/engine.spec.md`
