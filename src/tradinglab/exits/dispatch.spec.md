# exits.dispatch

Last updated: 2026-09-23

## Purpose

Shared trigger-dispatch registry for exit strategies. Both the live
`ExitEvaluator` and the mechanical strategy-tester evaluator route exit
triggers through this module so adding a new `TriggerKind` is a registry
change instead of two drifting handler chains.

## Public surface

- `ExitTriggerContext` bundles the data a handler may need: position,
  current spec bar, close/intrabar flag, optional mutable
  `TriggerState`, optional `now` timestamp, optional scanner
  evaluation context, optional normalized conditions, and the explicit
  `legacy_signed_offsets` compatibility flag.
- `check_trigger_decision(trigger, ctx) -> Decision` looks up
  `trigger.kind` in `_EXIT_DISPATCH` and returns a no-fire decision for
  unknown kinds.
- `supported_trigger_kinds() -> set[TriggerKind]` exposes the registry
  keys for contract tests.
- `_EXIT_DISPATCH` maps every `TriggerKind` to a handler returning a
  `Decision`.
- `prepare_trigger_mask(trigger, *, bars, eval_ctx, normalized_conditions, cache_prices)`
  optionally prepares a mechanical close-bar `PreparedExit` kernel.
  Its `fires(trigger, index, position)` returns `bool` or `None` for
  scalar fallback. Only canonical INDICATOR and legacy-price
  LIMIT/STOP/STOP_LIMIT handlers have kernels. Identity checks at
  preparation and consumption preserve custom/replaced handlers.
  This API is specifically for the tester's `legacy_signed_offsets=True`
  policy, not the live raw-offset policy.

## Semantics

- PRICE exits (`MARKET`, `LIMIT`, `STOP`, `STOP_LIMIT`) delegate to
  `exits.spec` for the live policy.
- Strategy-tester compatibility is explicit: when
  `ExitTriggerContext.legacy_signed_offsets` is true, `LIMIT`/`STOP`/
  `STOP_LIMIT` resolve `offset_pct` and `offset_dollar` using the
  historical signed-by-side strategy-tester policy. Positive stop
  offsets remain adverse-direction stops for old manifests and tests.
- Stateful exits (`TRAILING_STOP`, `CHANDELIER`) require a caller-owned
  `TriggerState`. Missing state returns no-fire instead of mutating
  module globals.
- `TIME_OF_DAY` evaluates against `ctx.now` when provided, otherwise
  `ctx.bar.date`; missing datetime returns no-fire.
- `INDICATOR` expects the caller to build the appropriate scanner
  `EvaluationContext`. The handler only evaluates the condition and
  returns evidence.
- Price kernels lazily resolve a target using the same
  `_legacy_resolve_exit_price` as scalar dispatch, then apply the shared
  `_legacy_price_touched` comparison to the current bar's high/low only.
  Each check does constant work and allocates no full-history mask, even
  when BLOCK positions close and re-enter at new prices on alternate bars.
  Each plan
  slot owns its own kernel: user-authored trigger IDs are not cache keys.
  Side/average-entry changes invalidate the cached scalar target/direction.
  Non-BLOCK entry policies pass `cache_prices=False` and keep price exits scalar;
  the canonical `compute_qty_at_fire` gate still runs on each check.
  Missing targets and malformed quantities keep scalar evaluation order
  and exception timing. INDICATOR masks use `evaluate_group_vec`;
  unsupported trees fall back to the scalar handler.
- MARKET, TIME_OF_DAY, TRAILING_STOP and CHANDELIER remain scalar, preserving
  their quantity, validation, state, datetime and exception semantics.

## Tests

- `tests/exits/test_dispatch.py` pins registry completeness, the
  strategy-tester alias identity, unknown-kind no-fire behavior,
  dynamic registry visibility, basic market dispatch, and explicit
  legacy signed-offset policy.
- `tests/unit/strategy_tester/test_vectorized_agreement.py` covers duplicate
  IDs, quantity edge cases, STACK repricing, custom and replaced handlers,
  scalar fallback and exact result agreement. High-turnover BLOCK tests
  at increasing bar counts assert changing targets, exact trades and one
  scalar touch comparison per eligible check, rather than a history scan.
