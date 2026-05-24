# strategy_tester/evaluator.py — Spec

## Purpose
Headless trigger-evaluation kernel for the Strategy Tester. The live `EntryEvaluator` / `ExitEvaluator` are Tk-thread-guarded (they touch `PaperBrokerEngine`, journal, indicator-manager, audit log). The mechanical tester ships a **parallel** implementation that consumes the same JSON-compatible `EntryStrategy` / `ExitStrategy` dataclasses and emits `Order`s directly into a fresh per-symbol `SandboxEngine`.

## Public API
- `evaluate_symbol(*, symbol, candles, interval, entry_strategy, exit_strategy, starting_cash, cost_model, deck_seed=0) -> SessionResult` — primary entry point. Side-effect-free apart from creating an engine in-process. Returns a standard `SessionResult` that the existing `performance.py` builders + Sandbox post-mortem renderer consume verbatim.
- `EvalContext` — dataclass; mutable per-symbol state. Internal but exposed for test fixtures.
- `class UnsupportedTriggerKind(NotImplementedError)` — typed signal for trigger kinds the headless path doesn't yet handle. Runner catches and marks the symbol as `error` without aborting the rest of the Run.
- `_ENTRY_HANDLERS` / `_EXIT_HANDLERS` — registry dicts mapping `TriggerKind → handler`. Future kinds light up the GUI's "Supported" list by adding a handler.

## Decision contract
For each bar `i`:
1. `engine.tick()` advances clock to `i`, fills any pending orders at `i.open`, updates MAE/MFE on `i.H/L`, marks-to-market at `i.close`.
2. `_sync_position_state_from_engine` mirrors engine state into the EvalContext.
3. If a position is open, check every enabled exit-leg trigger against `i.O/H/L/C`. First leg to fire wins; `submit_order` is queued and will fill at `i+1.open`.
4. Otherwise check the entry trigger; size via `_compute_quantity(decision_price=i.close)`; submit if `qty > 0`.

## Trigger scope (all wired)
Wired:
- **Entry**: MARKET, LIMIT, STOP, STOP_LIMIT, INDICATOR, **SCANNER_ALERT**
- **Exit**: MARKET, LIMIT, STOP, STOP_LIMIT, INDICATOR, **TRAILING_STOP**, **TIME_OF_DAY**, **CHANDELIER**
- `eod_kill_switch` (synthetic flatten on last bar)

Every `TriggerKind` enum value has a handler. `UnsupportedTriggerKind` is now
the defensive "missing-handler" fallback only — it should never fire in
practice unless a new kind is added to the schema before its handler.

### TRAILING_STOP / TIME_OF_DAY / CHANDELIER exits
All three delegate to the **pure-function evaluators in `exits/spec.py`** —
the same source-of-truth the live `ExitEvaluator` uses. The strategy tester
ships thin adapter handlers (`_exit_trailing_stop`, `_exit_time_of_day`,
`_exit_chandelier`) that:
1. Build a `positions.model.Position` from `EvalContext.position_*` via
   `_ctx_to_position(ctx)`.
2. Build an `exits.spec.Bar` from the current `_BarTuple` via
   `_bar_to_specbar(bar, ts)` (tz-aware UTC datetime from epoch seconds).
3. Look up / create a `TriggerState` keyed by `trigger.id` in
   `EvalContext.trigger_states`.
4. For TRAILING_STOP: call `update_trail_state(state, trigger, position, bar)`
   then `evaluate_trailing_stop(...)`. ATR variant short-circuits when
   `atr_value` is unavailable (no `BarsRegistry`).
5. For TIME_OF_DAY: stateless; calls `evaluate_time_of_day(trigger, position,
   bar, now=datetime.fromtimestamp(ts, tz=timezone.utc))`.
6. For CHANDELIER: calls `update_chandelier_state(...)` then
   `evaluate_chandelier_stop(...)`. Activation-bar (entry bar) is seeded with
   `is_activation=True` so the rolling-high/low window starts from the entry
   bar's H/L; subsequent bars advance with `is_activation=False`. ATR
   warm-up requires `atr_period` non-activation bars.

