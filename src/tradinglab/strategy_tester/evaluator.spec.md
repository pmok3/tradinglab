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

## PR-1 trigger scope
Wired:
- Entry MARKET, LIMIT, STOP, STOP_LIMIT
- Exit MARKET, LIMIT, STOP, STOP_LIMIT
- `eod_kill_switch` (synthetic flatten on last bar)

`UnsupportedTriggerKind` for:
- Entry INDICATOR, SCANNER_ALERT
- Exit TRAILING_STOP, TIME_OF_DAY, INDICATOR, CHANDELIER

Multi-leg OCO is reduced to first-leg-to-fire in PR 1. Proper OCO semantics ship in PR 2.

## Dependencies
- `backtest.engine.SandboxEngine`, `backtest.session.SessionResult / SessionSpec / ENGINE_VERSION`
- `backtest.bars.from_candles`
- `backtest.orders.Order / Side`
- `backtest.fills.apply_fills` (only for the EOD kill-switch flatten path)
- `entries.model` / `exits.model` enums + dataclasses
- `models.Candle`
- `.model.CostModel`

## Design Decisions
- **Position-state mirror, not duplicate state** — `EvalContext` carries strategy-level flags (fires_total, fires_by_symbol, initial_stop_price) but the actual open-position quantity / avg_cost comes from `engine.portfolio.positions[sym]`. Single source of truth, prevents drift.
- **Decision price = bar `i` close** (resolved before any new orders are submitted). Fill price = bar `i+1` open ± slippage. This matches the live evaluator's "decide at close, fill next open" canonical contract.
- **Sizing capped at starting_cash for FIXED_NOTIONAL** — opinionated; prevents accidental 10x leverage from a misconfigured strategy. `FIXED_QTY` is honored verbatim.
- **Exit checks run before entry checks on the same bar** — an open position must clear before re-entry on the same bar. Matches live evaluator.
- **EOD kill-switch is a final-bar synthetic fill via direct `_apply_fill_with_tracking`** — bypasses the tick loop (the loop is exhausted). Uses last-bar open as the fill price, slippage included.
- **Registry-based dispatch** — new trigger kinds register a handler and immediately work end-to-end. Avoids scattered `if kind == X` blocks.
- **Worker isolation via `UnsupportedTriggerKind`** — distinct from `ValueError` / `RuntimeError` so the runner can map it specifically to a per-symbol error message without abort.

## Invariants
- Returns a valid `SessionResult` for every call (even with zero candles — empty fills + zero equity_curve).
- Never mutates the input `EntryStrategy` / `ExitStrategy` objects.
- Engine `bars_by_symbol` always contains exactly the one symbol under evaluation (per-symbol independent capital).
- `SessionResult.spec.tickers == (symbol,)`.

## Testing
- `tests/unit/strategy_tester/test_evaluator.py` — entry MARKET fires on first bar; LIMIT entry fires when bar.low touches; STOP entry fires when bar.high touches; FIXED_NOTIONAL sizing rounds DOWN; position open blocks re-entry; exit STOP closes on touch; EOD kill-switch flattens at end; unsupported kinds raise `UnsupportedTriggerKind`.
- `tests/smoke/test_smoke_strategy.py::check_st0_kernel_only` — 3 synthetic tickers + MARKET entry + STOP exit, validates `SessionResult` has ≥1 fill and per-symbol JSON parses.

## See also
- [model](model.spec.md), [runner](runner.spec.md)
- `entries/evaluator.spec.md` — live counterpart (Tk-bound; cannot be reused).
- `backtest/engine.spec.md` — kernel.
