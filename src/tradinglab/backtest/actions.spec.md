# backtest/actions.py — Spec

## Purpose
Two engine-output dataclasses recording corporate actions during a
sandbox replay: `CashAdjustment` (cash dividend, special dividend,
spin-off cash) and `QuantityAdjustment` (stock split, reverse split).
Persisted so loaded sessions deterministically reproduce equity-curve
ex-event boundaries even if upstream `EarningsRecord` /
`DividendRecord` data later mutates.

## Public API
- `@dataclass(frozen=True) class CorporateAction(ts, kind, amount=0.0, ratio_num=1, ratio_den=1, source_ref="")` — engine-input record registered via `SandboxEngine.register_corporate_actions`; `kind ∈ {"cash_dividend", "special_dividend", "spinoff_cash", "stock_split"}`.
- `@dataclass(frozen=True) class CashAdjustment(ts, symbol, amount_per_share, quantity, reason, source_ref="")`.
  - `reason ∈ {"cash_dividend", "special_dividend", "spinoff_cash"}`.
- `@dataclass(frozen=True) class QuantityAdjustment(ts, symbol, ratio_num, ratio_den, pre_quantity, reason="stock_split", source_ref="")`.
  - `reason ∈ {"stock_split"}` (reverse splits use the same reason with `ratio_den > ratio_num`).
  - Engine consumers compute the effective ratio as `ratio_num / ratio_den` (see `engine._apply_corporate_actions`).

## Dependencies
None beyond `dataclasses`.

## Design Decisions
- **Engine-output, not ambient event data.** `EarningsRecord` /
  `DividendRecord` (in `tradinglab.events`) are ambient context,
  provider-mutable, NOT persisted in `SessionResult`. The records
  here are facts about what the engine *did* (analogous to `Fill`)
  and ARE persisted.
- **`amount_per_share` AND `quantity` both stored.** Engine already
  credited `amount_per_share * quantity` to cash by emission time —
  row-level facts kept separately for forensic audit.
- **No materialised spin-off child position in v1.** Full mechanic
  would require fetching the child ticker's `BarSeries` and
  registering it mid-session; cash-equivalent matches user's Q10
  ("convert spinoff value to cash credit").
- **Frozen dataclasses** — same posture as `Order` / `Fill`.
- **Fractional shares tolerated** — size is `float` elsewhere;
  reverse-split rounding is render-time only.

## Invariants
- `ratio_num >= 1` and `ratio_den >= 1` (both > 0).
- `quantity != 0` when emitting.
- `ts` is the engine bar timestamp in UTC epoch seconds. `replay_events.py` converts event-provider millisecond timestamps onto the engine timeline before registration.

## Data Flow
1. `SandboxController.start_session` fetches per-symbol `EventBundle`
   and pre-computes per-symbol pending `(ts, reason, payload)` tuples
   sorted ascending.
2. `SandboxEngine.tick()` between MAE/MFE roll (phase 2) and MTM
   (phase 3) drains all tuples with `ts == clock.now_ts` for each
   open-position symbol, applies them, appends records to
   `SessionResult.cash_adjustments` / `quantity_adjustments`.
3. `Portfolio.cash` mutated in place for cash adjustments;
   `Position.quantity` rescaled in place for splits.
