# exits.dispatch

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

## Tests

- `tests/exits/test_dispatch.py` pins registry completeness, the
  strategy-tester alias identity, unknown-kind no-fire behavior,
  dynamic registry visibility, basic market dispatch, and explicit
  legacy signed-offset policy.