### Per-trigger state reset on position activation
`EvalContext` carries:
- `trigger_states: dict[str, TriggerState]` — per-trigger HWM / chandelier
  window / ATR state.
- `prev_position_open: bool` — previous bar's open/closed state.
- `scanner_alert_prev_match: dict[str, bool]` — per-SCANNER_ALERT-trigger
  previous-bar match state.

`evaluate_symbol` detects position-open transitions (False → True between
ticks) and calls `_reset_trigger_states_on_activation(ctx, exit_strategy,
bar, ts)`:
- Clears `trigger_states` entirely (fresh state for the new position).
- Seeds CHANDELIER state by calling `update_chandelier_state(state, trigger,
  position, bar, is_activation=True)` for every enabled CHANDELIER leg.

### SCANNER_ALERT entries
`_entry_scanner_alert(...)` loads the saved `ScanDefinition` once via
`scanner.storage.load(scanner_id)` and normalises its `.root` Group
intervals to the test's outer interval (same `_normalize_intervals` path as
INDICATOR triggers; same multi-interval limitation). It then evaluates the
root group per bar and fires on **edge transitions** (False/None → True):
- **Bar 0**: observes the current match state into
  `scanner_alert_prev_match[trigger.id]` and returns no-fire. This avoids
  the backtest trap where every already-matching symbol fires on day 1.
- **Bars 1+**: fires when `prev == False AND current == True`; updates the
  cache regardless. Missing scanner ID (FileNotFoundError) is logged once
  and treated as silent no-fire for the rest of the run.

This diverges from the live `ScanRunner` semantic (which treats first match
after empty history as new) — deliberately, to keep backtest results sane.
Documented in the handler docstring.

INDICATOR triggers delegate to `scanner.engine.evaluate_group` against a
per-symbol `EvaluationContext` built once outside the bar loop. The
context's `current_index` is mutated each bar so the `IndicatorMemo`
cache stays warm (O(n), not O(n²)) across the entire symbol scan. The
strategy's per-trigger `interval` falls back to the outer `interval`
passed to `evaluate_symbol`; true cross-interval evaluation requires a
`BarsRegistry` and is deferred (the handler swallows
`NotImplementedError` and treats it as "no fire"). Indicator-side
exceptions are logged via `logging` and treated as "no fire" so a
broken indicator never aborts an entire Run.

**Single-interval normalization.** Saved scanner conditions carry
per-`Condition`/`FieldRef` ``interval`` slots that default to
``"5m"`` (per `scanner.model`). When the strategy tester runs at a
different interval (e.g. ``"1d"``) with no `BarsRegistry`, the
scanner's cross-interval gate would silently return ``None`` for
every leaf — producing zero fires across the entire universe. To
prevent that, `evaluate_symbol` calls `_build_normalized_conditions`
once per symbol to deep-clone every INDICATOR trigger's condition
tree with all internal intervals forced to match the test's outer
interval. The normalized cache is keyed by ``trigger.id`` and
threaded to the indicator handlers, which look up the rewritten tree
instead of the original. The input strategy objects are never
mutated.

Multi-leg OCO is reduced to first-leg-to-fire in PR 1. Proper OCO semantics ship in PR 2.

## Dependencies
- `backtest.engine.SandboxEngine`, `backtest.session.SessionResult / SessionSpec / ENGINE_VERSION`
- `backtest.bars.from_candles`
- `backtest.orders.Order / Side`
- `backtest.fills.apply_fills` (only for the EOD kill-switch flatten path)
- `entries.model` / `exits.model` enums + dataclasses
- `models.Candle`
- `.model.CostModel`
- `scanner.engine.{make_context, evaluate_group, EvaluationContext}` (INDICATOR triggers)
- `scanner.storage.load` + `scanner.model.ScanDefinition` (SCANNER_ALERT entries)
- `exits.spec.{Bar, TriggerState, update_trail_state, evaluate_trailing_stop, evaluate_time_of_day, update_chandelier_state, evaluate_chandelier_stop}` — pure-function evaluators reused for TRAILING_STOP / TIME_OF_DAY / CHANDELIER (no Tk dependency)
- `positions.model.Position` — adapter dataclass produced by `_ctx_to_position` for spec.py evaluators

