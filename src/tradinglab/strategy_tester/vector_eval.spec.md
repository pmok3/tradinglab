# strategy_tester/vector_eval.py — Spec

Last updated: 2026-09-21

## Purpose
Precomputed, vectorized decision facts for the mechanical strategy
tester. The legacy per-bar loop in `evaluator.py` spends most of its
wall-clock in per-bar Python dispatch: building trigger contexts,
calling the shared entry/exit registries, constructing spec-`Bar`
adapters, and (for the arm-window gate) building datetimes and
re-parsing `"HH:MM"` strings. This module computes every decision fact
that does not depend on path-dependent engine state **once per symbol**
as NumPy arrays, so the hot loop (`evaluator._run_vectorized_loop`)
degrades to array lookups plus the genuinely path-dependent
bookkeeping (engine fills, fire counters, cooldown, edge-trigger
state). Output is bit-identical to the legacy loop.

## Public API
- `VectorEvalPlan` — immutable-except-for-holding-runtime dataclass:
  `n`, `entry_kind`, `entry_is_long`, `entry_trigger_id`,
  `entry_fire: bool[n]`, `entry_is_scanner_alert`,
  `entry_static_gate: bool[n]`, `exits_vectorized: bool`,
  `exit_legs` (enabled exit triggers in evaluation order),
  `exit_static_masks: dict[int, bool[n]]` (MARKET / TIME_OF_DAY /
  INDICATOR legs), plus per-holding runtime `position_view`,
  `exit_targets`, `bar_views` rebuilt by `activate_holding` at each
  position-open transition.
- `build_plan(*, n, bars, interval, entry_strategy, exit_strategy, et_tod_sec, rth_mask, eval_ctx, normalized_conditions) -> VectorEvalPlan | None` — builds the plan, or returns `None` when exact vectorization cannot be proven (see Fallback). Never raises for trigger-model reasons; malformed-but-scalar-silent configs (missing prices, bad `"HH:MM"`) become all-False masks.
- `activate_holding(plan, ctx, o, h, l, c)` — rebuilds per-holding exit runtime at a False→True position transition: clears `ctx.trigger_states`, clears `exit_targets`, rebuilds the `positions.model.Position` view from the synced context, and seeds CHANDELIER states with `is_activation=True`. Mirrors `evaluator._reset_trigger_states_on_activation`.
- `check_exits_vectorized(plan, ctx, i, o, h, l, c) -> tuple[bool, float]` — mirrors `evaluator._check_exits`: iterates `exit_legs` in order, first firing leg wins, returns `(fired, qty_to_close)` with the same `qty_pct` clamp.

## Decision contract
- **Entry fire masks** replicate `entries.spec.should_fire_*` exactly:
  MARKET → all-True; LONG LIMIT `low <= price` / SHORT LIMIT
  `high >= price`; LONG STOP `high >= stop_price` / SHORT STOP
  `low <= stop_price`; STOP_LIMIT requires both touches on the same bar.
  NaN never compares true (NumPy `float64` comparisons match Python
  float semantics). Missing prices → all-False (scalar silent no-fire).
- **INDICATOR / SCANNER_ALERT masks** come from
  `scanner.engine.evaluate_group_vec` (all-bar `is_true` masks;
  unknown-as-not-matched preserved). The condition lookup mirrors the
  dispatch handlers' guards (`normalized_conditions` keyed by
  `trigger.id`, missing scan → all-False).
- **SCANNER_ALERT edge state is NOT precomputed.** The mask carries
  match state; the hot loop updates
  `ctx.scanner_alert_prev_match[trigger.id]` exactly when the scalar
  handler would (all entry gates passed) and fires only on a
  False/unknown → True edge.
- **Entry static gate** = `enabled` AND intraday-only arm-window AND
  `require_market_open`. The arm window compares
  `et_tod_sec = (bars.ts + et_offsets_sec) % 86400` against parsed
  `"HH:MM"` bounds — inclusive both ends, midnight wrap, malformed →
  gate disabled, non-intraday intervals bypass entirely (same
  `is_intraday(interval) if interval else True` rule as `_check_entry`).
  Bar timestamps are whole seconds, so seconds-since-ET-midnight
  comparison is exact.