## Design Decisions
- **Position-state mirror, not duplicate state** — `EvalContext` carries strategy-level flags (fires_total, fires_by_symbol, initial_stop_price) but the actual open-position quantity / avg_cost comes from `engine.portfolio.positions[sym]`. Single source of truth, prevents drift.
- **Decision price = bar `i` close** (resolved before any new orders are submitted). Fill price = bar `i+1` open ± slippage. This matches the live evaluator's "decide at close, fill next open" canonical contract.
- **Sizing capped at starting_cash for FIXED_NOTIONAL** — opinionated; prevents accidental 10x leverage from a misconfigured strategy. `FIXED_QTY` is honored verbatim.
- **Exit checks run before entry checks on the same bar** — an open position must clear before re-entry on the same bar. Matches live evaluator.
- **EOD kill-switch is a final-bar synthetic fill via direct `_apply_fill_with_tracking`** — bypasses the tick loop (the loop is exhausted). Uses last-bar open as the fill price, slippage included.
- **Registry-based dispatch** — new trigger kinds register a handler and immediately work end-to-end. Avoids scattered `if kind == X` blocks.
- **Worker isolation via `UnsupportedTriggerKind`** — distinct from `ValueError` / `RuntimeError` so the runner can map it specifically to a per-symbol error message without abort.
- **Reuse `exits/spec.py` pure functions for stateful exits** — TRAILING_STOP / TIME_OF_DAY / CHANDELIER share byte-identical math with the live evaluator. No re-implementation drift. Strategy tester ships only thin adapters (~30-40 lines each) that translate `EvalContext` ↔ `Position` / `Bar` dataclasses and thread `TriggerState` keyed by `trigger.id`.
- **Position-open transition triggers state reset** — Each new position gets fresh per-trigger state (HWM, chandelier window, ATR). Detection via `prev_position_open` flag. CHANDELIER triggers additionally get an activation-bar seed call so their rolling-extremum window starts from the entry bar.
- **SCANNER_ALERT bar-0 observes only** — deliberate divergence from live `ScanRunner` semantics. A live scanner fires on the *first* match it ever sees (after empty history); in a backtest that would fire every already-matching symbol on bar 0, which is meaningless. Bar 0 just snapshots match state; first real fire candidate is bar 1.

## Invariants
- Returns a valid `SessionResult` for every call (even with zero candles — empty fills + zero equity_curve).
- Never mutates the input `EntryStrategy` / `ExitStrategy` objects.
- Engine `bars_by_symbol` always contains exactly the one symbol under evaluation (per-symbol independent capital).
- `SessionResult.spec.tickers == (symbol,)`.

## Testing
- `tests/unit/strategy_tester/test_evaluator.py` — entry MARKET fires on first bar; LIMIT entry fires when bar.low touches; STOP entry fires when bar.high touches; FIXED_NOTIONAL sizing rounds DOWN; position open blocks re-entry; exit STOP closes on touch; EOD kill-switch flattens at end; defensive `UnsupportedTriggerKind` fallback (via registry-pop test); INDICATOR entry fires when `close > threshold` becomes true; INDICATOR entry never fires for an unreachable threshold; INDICATOR with `condition=None` silently doesn't fire; INDICATOR exit closes the position when its condition triggers; **TRAILING_STOP percent fires on retrace, no fire on uninterrupted uptrend, dollar unit honoured**; **TIME_OF_DAY fires at/after cutoff, no fire before, malformed string = no fire**; **CHANDELIER fires after ATR warm-up, no fire during warm-up window**; **SCANNER_ALERT fires on edge False → True, no fire when already matching, missing scanner ID = silent no-fire.**
- `tests/smoke/test_smoke_strategy.py::check_st0_kernel_only` — 3 synthetic tickers + MARKET entry + STOP exit, validates `SessionResult` has ≥1 fill and per-symbol JSON parses.

## See also
- [model](model.spec.md), [runner](runner.spec.md)
- `entries/evaluator.spec.md` — live counterpart (Tk-bound; cannot be reused).
- `backtest/engine.spec.md` — kernel.