- **Exit MARKET / TIME_OF_DAY / INDICATOR** are `bool[n]` masks.
  TIME_OF_DAY compares `et_tod_sec >= cutoff` (level-triggered; the
  evaluator de-dupes via position state). MARKET/TIME_OF_DAY keep the
  legacy `compute_qty_at_fire` gate (TIME_OF_DAY swallows malformed
  `qty_pct` into no-fire; MARKET lets it raise — mirroring the two
  scalar handlers).
- **Exit LIMIT / STOP / STOP_LIMIT** resolve the price target from the
  position's average entry price via a line-for-line mirror of
  `exits.dispatch._legacy_resolve_exit_price` (strategy tester runs
  with `legacy_signed_offsets=True`), lazily at the first bar the leg
  is checked (same raise timing as legacy). Touch comparisons mirror
  `_legacy_limit` / `_legacy_stop` (STOP_LIMIT fires like a stop).
- **Exit TRAILING_STOP / CHANDELIER** are inherently sequential, so the
  vectorized loop advances them with the *real*
  `exits.spec.update_trail_state` / `evaluate_trailing_stop` /
  `update_chandelier_state` / `evaluate_chandelier_stop` functions —
  bit-identical by construction. The per-bar UTC `datetime` the legacy
  adapter builds is skipped (the evaluators only read OHLC) via a
  reusable dateless spec-`Bar`. Exception behaviour mirrors the scalar
  handlers (log + no-fire).

## Dtypes
- Bar timestamps `int64`; OHLC `float64`; ET offsets `int64`;
  seconds-since-ET-midnight `int64`; RTH mask `bool_`; every decision
  mask `bool` (contiguous). No pandas reindexing — raw NumPy arrays
  aligned positionally with `bars`.

## Fallback (documented, not silent)
`build_plan` returns `None` — the symbol runs the untouched legacy
per-bar loop — when:
- an entry/exit trigger kind is missing from the shared dispatch
  registry (`_ENTRY_DISPATCH` / `_EXIT_DISPATCH` membership is checked
  first, preserving the typed `UnsupportedTriggerKind` contract);
- any INDICATOR / SCANNER_ALERT tree is outside
  `evaluate_group_vec`'s supported subset (it returns `None` for
  within-last quantifiers, cross-symbol/cross-interval refs,
  unsupported operators);
- the entry `position_already_open_policy` is not BLOCK — mid-holding
  STACK adds would move the average entry price that exit targets and
  stateful-exit position views resolve from. The entry side stays
  vectorized; only exits fall back per-bar.

## Design Decisions
- **One shared orchestration loop** — fills, order timing, position
  state, EOD behaviour, daily counters, stacking, and partial exits are
  path-dependent and stay scalar. Vectorization applies to *decision
  predicates* only.
- **Chandelier/trailing recurrences are not vectorized** — they are
  sequential by nature; reusing the spec functions is exact and keeps
  one source of truth.
- **This module never imports `evaluator`** (no cycle); the evaluator
  imports it. Local mirrors of tiny helpers (`_parse_hhmm`) are
  deliberate.
- **Registry-membership checks, not handler snapshots** — dispatch
  registries are shared dicts; checking `kind in _ENTRY_DISPATCH`
  preserves monkeypatch-based tests that simulate unsupported kinds.

## Invariants
- `build_plan` is pure w.r.t. its inputs (no engine interaction); the
  plan for a symbol is built once.
- `check_exits_vectorized` never fires when `ctx.position_open` is
  False or `ctx.position_qty <= 0`.
- Exit leg evaluation order is preserved; first firing leg wins.

## Testing
- `tests/unit/strategy_tester/test_vectorized_agreement.py` — 66
  old-vs-new exact-agreement tests (see `evaluator.spec.md`).
- `tests/perf/test_strategy_eval_perf.py` — ≥1.5x speedup gate at
  ~4k bars.

## See also
- [evaluator](evaluator.spec.md)
- `entries/dispatch.spec.md`, `exits/dispatch.spec.md`,
  `exits/spec.spec.md`, `scanner/engine.spec.md`
